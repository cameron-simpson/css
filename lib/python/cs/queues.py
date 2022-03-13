#!/usr/bin/python
#
# Some Queue subclasses and ducktypes.
#       - Cameron Simpson <cs@cskk.id.au>
#

''' Queue-like items: iterable queues and channels.
'''

import sys
from functools import partial
from threading import Timer, Lock, RLock, Thread
import time
##from cs.debug import Lock, RLock, Thread
import cs.logutils
from cs.logutils import exception, warning, debug
from cs.pfx import Pfx, PfxCallInfo
from cs.py3 import Queue, PriorityQueue, Queue_Empty
from cs.resources import MultiOpenMixin, not_closed, ClosedError
from cs.seq import seq

__version__ = '20220313-post'

DISTINFO = {
    'description':
    "some Queue subclasses and ducktypes",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.logutils',
        'cs.pfx',
        'cs.py3',
        'cs.resources',
        'cs.seq',
    ],
}

class _QueueIterator(MultiOpenMixin):
  ''' A QueueIterator is a wrapper for a Queue (or ducktype) which
      presents an iterator interface to collect items.
      It does not offer the .get or .get_nowait methods.
  '''

  sentinel = object()

  def __init__(self, q, name=None):
    if name is None:
      name = "QueueIterator-%d" % (seq(),)
    self.q = q
    self.name = name
    self.finalise_later = True
    # count of non-sentinel items
    self._item_count = 0

  def __str__(self):
    return "%s(%r)" % (type(self).__name__, self.name)

  @not_closed
  def put(self, item, *args, **kw):
    ''' Put `item` onto the queue.
        Warn if the queue is closed.
        Reject if `item` is the sentinel.
    '''
    if self.closed:
      with PfxCallInfo():
        warning("%r.put: all closed: item=%s", self, item)
      raise ClosedError("_QueueIterator closed")
    if item is self.sentinel:
      raise ValueError("put(sentinel)")
    self._item_count += 1
    return self._put(item, *args, **kw)

  def _put(self, item, *args, **kw):
    ''' Direct call to self.q.put() with no checks.
    '''
    return self.q.put(item, *args, **kw)

  def startup(self):
    ''' Required MultiOpenMixin method.
    '''

  def shutdown(self):
    ''' Support method for MultiOpenMixin.shutdown.
        Queue the sentinel object so that calls to .get() from .__next__ do not block.
    '''
    self._put(self.sentinel)

  def __iter__(self):
    ''' Iterable interface for the queue.
    '''
    return self

  def __next__(self):
    ''' Return the next item from the queue.
        If the queue is closed, raise StopIteration.
    '''
    q = self.q
    try:
      item = q.get()
    except Queue_Empty as e:
      warning(
          "%s: Queue_Empty: %s, (SHOULD THIS HAPPEN?) calling finalise...",
          self, e
      )
      self._put(self.sentinel)
      self.finalise()
      # pylint: disable=raise-missing-from
      raise StopIteration("Queue_Empty: %s" % (e,))
    if item is self.sentinel:
      # sentinel consumed (clients won't see it, so we must)
      self.q.task_done()
      # put the sentinel back for other iterators
      self._put(self.sentinel)
      raise StopIteration("SENTINEL")
    self._item_count -= 1
    return item

  next = __next__

  def _get(self):
    ''' Calls the inner queue's .get via .__next__; can break other users' iterators.
    '''
    try:
      return next(self)
    except StopIteration as e:
      # pylint: disable=raise-missing-from
      raise Queue_Empty("got %s from %s" % (e, self))

  def empty(self):
    ''' Test if the queue is empty.
    '''
    return self._item_count == 0

  def task_done(self):
    ''' Report that an item has been processed.
    '''
    self.q.task_done()

  def join(self):
    ''' Wait for the Queue items to complete.
    '''
    self.q.join()

def IterableQueue(capacity=0, name=None):
  ''' Factory to create an iterable Queue.
  '''
  return _QueueIterator(Queue(capacity), name=name).open()

def IterablePriorityQueue(capacity=0, name=None):
  ''' Factory to create an iterable PriorityQueue.
  '''
  return _QueueIterator(PriorityQueue(capacity), name=name).open()

class Channel(object):
  ''' A zero-storage data passage.
      Unlike a Queue(1), put() blocks waiting for the matching get().
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
    ''' Call the Channel.
        With no arguments, do a .get().
        With an argument, do a .put().
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
      raise StopIteration()
    return self.get()

  # pylint: disable=consider-using-with
  @not_closed
  def get(self):
    ''' Read a value from the Channel.
        Blocks until someone put()s to the Channel.
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
    ''' Write a value to the Channel.
        Blocks until a corresponding get() occurs.
    '''
    # block until there is a matching .get()
    self.__writable.acquire()
    self._value = value
    # allow .get() to proceed
    self.__readable.release()

  def close(self):
    ''' Close the Channel, preventing further puts.
    '''
    if self.closed:
      warning("%s: .close() of closed Channel" % (self,))
    else:
      self.closed = True

class PushQueue(MultiOpenMixin):
  ''' A puttable object which looks like an iterable Queue.

      Calling .put(item) calls `func_push` supplied at initialisation
      to trigger a function on data arrival, whose processing is mediated
      queued via a Later for delivery to the output queue.
  '''

  def __init__(self, name, functor, outQ):
    ''' Initialise the PushQueue with the Later `L`, the callable `functor`
        and the output queue `outQ`.

        Parameters:
        * `functor` is a one-to-many function which accepts a single
          item of input and returns an iterable of outputs; it may be a
          generator. These outputs are passed to outQ.put individually as
          received.
        * `outQ` is a MultiOpenMixin which accepts via its .put() method.
    '''
    if name is None:
      name = "%s%d-%s" % (self.__class__.__name__, seq(), functor)
    self.name = name
    self._lock = RLock()
    MultiOpenMixin.__init__(self)
    self.functor = functor
    self.outQ = outQ

  def __str__(self):
    return "%s:%s" % (type(self).__name__, self.name)

  def __repr__(self):
    return "<%s outQ=%s>" % (self, self.outQ)

  @not_closed
  def put(self, item):
    ''' Receive a new item.
        If self.is_iterable then presume that self.func_push returns
        an iterator and submit self.func_push(item) to defer_iterable.
        Otherwise, defer self.func_push(item) and after completion,
        queue its results to outQ.
    '''
    outQ = self.outQ
    functor = self.functor
    with outQ:
      for computed in functor(item):
        outQ.put(computed)

  def startup(self):
    ''' Start up.
    '''

  def shutdown(self):
    ''' shutdown() is called by MultiOpenMixin._close() to close
        the outQ for real.
    '''
    self.outQ.close()

class NullQueue(MultiOpenMixin):
  ''' A queue-like object that discards its inputs.
      Calls to .get() raise Queue_Empty.
  '''

  def __init__(self, blocking=False, name=None):
    ''' Initialise the NullQueue.

        Parameters:
        * `blocking`: if true, calls to .get() block until .shutdown().
          Default: False.
        * `name`: a name for this NullQueue.
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
    ''' Put a value onto the Queue; it is discarded.
    '''

  def get(self):
    ''' Get the next value. Always raises Queue_Empty.
        If .blocking, delay until .shutdown().
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
    ''' Close the TimerQueue. This forbids further job submissions.
        If `cancel` is supplied and true, cancel all pending jobs.
        Note: it is still necessary to call TimerQueue.join() to
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
        'func' is the job function, typically made with functools.partial.
    '''
    assert not self.closed, "add() on closed TimerQueue"
    self.Q.put((when, seq(), func))

  def join(self):
    ''' Wait for the main loop thread to finish.
    '''
    assert self.mainThread is not None, "no main thread to join"
    self.mainThread.join()

  def _main(self):
    ''' Main loop:
        Pull requests off the queue; they will come off in time order,
        so we always get the most urgent item.
        If we're already delayed waiting for a previous request,
          halt that request's timer and compare it with the new job; push the
          later request back onto the queue and proceed with the more urgent
          one.
        If it should run now, run it.
        Otherwise start a Timer to run it later.
        The loop continues processing items until the TimerQueue is closed.
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

  def __init__(self, queued=None):
    ''' Initialise the queue.
        `queued` is an optional iterable of initial items for the queue.
    '''
    self.queued = []
    if queued is not None:
      # catch a common mistake
      assert not isinstance(queued, str)
      self.queued.extend(queued)
    self._lock = Lock()

  def get(self):
    ''' Get pops from the start of the list.
    '''
    with self._lock:
      try:
        return self.queued.pop(0)
      except IndexError:
        raise Queue_Empty("list is empty")  # pylint: disable=raise-missing-from

  def put(self, item):
    ''' Put appends to the queue.
    '''
    with self._lock:
      self.queued.append(item)

  def extend(self, items):
    ''' Convenient/performant queue-lots-of-items.
    '''
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
      items = list(items)
    with self._lock:
      self.queued[offset:offset] = items

  def __bool__(self):
    ''' A `ListQueue` looks a bit like a container,
        and is false when empty.
    '''
    with self._lock:
      return bool(self.queued)

  def __iter__(self):
    ''' A `ListQueue` is iterable.
    '''
    return self

  def __next__(self):
    ''' Iteration gets from the queue.
    '''
    try:
      return self.get()
    except Queue_Empty:
      raise StopIteration("list is empty")  # pylint: disable=raise-missing-from

if __name__ == '__main__':
  import cs.queues_tests
  cs.queues_tests.selftest(sys.argv)
