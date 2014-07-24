#!/usr/bin/python
#
# Some Queue subclasses and ducktypes.
#       - Cameron Simpson <cs@zip.com.au>
#

import sys
from functools import partial
import logging
import threading
from threading import Condition, Timer
import time
import traceback
from cs.debug import Lock, RLock, Thread, trace_caller, stack_dump
from cs.excutils import noexc, logexc
from cs.logutils import exception, error, warning, debug, D, Pfx, PfxCallInfo
from cs.seq import seq
from cs.py3 import Queue, PriorityQueue, Queue_Full, Queue_Empty
from cs.py.func import callmethod_if as ifmethod
from cs.obj import O, Proxy

def not_closed(func):
  ''' Decorator to wrap NestingOpenCloseMixin proxy object methods
      which hould raise when self.closed.
  '''
  def not_closed_wrapper(self, *a, **kw):
    if self.closed:
      error("%r: ALREADY CLOSED: closed set to True from the following:", self)
      stack_dump(stack=self.closed_stacklist, log_level=logging.ERROR)
      raise RuntimeError("%s: %s: already closed" % (not_closed_wrapper.__name__, self))
    return func(self, *a, **kw)
  not_closed_wrapper.__name__ = "not_closed_wrapper(%s)" % (func.__name__,)
  return not_closed_wrapper

class _NOC_Proxy(Proxy):
  ''' A Proxy subclass to return from NestingOpenCloseMixin.open() and __enter__.
      Note tht this has its own localised .closed attribute which starts False.
      This lets users indidually track .closed for their use.
  '''

  def __init__(self, other, name=None):
    Proxy.__init__(self, other)
    if name is None:
      name = "%s-open%d" % ( getattr(other,
                                     'name',
                                     "%s#%d" % (self.__class__.__name__,
                                                id(self))),
                             seq()
                           )
    self.name = name
    self.closed = False

  def __str__(self):
    return "open(%r:%s[closed=%r,all_closed=%r])" % (self.name, self._proxied, self.closed, self._proxied.all_closed)

  __repr__ = __str__

  @not_closed
  def close(self, check_final_close=False):
    ''' Close this open-proxy. Sanity check then call inner close.
    '''
    self.closed = True
    self.closed_stacklist = traceback.extract_stack()
    self._proxied._close()
    if check_final_close:
      if self._proxied.all_closed:
        self.D("OK FINAL CLOSE")
      else:
        raise RuntimeError("%s: expected this to be the final close, but it was not" % (self,))

class _NOC_ThreadingLocal(threading.local):

  def __init__(self):
    self.cmgr_proxies = []

class NestingOpenCloseMixin(O):
  ''' A mixin to count open and closes, and to call .shutdown() when the count goes to zero.
      A count of active open()s is kept, and on the last close()
      the object's .shutdown() method is called.
      Use via the with-statement calls open()/close() for __enter__()
      and __exit__().
      Multithread safe.
      This mixin uses the internal attribute _opens and relies on a
      preexisting attribute _lock for locking.
  '''

  def __init__(self, proxy_type=None, finalise_later=False):
    ''' Initialise the NestingOpenCloseMixin state.
        Then takes makes use of the following methods if present:
          `self.on_open(count)`: called on open with the post-increment open count
          `self.on_close(count)`: called on close with the pre-decrement open count
          `self.on_shutdown()`: called after calling self.shutdown()
        `finalise_later`: do not notify the finalisation Condition on
          shutdown, require a separate call to .finalise().
          This is mode is useful for objects such as queues where
          the final close prevents further .put calls, but users
          calling .join may need to wait for all the queued items
          to be processed.
    '''
    if proxy_type is None:
      proxy_type = _NOC_Proxy
    self._noc_proxy_type = proxy_type
    self._noc_tl = _NOC_ThreadingLocal()
    self.opened = False
    self._opens = 0
    ##self.closed = False # final _close() not yet called
    self._keep_open = None
    self._keep_open_until = None
    self._keep_open_poll_interval = 0.5
    self._keep_open_increment = 1.0
    self._finalise_later= finalise_later
    self._finalise = Condition(self._lock)

  def open(self, name=None):
    ''' Increment the open count.
	If self.on_open, call self.on_open(self, count) with the
	post-increment count.
        `name`: optional name for this open object.
        Return a Proxy object that tracks this open.
    '''
    self.opened = True
    with self._lock:
      self._opens += 1
      count = self._opens
    ifmethod(self, 'on_open', a=(count,))
    return self._noc_proxy_type(self, name=name)

  def close(self):
    ''' Placeholder method to warn callers that they should be using the proxy returned from .open().
    '''
    raise RuntimeError("%s subclasses do not support .close(): that method is to be called on the _NOC_Proxy returned from .open()" % (self.__class__.__name__,))

  @property
  def cmgr_proxy(self):
    ''' Property representing the current context manager proxy.
    '''
    return self._noc_tl.cmgr_proxies[-1]

  def __enter__(self):
    ''' NestingOpenClose context managers return a proxy object.
    '''
    proxy = self.open()
    self._noc_tl.cmgr_proxies.append(proxy)
    return proxy

  def __exit__(self, exc_type, exc_value, traceback):
    proxy = self._noc_tl.cmgr_proxies.pop()
    proxy.close()
    return False

  @logexc
  def _close(self):
    ''' Decrement the open count.
        If self.on_close, call self.on_close(self, count) with the
        pre-decrement count.
        If self.on_shutdown and the count goes to zero, call self.on_shutdown(self).
        If the count goes to zero, call self.shutdown().
    '''
    with self._lock:
      if self._opens < 1:
        error("%s: EXTRA CLOSE", self)
      self._opens -= 1
      count = self._opens
    ifmethod(self, 'on_close', a=(count,))
    if count == 0:
      ifmethod(self, 'on_shutdown')
      self.shutdown()
      if not self._finalise_later:
        self.finalise()
    elif self.all_closed:
      error("%s.close: count=%r, ALREADY CLOSED", self, count)

  def finalise(self):
    ''' Finalise the object, releasing all callers of .join().
	Normally this is called automatically after .shutdown unless
	`finalise_later` was set to true during initialisation.
    '''
    with self._lock:
      if self._finalise:
        self._finalise.notify_all()
        self._finalise = None
        return
    warning("%s: finalised more than once", self)

  @property
  def all_closed(self):
    if self._opens > 0:
      return False
    if self._opens < 0:
      with PfxCallInfo():
        warning("%r._opens < 0: %r", self, self._opens)
    if not self.opened:
      # never opened, so not totally closed
      return False
    ##if not self.closed:
    ##  with PfxCallInfo():
    ##    warning("%r.closed = %r, but want to return all_closed=True", self, self.closed)
    ##  return False
    return True

  def join(self):
    ''' Join this object.
        Wait for the internal _finalise Condition (if still not None).
        Normally this is notified at the end of the shutdown procedure
        unless the object's `finalise_later` parameter was true.
    '''
    self._lock.acquire()
    if self._finalise:
      self._finalise.wait()
    else:
      self._lock.release()

  def ping(self):
    ''' Mark this object as "busy"; it will be kept open a little longer in case of more use.
    '''
    T = None
    with self._lock:
      if self._keep_open is None:
        name = "%s._ping_mainloop" % (self,)
        P = self.open(name=name)
        self._keep_open = P
        T = Thread(name=name, target=self._ping_mainloop, args=(P,))
      else:
        P = self._keep_open
    self._keep_open_until = time.time() + self._keep_open_increment
    if T:
      T.start()

  def _ping_mainloop(self, proxy):
    ''' Pinger main loop: wait until expiry then close the open proxy.
    '''
    name = self._keep_open.name
    while self._keep_open_until > time.time():
      debug("%s: pinger: sleep for another %gs", name, self._keep_open_poll_interval)
      time.sleep(self._keep_open_poll_interval)
    self._keep_open = None
    self._keep_open_until = None
    debug("%s: pinger: close()", name)
    proxy.close()

class _Q_Proxy(_NOC_Proxy):
  ''' A _NOC_Proxy subclass for queues with a sanity check on .put.
  '''

  @not_closed
  def put(self, item, *a, **kw):
    ##D("PUT %r", item)
    ##D("%s PUT %r", self, item)
    return self._proxied.put(item, *a, **kw)

class _QueueIterator(NestingOpenCloseMixin):
  ''' A QueueIterator is a wrapper for a Queue (or ducktype) which
      presents an iterator interface to collect items.
      It does not offer the .get or .get_nowait methods.
  '''

  sentinel = object()

  def __init__(self, q, name=None):
    if name is None:
      name = "QueueIterator-%d" % (seq(),)
    self._lock = Lock()
    self.name = name
    O.__init__(self, q=q)
    NestingOpenCloseMixin.__init__(self, proxy_type=_Q_Proxy, finalise_later=True)

  def __str__(self):
    return "<%s:opens=%d,closed=%s>" % (self.name, self._opens, self.all_closed)

  def put(self, item, *args, **kw):
    ''' Put `item` onto the queue.
        Warn if the queue is closed.
        Reject if `item` is the sentinel.
    '''
    if self.all_closed:
      with PfxCallInfo():
        warning("%r.put: all closed: item=%s", self, item)
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
    try:
      item = q.get()
    except Queue_Empty:
      D("%s: EMPTY, calling finalise...", self)
      self.finalise()
      raise StopIteration
    if item is self.sentinel:
      # put the sentinel back for other iterators
      self._put(item)
      raise StopIteration
    return item

  next = __next__

def IterableQueue(capacity=0, name=None, *args, **kw):
  if not isinstance(capacity, int):
    raise RuntimeError("capacity: expected int, got: %r" % (capacity,))
  name = kw.pop('name', name)
  return _QueueIterator(Queue(capacity, *args, **kw), name=name).open()

def IterablePriorityQueue(capacity=0, name=None, *args, **kw):
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

class PushQueue(NestingOpenCloseMixin):
  ''' A puttable object to look like a Queue.
      Calling .put(item) calls `func_push` supplied at initialisation
      to trigger a function on data arrival, which returns an iterable
      queued via a Later for delivery to the output queue.
  '''

  def __init__(self, name, L, func_push, outQ, func_final=None,
                     on_open=None, on_close=None, on_shutdown=None):
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
        Submit `func_push(item)` via L.defer_iterable() to
          allow a progressive feed to `outQ`.
        Otherwise, submit `func_push` with `item` via L.defer().
    '''
    if name is None:
      name = "%s%d-%s" % (self.__class__.__name__, seq(), func_push)
    self.name = name
    self._lock = Lock()
    O.__init__(self)
    NestingOpenCloseMixin.__init__(self,
                                   on_open=on_open, on_close=on_close, on_shutdown=on_shutdown,
                                   proxy_type=_Q_Proxy)
    self.later = L
    self.func_push = func_push
    self.outQ = outQ
    self.func_final = func_final
    self.LFs = []

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
    if self.all_closed:
      warning("%s.put(%s) when all closed" % (self, item))
    L = self.later
    try:
      items = self.func_push(item)
      ##items = list(items)
    except Exception as e:
      exception("%s.func_push(item=%r): %s", self, item, e)
      items = ()
    # pass a new open-proxy to defer_iterable, as it will close it
    L._defer_iterable(items, self.outQ.open())

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
    # schedule final close of output queue
    self.later._after( LFs, None, self.outQ.close )

  def _run_func_final(self):
    debug("%s._run_func_final()", self)
    outQ = self.outQ
    items = self.func_final()
    for item in items:
      outQ.put(item)

class NullQueue(NestingOpenCloseMixin):
  ''' A queue-like object that discards its inputs.
      Calls to .get() raise Queue_Empty.
  '''

  def __init__(self, blocking=False, name=None):
    ''' Initialise the NullQueue.
        `blocking`: if true, calls to .get() block until .shutdown().
          Its default is False. 
        `name`: a name for this NullQueue.
    '''
    if name is None:
      name = "%s%d" % (self.__class__.__name__, seq())
    self.name = name
    self._lock = Lock()
    O.__init__(self)
    NestingOpenCloseMixin.__init__(self, proxy_type=_Q_Proxy)
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

  def shutdown(self):
    ''' Shut down the queue. Wakes up anything waiting on ._close_cond, such
        as callers of .get() on a .blocking queue.
    '''
    pass

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
