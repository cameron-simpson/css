#!/usr/bin/python
#
# Some Queue subclasses and ducktypes.
#       - Cameron Simpson <cs@zip.com.au>
#

import sys
from functools import partial
from threading import Condition, Timer
import time
from cs.asynchron import Asynchron
from cs.debug import Lock, RLock, Thread, trace_caller
from cs.excutils import noexc, logexc
from cs.logutils import exception, warning, debug, D, Pfx, PfxCallInfo
from cs.seq import seq
from cs.py3 import Queue, PriorityQueue, Queue_Full, Queue_Empty
from cs.obj import O, Proxy

def not_closed(func):
  ''' Decorator to wrap NestingOpenCloseMixin proxy object methods
      which hould raise when self.closed.
  '''
  def not_closed_wrapper(self, *a, **kw):
    if self.closed:
      raise RuntimeError("%s: %s: already closed" % (not_closed_wrapper.__name__, self))
    return func(self, *a, **kw)
  not_closed_wrapper.__name__ = "not_closed_wrapper(%s)" % (func.__name__,)
  return not_closed_wrapper

class _NOC_Proxy(Proxy):
  ''' A Proxy subclass to return from NestingOpenCloseMixin.open() and __enter__.
      Note tht this has its own localised .closed attribute which starts False.
      This lets users indidually track .closed for their use.
  '''

  def __init__(self, other):
    Proxy.__init__(self, other)
    self.closed = False

  def __str__(self):
    return "open(%s[closed=%r])" % (self._proxied, self.closed)

  @not_closed
  def close(self):
    ''' Close this open-proxy. Sanity check then call inner close.
    '''
    self.closed = True
    self._proxied._close()

class NestingOpenCloseMixin(object):
  ''' A mixin to count open and closes, and to call .shutdown() when the count goes to zero.
      A count of active open()s is kept, and on the last close()
      the object's .shutdown() method is called.
      Use via the with-statement calls open()/close() for __enter__()
      and __exit__().
      Multithread safe.
      This mixin uses the internal attribute _opens and relies on a
      preexisting attribute _lock for locking.
  '''

  def __init__(self, open=False, on_open=None, on_close=None, on_shutdown=None, proxy_type=None):
    ''' Initialise the NestingOpenCloseMixin state.
	If the optional parameter `open` is true, return the object in "open"
        state (active opens == 1) otherwise closed (opens == 0).
        The default is "closed" to optimise use as a context manager;
        the __enter__ method will open the object.
        The following callback parameters may be supplied to aid tracking activity:
        `on_open`: called on open with the post-increment open count
        `on_close`: called on close with the pre-decrement open count
        `on_shutdown`: called after calling self.shutdown()
    '''
    if proxy_type is None:
      proxy_type = _NOC_Proxy
    self._noc_proxy_type = proxy_type
    self._opens = 0
    self.on_open = on_open
    self.on_close = on_close
    self.on_shutdown = on_shutdown
    self._asynchron = Asynchron()
    if open:
      self.open()

  def open(self):
    ''' Increment the open count.
	If self.on_open, call self.on_open(self, count) with the
	post-increment count.
        Return a Proxy object that tracks this open.
    '''
    with self._lock:
      self._opens += 1
      count = self._opens
    if self.on_open:
      self.on_open(self, count)
    return self._noc_proxy_type(self)

  def __enter__(self):
    return self.open()

  @logexc
  def _close(self):
    ''' Decrement the open count.
	If self.on_open, call self.on_open(self, count) with the
	pre-decrement count.
        If self.on_shutdown and the count goes to zero, call self.on_shutdown(self).
        If the count goes to zero, call self.shutdown().
    '''
    with self._lock:
      if self._opens < 1:
        raise RuntimeError("%s: EXTRA CLOSE", self)
      self._opens -= 1
      count = self._opens
    if self.on_close:
      self.on_close(self, count)
    if count == 0:
      self._asynchron.put(True)
      self.shutdown()
      if self.on_shutdown:
        self.on_shutdown(self)
    elif self.all_closed:
      error("%s.close: count=%r, ALREADY CLOSED", self, count)

  @property
  def all_closed(self):
    if self._opens > 0:
      return False
    if self._opens < 0:
      with PfxCallInfo():
        warning("%r._opens < 0: %r", self, self._opens)
    return True

  def __exit__(self, exc_type, exc_value, traceback):
    self.close()
    return False

  def join(self):
    return self._asynchron.join()

class _Q_Proxy(_NOC_Proxy):
  ''' A _NOC_Proxy subclass for queues with a sanity check on .put.
  '''

  @not_closed
  def put(self, item, *a, **kw):
    return self._proxied.put(item, *a, **kw)

class QueueIterator(NestingOpenCloseMixin,O):
  ''' A QueueIterator is a wrapper for a Queue (or ducktype) which
      presents and iterator interface to collect items.
      It does not offer the .get or .get_nowait methods.
  '''

  sentinel = object()

  def __init__(self, q, name=None, open=False):
    if name is None:
      name = "QueueIterator-%d" % (seq(),)
    self._lock = Lock()
    self.name = name
    O.__init__(self, q=q)
    NestingOpenCloseMixin.__init__(self, open=open, proxy_type=_Q_Proxy)

  def __str__(self):
    return "<%s:opens=%d,closed=%s>" % (self.name, self._opens, self.all_closed)

  def put(self, item, *args, **kw):
    ''' Put `item` onto the queue.
        Warn if the queue is closed.
        Reject if `item` is the sentinel.
    '''
    if self.all_closed:
      with PfxCallInfo():
        warning("queue closed: item=%s", item)
    if item is self.sentinel:
      raise ValueError("put(sentinel)")
    return self._put(item, *args, **kw)

  def _put(self, item, *args, **kw):
    ''' Direct call to self.q.put() with no checks.
    '''
    return self.q.put(item, *args, **kw)

  def shutdown(self):
    ''' Support method for NestingOpenCloseMixin.shutdown.
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
    if self.all_closed and q.empty():
      raise StopIteration
    try:
      item = q.get()
    except Queue_Empty:
      raise StopIteration
    if item is self.sentinel:
      # put the sentinel back for other iterators
      self._put(item)
      raise StopIteration
    return item

  next = __next__

##  def __getattr__(self, attr):
##    return getattr(self.q, attr)
##
##  def get(self, block=True, timeout=None):
##    if block and timeout is None:
##      # calling an indefinitiely blocking get if probably an 
##      raise RuntimeError(".get(block=%r,timeout=%r) on QueueIterator", block, timeout)
##    if block and timeout is not None:
##      start = time.time()
##    q = self.q
##    item = q.get(block=block, timeout=timeout)
##    # block must have been True since no exception raised
##    while item is self.sentinel:
##      # the sentinel should be ignored
##      if timeout is None:
##        if q.empty():
##          q.put(item)
##          continue
##      else:
##        now = time.time()
##        elapsed = now - start
##        timeout -= elapsed
##        if timeout <= 0:
##          if q.empty():
##            raise Queue_Empty
##      # timeout > 0 or queue not empty
##      # get the next item, ignoring the sentinel
##      try:
##        item = q.get(block=block, timeout=timeout)
##      except Queue_Empty:
##        # put the sentinel back to prevent blocking another caller
##        q._put(self.sentinel)
##        raise
##      q._put(self.sentinel)
##    return item
##
##  def get_nowait(self):
##    q = self.q
##    item = q.get_nowait()
##    if item is self.sentinel:
##      q._put(self.sentinel)
##      raise Queue_Empty
##    return item

def IterableQueue(capacity=0, name=None, open=False, *args, **kw):
  name = kw.pop('name', name)
  open = kw.pop('open', open)
  return QueueIterator(Queue(capacity, *args, **kw), name=name, open=open)

def IterablePriorityQueue(capacity=0, name=None, open=False, *args, **kw):
  name = kw.pop('name', name)
  open = kw.pop('open', open)
  return QueueIterator(PriorityQueue(capacity, *args, **kw), name=name, open=open)

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
    return "<cs.threads.Channel %s>" % (state,)

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
    self.close()
    value = self._value
    delattr(self,'_value')
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
    if self.closed:
      warning("%s: .close() of closed Channel" % (self,))
    else:
      self.closed = True

class PushQueue(NestingOpenCloseMixin, O):
  ''' A puttable object to look like a Queue.
      Calling .put(item) calls `func_push` supplied at initialisation to
      trigger a function on data arrival.
  '''

  def __init__(self, L, func_push, outQ, func_final=None, is_iterable=False, name=None,
                     open=False, on_open=None, on_close=None, on_shutdown=None):
    ''' Initialise the PushQueue with the Later `L`, the callable `func_push`
        and the output queue `outQ`.
	`func_push` is a one-to-many function which accepts a single
	  item of input and returns an iterable of outputs; it may
	  be a generator.
          This iterable is submitted to `L` via defer_iterable to call
          `outQ.put` with each output.
        `outQ` accepts results from the callable via its .put() method.
        `func_final`, if specified and not None, is called after completion of
          all calls to `func_push`.
        If `is_iterable``, submit `func_push(item)` via L.defer_iterable() to
          allow a progressive feed to `outQ`.
        Otherwise, submit `func_push` with `item` via L.defer().
    '''
    if name is None:
      name = "%s%d-%s" % (self.__class__.__name__, seq(), func_push)
    self.name = name
    self._lock = Lock()
    O.__init__(self)
    NestingOpenCloseMixin.__init__(self, open=open,
                                   on_open=on_open, on_close=on_close, on_shutdown=on_shutdown,
                                   proxy_type=_Q_Proxy)
    self.later = L
    self.func_push = func_push
    self.outQ = outQ
    self.func_final = func_final
    self.is_iterable = is_iterable
    self.LFs = []
    if not is_iterable:
      raise RuntimeError("PUSHQUEUE NOT IS_ITERABLE")

  def __str__(self):
    return "PushQueue:%s" % (self.name,)

  def __repr__(self):
    return "<%s outQ=%s>" % (self, self.outQ)

  def put(self, item):
    ''' Receive a new item.
	If self.is_iterable then presume that self.func_push returns
	an iterator and submit self.func_push(item) to defer_iterable.
        Otherwise, defer self.func_push(item) and after completion,
        queue its results to outQ.
    '''
    debug("%s.put(item=%r)", self, item)
    if self.all_closed:
      warning("%s.put(%s) when all closed" % (self, item))
    L = self.later
    if self.is_iterable:
      # add to the outQ opens; defer_iterable will close it
      ##D("%s: %s.open()", self, self.outQ)
      self.outQ.open()
      try:
        items = self.func_push(item)
        ##items = list(items)
      except Exception as e:
        exception("%s.func_push: %s", self, e)
        items = ()
      ##D("%s: func_push(%r) => items=%r", self, item, items)
      L._defer_iterable(items, self.outQ)
    else:
      raise RuntimeError("PUSHQUEUE NOT IS_ITERABLE")
      # defer the computation then call _push_items which puts the results
      # and closes outQ
      LF = L._defer( self.func_push, item )
      self.LFs.append(LF)
      L._after( (LF,), None, self._push_items, LF )

  # NB: reports and discards exceptions
  @noexc
  def _push_items(self, LF):
    ''' Handler to run after completion of `LF`.
        Put the results of `LF` onto `outQ`.
    '''
    raise RuntimeError("NOTREACHED")
    try:
      for item in LF():
        self.outQ.put(item)
    except Exception as e:
      exception("%s._push_items: exception putting results of LF(): %s", self, e)
    self.outQ.close()

  def shutdown(self):
    ''' shutdown() is called by NestingOpenCloseMixin._close() to close
        the outQ for real.
    '''
    debug("%s.shutdown()", self)
    LFs = self.LFs
    self.LFs = []
    if self.func_final:
      # run func_final to completion before closing outQ
      LFclose = self.later._after( LFs, None, self._run_func_final )
      LFs = (LFclose,)
    self.later._after( LFs, None, self.outQ.close )

  def _run_func_final(self):
    debug("%s._run_func_final()", self)
    items = self.func_final()
    items = list(items)
    outQ = self.outQ
    for item in items:
      outQ.put(item)

class NullQueue(NestingOpenCloseMixin, O):
  ''' A queue-like object that discards its inputs.
      Calls to .get() raise Queue_Empty.
  '''

  def __init__(self, blocking=False, name=None,
               open=False, on_open=None, on_close=None, on_shutdown=None):
    ''' Initialise the NullQueue.
        `blocking`: if true, calls to .get() block until .shutdown().
          Its default is False. 
        `name`: a name for this NullQueue.
    '''
    if name is None:
      name = "%s%d" % (self.__class__.__name__, seq())
    self.name = name
    self._lock = Lock()
    self._close_cond = Condition(self._lock)
    O.__init__(self)
    NestingOpenCloseMixin.__init__(self, open=open,
                                   on_open=on_open, on_close=on_close, on_shutdown=on_shutdown,
                                   proxy_type=_Q_Proxy)
    self.blocking = blocking

  def __str__(self):
    return "NullQueue:%s" % (self.name,)

  def __repr__(self):
    return "<%s blocking=%s>" % (self, self.blocking)

  def put(self, item):
    ''' Put a value onto the Queue; it is discarded.
    '''
    debug("%s.put: DISCARD %r", self, item)
    pass

  def get(self):
    ''' Get the next value. Always raises Queue_Empty.
        If .blocking, delay until .shutdown().
    '''
    if self.blocking:
      with self._lock:
        if not self.all_closed:
          self._close_cond.wait()
    raise Queue_Empty

  def shutdown(self):
    ''' Shut down the queue. Wakes up anything waiting on ._close_cond, such
        as callers of .get() on a .blocking queue.
    '''
    with self._lock:
      self._close_cond.notify_all()

  def __iter__(self):
    return self

  def next(self):
    try:
      return self.get()
    except Queue_Empty:
      raise StopIteration

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
          except:
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
              except:
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
