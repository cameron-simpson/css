#!/usr/bin/python
#
# Some Queue subclasses and ducktypes.
#       - Cameron Simpson <cs@cskk.id.au>
#

''' Queue-like items: iterable queues, channels, etc.
'''

from contextlib import contextmanager
from functools import partial
from queue import Queue, PriorityQueue, Empty as Queue_Empty
import sys
from threading import Timer, Lock, RLock, Thread
import time
from typing import Any, Callable, Iterable

from typeguard import typechecked

from cs.lex import r
import cs.logutils
from cs.logutils import exception, warning, debug
from cs.obj import Sentinel
from cs.pfx import Pfx, PfxCallInfo
from cs.resources import (
    MultiOpenMixin,
    RunState,
    RunStateMixin,
    uses_runstate,
    not_closed,
    ClosedError,
)
from cs.result import CancellationError
from cs.seq import seq, unrepeated

__version__ = '20250426-post'

DISTINFO = {
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.lex',
        'cs.logutils',
        'cs.obj',
        'cs.pfx',
        'cs.resources',
        'cs.result',
        'cs.seq',
        'typeguard',
    ],
}

class QueueIterator(Iterable[Any]):
  ''' A `QueueIterator` is a wrapper for a `Queue`like object
      which presents an iterator interface to collect items.
      It does not offer the `.get` or `.get_nowait` methods.
      It does present a `.close` method.
  '''

  def __init__(self, q, name=None):
    if name is None:
      name = f'{self.__class__.__name__}-{seq()}'
    self.q = q
    self.name = name
    self.sentinel = Sentinel(
        "%s:%s:SENTINEL" % (self.__class__.__name__, name)
    )
    # count of non-sentinel items
    self._item_count = 0
    self.closed = False
    self.__lock = Lock()

  def __str__(self):
    return f'{self.__class__.__name__}({self.name!r},q={self.q})'

  def __repr__(self):
    return str(self)

  def close(self):
    with self.__lock:
      if not self.closed:
        self.q.put(self.sentinel)
        self.closed = True

  @not_closed
  def put(self, item, *args, **kw):
    ''' Put `item` onto the queue.
        Warn if the queue is closed.
        Raises `ValueError` if `item` is the sentinel.
    '''
    with self.__lock:
      if self.closed:
        with PfxCallInfo():
          warning("%r.put: all closed: item=%s", self, item)
        raise ClosedError("QueueIterator closed")
      if item is self.sentinel:
        raise ValueError("put(sentinel)")
      self.q.put(item, *args, **kw)
      self._item_count += 1

  def __iter__(self):
    ''' Iterable interface for the queue.
    '''
    return self

  def __next__(self):
    ''' Return the next item from the queue.
        If the queue is closed, raise `StopIteration`.
    '''
    q = self.q
    try:
      item = q.get()
    except Queue_Empty as e:
      warning("%s: queue.Empty: %s", self, e)
      q.put(self.sentinel)
      # pylint: disable=raise-missing-from
      raise StopIteration(f'{self}.get: queue.Empty: {e}') from e
    if item is self.sentinel:
      # sentinel consumed (clients won't see it, so we must)
      q.task_done()
      # put the sentinel back for other consumers
      q.put(self.sentinel)
      raise StopIteration(f'{self}.get: SENTINEL')
    with self.__lock:
      self._item_count -= 1
    return item

  next = __next__

  def empty(self):
    ''' Test if the queue is empty.
    '''
    # testing the count because the "close" sentinel makes the underlying queue not empty
    return self._item_count == 0

  def task_done(self):
    ''' Report that an item has been processed.
    '''
    self.q.task_done()

  def join(self):
    ''' Wait for the queue items to complete.
    '''
    self.q.join()

  def next_batch(self, batch_size=1024, block_once=False):
    ''' Obtain a batch of immediately available items from the queue.
        Up to `batch_size` items will be obtained, default 1024.
        Return a list of the items.
        If the queue is empty an empty list is returned.
        If the queue is not empty, continue collecting items until
        the queue is empty or the batch size is reached.
        If `block_once` is true, wait for the first item;
        this mode never returns an empty list except at the end of the iterator.
    '''
    batch = []
    try:
      if block_once:
        batch.append(next(self))
      while len(batch) < batch_size and not self.empty():
        batch.append(next(self))
    except StopIteration:
      pass
    return batch

  def iter_batch(self, batch_size=1024):
    ''' A generator which yields batches of items from the queue.
        The default `batch_size` is `1024`.
    '''
    while True:
      batch = self.next_batch(batch_size=batch_size, block_once=True)
      if not batch:
        return
      yield batch

def IterableQueue(capacity=0, name=None):
  ''' Factory to create an iterable queue.
      Note that the returned queue is already open
      and needs a close.
  '''
  return QueueIterator(Queue(capacity), name=name)

def IterablePriorityQueue(capacity=0, name=None):
  ''' Factory to create an iterable `PriorityQueue`.
  '''
  return QueueIterator(PriorityQueue(capacity), name=name)

def WorkerQueue(worker, capacity=0, name=None, args=(), kwargs=None):
  ''' Create an `IterableQueue` and start a worker `Thread` to consume it.
      Return a `(queue,Thread)` 2-tuple.
      The caller must close the queue.

      Parameters:
      * `worker`: the function to consume the queue
      * `capacity`: optional, passed to `IterableQueue`
      * `name`: optional, passed to `IterableQueue` and used in the `Thread` name
      * `args`: optional additional positional arguments for `worker` after the queue
      * `kwargs`: optional keyword arguments for the worker
  '''
  if name is None:
    name = worker.__name__
  q = IterableQueue(capacity=capacity, name=name)
  try:
    T = Thread(
        target=worker,
        args=[q, *args],
        kwargs=kwargs,
        name=f'{name} WorkerQueue'
    )
    T.start()
  except Exception:
    q.close()
    raise
  return q, T

class Channel(object):
  ''' A zero-storage data passage.
      Unlike a `Queue`, `put(item)` blocks waiting for the matching `get()`.
  '''

  # pylint: disable=consider-using-with
  def __init__(self):
    self.__readable = Lock()
    self.__readable.acquire()
    self.__writable = Lock()
    self.__writable.acquire()
    self.closed = False

  # pylint: disable=consider-using-with
  def __str__(self):
    if self.__readable.acquire(False):
      if self.__writable.acquire(False):
        state = "ERROR(readable and writable)"
        self.__writable.release()
      else:
        state = "put just happened, get imminent"
      self.__readable.release()
    else:
      if self.__writable.acquire(False):
        state = "idle"
        self.__writable.release()
      else:
        state = "get blocked waiting for put"
    return "%s[%s]" % (type(self).__name__, state)

  def __call__(self, *a):
    ''' Call the `Channel`.
        With no arguments, do a `.get()`.
        With an argument, do a `.put()`.
    '''
    if a:
      return self.put(*a)
    return self.get()

  def __iter__(self):
    ''' A `Channel` is iterable.
    '''
    return self

  def __next__(self):
    ''' `next(Channel)` calls `Channel.get()`.
    '''
    if self.closed:
      raise StopIteration
    return self.get()

  # pylint: disable=consider-using-with
  @not_closed
  def get(self):
    ''' Read a value from the `Channel`.
        Blocks until someone `put()`s to the `Channel`.
    '''
    # allow a writer to proceed
    self.__writable.release()
    # await a writer
    self.__readable.acquire()
    value = self._value
    delattr(self, '_value')
    return value

  # pylint: disable=attribute-defined-outside-init,consider-using-with
  @not_closed
  def put(self, value):
    ''' Write a value to the `Channel`.
        Blocks until a corresponding `get()` occurs.
    '''
    # block until there is a matching .get()
    self.__writable.acquire()
    self._value = value
    # allow .get() to proceed
    self.__readable.release()

  def close(self):
    ''' Close the `Channel`, preventing further `put()`s.
    '''
    if self.closed:
      warning("%s: .close() of closed Channel" % (self,))
    else:
      self.closed = True

class PushQueue(MultiOpenMixin, RunStateMixin):
  ''' A puttable object which looks like an iterable `Queue`.

      In this base class,
      calling `.put(item)` calls `functor` supplied at initialisation
      to trigger a function on data arrival
      whose iterable of results are put onto the output queue.

      As an example, the `cs.pipeline.Pipeline` class
      uses subclasses of `PushQueue` for each pipeline stage,
      overriding the `.put(item)` method
      to mediate the call of `functor` through `cs.later.Later`
      as resource controlled concurrency.
  '''

  @uses_runstate
  @typechecked
  def __init__(
      self,
      name: str,
      functor: Callable[[Any], Iterable],
      outQ,
      runstate: RunState,
  ):
    ''' Initialise the `PushQueue` with the callable `functor`
        and the output queue `outQ`.

        Parameters:
        * `functor` is a one-to-many function which accepts a single
          item of input and returns an iterable of outputs; it may be a
          generator. These outputs are passed to `outQ.put` individually as
          received.
        * `outQ` is a `MultiOpenMixin` which accepts via its `.put()` method.
    '''
    if name is None:
      name = "%s%d-%s" % (self.__class__.__name__, seq(), functor)
    self.name = name
    self._lock = RLock()
    self.functor = functor
    self.outQ = outQ

  def __str__(self):
    return "%s:%s" % (type(self).__name__, self.name)

  def __repr__(self):
    return "<%s outQ=%s>" % (self, self.outQ)

  @contextmanager
  def startup_shutdown(self):
    ''' Open/close the output queue.
    '''
    with self.outQ:
      yield

  @not_closed
  def put(self, item):
    ''' Receive a new `item`, put the results of `functor(item)` onto `self.outQ`.

        Subclasses might override this method, for example to process
        the result of `functor` differently, or to queue the call
        to `functor(item)` via some taks system.
    '''
    runstate = self.runstate
    if runstate.cancelled:
      raise CancellationError(f'{runstate}.cancelled')
    outQ = self.outQ
    functor = self.functor
    with outQ:
      for computed in functor(item):
        if runstate.cancelled:
          raise CancellationError(f'{runstate}.cancelled')
        outQ.put(computed)

class NullQueue(MultiOpenMixin):
  ''' A queue-like object that discards its inputs.
      Calls to `.get()` raise `queue.Empty`.
  '''

  def __init__(self, blocking=False, name=None):
    ''' Initialise the `NullQueue`.

        Parameters:
        * `blocking`: optional; if true, calls to `.get()` block until
          `.shutdown()`; default: `False`.
        * `name`: optional name for this `NullQueue`.
    '''
    if name is None:
      name = "%s%d" % (self.__class__.__name__, seq())
    self.name = name
    self._lock = RLock()
    MultiOpenMixin.__init__(self)
    self.blocking = blocking

  def __str__(self):
    return "NullQueue:%s" % (self.name,)

  def __repr__(self):
    return "<%s blocking=%s>" % (self, self.blocking)

  def put(self, item):
    ''' Put a value onto the queue; it is discarded.
    '''

  def get(self):
    ''' Get the next value. Always raises `queue.Empty`.
        If `.blocking,` delay until `.shutdown()`.
    '''
    if self.blocking:
      self.join()
    raise Queue_Empty

  def startup(self):
    ''' Start the queue.
    '''

  def shutdown(self):
    ''' Shut down the queue.
    '''

  def __iter__(self):
    return self

  def __next__(self):
    try:
      return self.get()
    except Queue_Empty:
      raise StopIteration  # pylint: disable=raise-missing-from

  next = __next__

NullQ = NullQueue(name='NullQ')

class TimerQueue(object):
  ''' Class to run a lot of "in the future" jobs without using a bazillion
      Timer threads.
  '''

  def __init__(self, name=None):
    if name is None:
      name = 'TimerQueue-%d' % (seq(),)
    self.name = name
    self.Q = PriorityQueue()  # queue of waiting jobs
    self.pending = None  # or (Timer, when, func)
    self.closed = False
    self._lock = Lock()
    self.mainRunning = False
    self.mainThread = Thread(target=self._main)
    self.mainThread.start()

  def __str__(self):
    return self.name

  def close(self, cancel=False):
    ''' Close the `TimerQueue`. This forbids further job submissions.
        If `cancel` is supplied and true, cancel all pending jobs.
        Note: it is still necessary to call `TimerQueue.join()` to
        wait for all pending jobs.
    '''
    self.closed = True
    if self.Q.empty():
      # dummy entry to wake up the main loop
      self.Q.put((None, None, None))
    if cancel:
      self._cancel()

  def _cancel(self):
    with self._lock:
      if self.pending:
        T, Twhen, Tfunc = self.pending
        self.pending[2] = None
        self.pending = None
        T.cancel()
      else:
        Twhen, Tfunc = None, None
    return Twhen, Tfunc

  def add(self, when, func):
    ''' Queue a new job to be called at 'when'.
        'func' is the job function, typically made with `functools.partial`.
    '''
    assert not self.closed, "add() on closed TimerQueue"
    self.Q.put((when, seq(), func))

  def join(self):
    ''' Wait for the main loop thread to finish.
    '''
    assert self.mainThread is not None, "no main thread to join"
    self.mainThread.join()

  # pylint: disable=too-many-statements
  def _main(self):
    ''' The main loop.

        Pull requests off the queue; they will come off in time order,
        so we always get the most urgent item.
        If we're already delayed waiting for a previous request,
        halt that request's timer and compare it with the new job; push the
        later request back onto the queue and proceed with the more urgent
        one.
        If it should run now, run it.
        Otherwise start a `Timer` to run it later.
        The loop continues processing items until the `TimerQueue` is closed.
    '''
    with Pfx("TimerQueue._main()"):
      assert not self.mainRunning, "main loop already active"
      self.mainRunning = True
      while not self.closed:
        when, n, func = self.Q.get()
        debug("got when=%s, n=%s, func=%s", when, n, func)
        if when is None:
          # it should be the dummy item
          assert self.closed
          assert self.Q.empty()
          break
        with self._lock:
          if self.pending:
            # Cancel the pending Timer
            # and choose between the new job and the job the Timer served.
            # Requeue the lesser job and do or delay-via-Timer the more
            # urgent one.
            T, Twhen, Tfunc = self.pending
            self.pending[2] = None  # prevent the function from running if racy
            T.cancel()
            self.pending = None  # nothing pending now
            T = None  # let go of the cancelled timer
            if when < Twhen:
              # push the pending function back onto the queue, but ahead of
              # later-queued funcs with the same timestamp
              requeue = (Twhen, 0, Tfunc)
            else:
              # push the dequeued function back - we prefer the pending one
              requeue = (when, n, func)
              when = Twhen
              func = Tfunc
            self.Q.put(requeue)
          # post: self.pending is None and the Timer is cancelled
          assert self.pending is None

        now = time.time()
        delay = when - now
        if delay <= 0:
          # function due now - run it
          try:
            retval = func()
          except Exception as e:  # pylint: disable=broad-except
            exception("func %s threw exception: %s", func, e)
          else:
            debug("func %s returns %s", func, retval)
        else:
          # function due later - run it from a Timer
          def doit(self):
            # pull off our pending task and untick it
            Tfunc = None
            with self._lock:
              if self.pending:
                T, Twhen, Tfunc = self.pending
              self.pending = None
            # run it if we haven't been told not to
            if Tfunc:
              try:
                retval = Tfunc()
              except Exception as e:  # pylint: disable=broad-except
                exception("func %s threw exception: %s", Tfunc, e)
              else:
                debug("func %s returns %s", Tfunc, retval)

          with self._lock:
            T = Timer(delay, partial(doit, self))
            self.pending = [T, when, func]
            T.start()
      self.mainRunning = False

class ListQueue:
  ''' A simple iterable queue based on a `list`.
  '''

  def __init__(self, queued=None, *, unique=None):
    ''' Initialise the queue.

        Parameters:
        * `queued` is an optional iterable of initial items for the queue
        * `unique`: optional signature function, default `None`

        The `unique` parameter provides iteration via the
        `cs.seq.unrepeated` iterator filter which yields only items
        not seen earlier in the iteration.
        If `unique` is `None` or `False` iteration iterates
        over the queue items directly.
        If `unique` is `True`, iteration uses the default mode
        where items are compared for equality.
        Otherwise `unique` may be a callable which produces a
        value to use to detect repetitions, used as the `cs.seq.unrepeated`
        `signature` parameter.

        Example:

            >>> items = [1, 2, 3, 1, 2, 5]
            >>> list(ListQueue(items))
            [1, 2, 3, 1, 2, 5]
            >>> list(ListQueue(items, unique=True))
            [1, 2, 3, 5]
    '''
    self.queued = []
    if queued is not None:
      # catch a common mistake
      assert not isinstance(queued, str)
      self.queued.extend(queued)
    if unique is None or unique is False:
      unrepeated_signature = None
    elif unique is True:
      unrepeated_signature = lambda item: item
    elif callable(unique):
      unrepeated_signature = unique
    else:
      raise ValueError(
          "unique=%s: neither None nor False nor Ture nor a callable",
          r(unique)
      )

    self.unrepeated_signature = unrepeated_signature
    self._lock = Lock()

  def __str__(self):
    return "%s:%d[]" % (self.__class__.__name__, len(self))

  def __repr__(self):
    return "%s(%r)" % (self.__class__.__name__, self.queued)

  def get(self):
    ''' Get pops from the start of the list.
    '''
    with self._lock:
      try:
        return self.queued.pop(0)
      except IndexError:
        raise Queue_Empty("list is empty")  # pylint: disable=raise-missing-from

  def append(self, item):
    ''' Append an item to the queue, aka `put`.
    '''
    with self._lock:
      self.queued.append(item)

  def put(self, item):
    ''' Put appends to the queue.
    '''
    return self.append(item)

  def extend(self, items):
    ''' Convenient/performant queue-lots-of-items.
    '''
    if isinstance(items, str):
      raise TypeError(
          "extend expects an iterable and str is explicitly disallowed, rejecting %r"
          % (repr(items),)
      )
    with self._lock:
      self.queued.extend(items)

  def insert(self, index, item):
    ''' Insert `item` at `index` in the queue.
    '''
    with self._lock:
      self.queued.insert(index, item)

  def prepend(self, items, offset=0):
    ''' Insert `items` at `offset` (default `0`, the front of the queue).
    '''
    if not isinstance(items, (list, tuple)):
      if isinstance(items, str):
        raise TypeError(
            "prepend expects an iterable and str is explicitly disallowed, rejecting %r"
            % (repr(items),)
        )
      items = list(items)
    with self._lock:
      self.queued[offset:offset] = items

  def __bool__(self):
    ''' A `ListQueue` looks a bit like a container,
        and is false when empty.
    '''
    with self._lock:
      return bool(self.queued)

  def __len__(self):
    return len(self.queued)

  def __iter__(self):
    ''' A `ListQueue` is iterable.
    '''
    if self.unrepeated_signature is None:
      return self

    # remove duplicates from the iteration
    def unique_items():
      while True:
        try:
          item = self.get()
        except Queue_Empty:
          break
        yield item

    return unrepeated(unique_items())

  def __next__(self):
    ''' Iteration gets from the queue.
    '''
    try:
      return self.get()
    except Queue_Empty:
      raise StopIteration("list is empty")  # pylint: disable=raise-missing-from

def get_batch(q, max_batch=128, *, poll_delay=0.01):
  ''' Get up to `max_batch` closely spaced items from the queue `q`.
      Return the batch. Raise `queue.Empty` if the first `q.get()` raises.

      Block until the first item arrives. While the batch's size is
      less that `max_batch` and there is another item available
      within `poll_delay` seconds, append that item to the batch.

      This requires `get_batch()` to be the sole consumer of `q`
      for correct operation as it makes decisions based on `q.empty()`.
  '''
  if max_batch < 2:
    raise ValueError("max_batch:%r should be >= 2" % (max_batch,))
  if poll_delay <= 0:
    raise ValueError("poll_delay:%r should be > 0" % (poll_delay,))
  batch = []
  while len(batch) < max_batch:
    try:
      item = q.get()
    except Queue_Empty:
      if batch:
        return batch
      raise
    batch.append(item)
    if q.empty():
      time.sleep(poll_delay)
      if q.empty():
        break
  return batch

if __name__ == '__main__':
  import cs.queues_tests
  cs.queues_tests.selftest(sys.argv)
