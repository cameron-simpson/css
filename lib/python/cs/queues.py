#!/usr/bin/python
#
# Some Queue subclasses and ducktypes.
#       - Cameron Simpson <cs@zip.com.au>
#

from threading import Condition
import time
from cs.debug import Lock, RLock, Thread, trace_caller
from cs.excutils import noexc
from cs.logutils import exception, warning, D, PfxCallInfo
from cs.seq import seq
from cs.py3 import Queue, PriorityQueue, Queue_Full, Queue_Empty
from cs.obj import O

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

  def __init__(self, open=False):
    ''' Initialise the NestingOpenCloseMixin state.
	If the optional parameter `open` is true, return the object in "open"
        state (active opens == 1) otherwise closed (opens == 0).
        The default is "closed" to optimise use as a context manager;
        the __enter__ method will open the object.
    '''
    self._opens = 0
    if open:
      self.open()

  def open(self):
    ''' Increment the open count.
    '''
    with self._lock:
      self._opens += 1

  def __enter__(self):
    self.open()
    return self

  def close(self):
    ''' Decrement the open count.
        If the count goes to zero, call self.shutdown().
    '''
    with self._lock:
      count = self._opens
      count -= 1
      self._opens = count
    if count == 0:
      self.shutdown()

  @property
  def closed(self):
    if self._opens > 0:
      return False
    if self._opens < 0:
      with PfxCallInfo():
        warning("%r._opens < 0: %r", self, self._opens)
    return True

  def __exit__(self, exc_type, exc_value, traceback):
    self.close()
    return False

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
    NestingOpenCloseMixin.__init__(self, open=open)

  def __str__(self):
    return "<%s:opens=%d,closed=%s>" % (self.name, self._opens, self.closed)

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

  def put(self, item, *args, **kw):
    ''' Put `item` onto the queue.
        Warn if the queue is closed.
        Reject if `item` is the sentinel.
    '''
    if self.closed:
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
    if self.closed and q.empty():
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

  def get(self):
    ''' Read a value from the Channel.
        Blocks until someone put()s to the Channel.
    '''
    if self.closed:
      raise RuntimeError("%s: closed", self)
    # allow a writer to proceed
    self.__writable.release()
    # await a writer
    self.__readable.acquire()
    self.close()
    value = self._value
    delattr(self,'_value')
    return value

  def put(self, value):
    ''' Write a value to the Channel.
        Blocks until a corresponding get() occurs.
    '''
    if self.closed:
      raise RuntimeError("%s: closed", self)
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

  def __init__(self, L, func_push, outQ, func_final=None, is_iterable=False, name=None, open=False):
    ''' Initialise the PushQueue with the Later `L`, the callable `func_push`
        and the output queue `outQ`.
	`func_push` is a one-to-many function which accepts a single
	  item of input and returns an iterable of outputs; it may
	  be a generator.
        `outQ` accepts results from the callable via its .put() method.
        `func_final`, if specified and not None, is called after completion of
          all calls to `func_push`.
        If `is_iterable``, submit `func_push(item)` via L.defer_iterable() to
          allow a progressive feed to `outQ`.
        Otherwise, submit `func_push` with `item` via L.defer().
    '''
    if name is None:
      name = "%s%d-%s" % (self.__class__.__name__, seq(), func_push.__name__)
    self.name = name
    self._lock = Lock()
    O.__init__(self)
    NestingOpenCloseMixin.__init__(self, open=open)
    self.later = L
    self.func_push = func_push
    self.outQ = outQ
    self.func_final = func_final
    self.is_iterable = is_iterable
    self.LFs = []

  def __str__(self):
    return "<%s>" % (self.name,)

  def put(self, item):
    ''' Receive a new item.
	If self.is_iterable then presume that self.func_push returns
	an iterator and submit self.func_push(item) to defer_iterable.
        Otherwise, defer self.func_push(item) and after completion,
        queue its results to outQ.
    '''
    if self.closed:
      warning("%s.put(%s) when closed" % (self, item))
    L = self.later
    self.outQ.open()
    if self.is_iterable:
      # add to the outQ opens; defer_iterable will close it
      items = self.func_push(item)
      items = list(items)
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
    try:
      for item in LF():
        self.outQ.put(item)
    except Exception as e:
      exception("%s._push_items: exception putting results of LF(): %s", self, e)
    self.outQ.close()

  def shutdown(self):
    ''' shutdown() is called by NestingOpenCloseMixin.close() to close
        the outQ for real.
    '''
    LFs = self.LFs
    self.LFs = []
    if self.func_final:
      # run func_final to completion before closing outQ
      LFclose = self.later._after( LFs, None, self._run_func_final )
      LFs = (LFclose,)
    self.later._after( LFs, None, self.outQ.close )

  def _run_func_final(self):
    items = list(self.func_final())
    outQ = self.outQ
    for item in items:
      outQ.put(item)

class NullQueue(NestingOpenCloseMixin, O):
  ''' A queue-like object that discards its inputs.
      Calls to .get() raise Queue_Empty.
  '''

  def __init__(self, blocking=False, name=None, open=False):
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
    NestingOpenCloseMixin.__init__(self, open=open)
    self.blocking = blocking

  def put(self, item):
    ''' Put a value onto the Queue; it is discarded.
    '''
    pass

  def get(self):
    ''' Get the next value. Always raises Queue_Empty.
        If .blocking, delay until .shutdown().
    '''
    if self.blocking:
      with self._lock:
        if not self.closed:
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
