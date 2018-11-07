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
from cs.obj import O
from cs.pfx import Pfx, PfxCallInfo
from cs.py3 import Queue, PriorityQueue, Queue_Empty
from cs.resources import MultiOpenMixin, not_closed, ClosedError
from cs.seq import seq

DISTINFO = {
    'description': "some Queue subclasses and ducktypes",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.logutils',
        'cs.obj',
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

  class _QueueIterator_Sentinel(object):
    pass

  sentinel = _QueueIterator_Sentinel()

  def __init__(self, q, name=None):
    if name is None:
      name = "QueueIterator-%d" % (seq(),)
    MultiOpenMixin.__init__(self, finalise_later=True)
    self.q = q
    self.name = name
    # count of non-sentinel items
    self._item_count = 0

  def __str__(self):
    return "<%s:opens=%d>" % (self.name, self._opens)

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
    pass

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
      warning("%s: Queue_Empty, (SHOULD THIS HAPPEN?) calling finalise...", self)
      self._put(self.sentinel)
      self.finalise()
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
      raise Queue_Empty("got StopIteration from %s" % (self,))

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

def IterableQueue(capacity=0, name=None, *args, **kw):
  ''' Factory to create an iterable Queue.
  '''
  if not isinstance(capacity, int):
    raise RuntimeError("capacity: expected int, got: %r" % (capacity,))
  name = kw.pop('name', name)
  return _QueueIterator(Queue(capacity, *args, **kw), name=name).open()

def IterablePriorityQueue(capacity=0, name=None, *args, **kw):
  ''' Factory to create an iterable PriorityQueue.
  '''
  if not isinstance(capacity, int):
    raise RuntimeError("capacity: expected int, got: %r" % (capacity,))
  name = kw.pop('name', name)
  return _QueueIterator(PriorityQueue(capacity, *args, **kw), name=name).open()

class Channel(object):
  ''' A zero-storage data passage.
      Unlike a Queue(1), put() blocks waiting for the matching get().
  '''
  def __init__(self):
    self.__readable = Lock()
    self.__readable.acquire()
    self.__writable = Lock()
    self.__writable.acquire()
    self.closed = False

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
    O.__init__(self)
    MultiOpenMixin.__init__(self)
    self.functor = functor
    self.outQ = outQ

  def __str__(self):
    return "PushQueue:%s" % (self.name,)

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
    pass

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
    O.__init__(self)
    MultiOpenMixin.__init__(self)
    self.blocking = blocking

  def __str__(self):
    return "NullQueue:%s" % (self.name,)

  def __repr__(self):
    return "<%s blocking=%s>" % (self, self.blocking)

  def put(self, item):
    ''' Put a value onto the Queue; it is discarded.
    '''
    pass

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
    pass

  def shutdown(self):
    ''' Shut down the queue.
    '''
    pass

  def __iter__(self):
    return self

  def __next__(self):
    try:
      return self.get()
    except Queue_Empty:
      raise StopIteration

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
    self.Q = PriorityQueue()    # queue of waiting jobs
    self.pending = None         # or (Timer, when, func)
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
      self.Q.put( (None, None, None) )
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
    self.Q.put( (when, seq(), func) )

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
            self.pending = None     # nothing pending now
            T = None                # let go of the cancelled timer
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
          except Exception:
            exception("func %s threw exception", func)
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
              except Exception:
                exception("func %s threw exception", Tfunc)
              else:
                debug("func %s returns %s", Tfunc, retval)
          with self._lock:
            T = Timer(delay, partial(doit, self))
            self.pending = [ T, when, func ]
            T.start()
      self.mainRunning = False

if __name__ == '__main__':
  import cs.queues_tests
  cs.queues_tests.selftest(sys.argv)
