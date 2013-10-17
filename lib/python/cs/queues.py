#!/usr/bin/python
#
# Some Queue subclasses and ducktypes.
#       - Cameron Simpson <cs@zip.com.au>
#

from cs.debug import Lock, RLock, Thread, trace_caller
from cs.excutils import noexc
from cs.logutils import exception, warning, D
from cs.seq import seq
from cs.py3 import Queue, PriorityQueue, Queue_Full, Queue_Empty
from cs.obj import O

class QueueIterator(O):

  sentinel = object()

  def __init__(self, q, name=None):
    O.__init__(self, q=q)
    if name is None:
      name = "QueueIterator-%d" % (seq(),)
    self.name = name
    self.closed = False
    self.opens = 0

  def __str__(self):
    return "<%s:opens=%d,closed=%s>" % (self.name, self.opens, self.closed)

  def __getattr__(self, attr):
    return getattr(self.q, attr)

  def get(self, *a):
    q = self.q
    item = q.get(*a)
    if item is self.sentinel:
      q.put(self.sentinel)
      raise Queue_Empty
    return item

  def get_nowait(self):
    q = self.q
    item = q.get_nowait()
    if item is self.sentinel:
      q.put(self.sentinel)
      raise Queue_Empty
    return item

  def put(self, item, *args, **kw):
    ''' Put an item on the queue.
    '''
    if self.closed:
      warning("queue closed: item=%s", item)
      ##raise Queue_Full("queue closed")
    if item is self.sentinel:
      raise ValueError("put(sentinel)")
    return self.q.put(item, *args, **kw)

  ##@trace_caller
  def open(self):
    self.opens += 1

  ##@trace_caller
  def close(self):
    if self.closed:
      # TODO: possibly an error, must debug sometime
      warning("%s.close: already closed", self)
    else:
      self.opens -= 1
      if self.opens < 1:
        self.closed = True
        self.q.put(self.sentinel)

  def __iter__(self):
    ''' Iterable interface for the queue.
    '''
    return self

  def __next__(self):
    try:
      item = self.get()
    except Queue_Empty:
      ##D("IQ %s.__next__: Queue_Empty, STOPPING", self)
      raise StopIteration
    ##D("IQ %s.__next__: item=%s", self, item)
    return item

  next = __next__

def IterableQueue(capacity=0, name=None, *args, **kw):
  name = kw.pop('name', name)
  return QueueIterator(Queue(capacity, *args, **kw), name=name)

def IterablePriorityQueue(capacity=0, name=None, *args, **kw):
  name = kw.pop('name', name)
  return QueueIterator(PriorityQueue(capacity, *args, **kw), name=name)

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

class PushQueue(O):
  ''' A puttable object to look like a Queue.
      Calling .put(item) calls a pushfunc supplied at initialisation to
      trigger a function on data arrival.
  '''

  def __init__(self, L, pushfunc, outQ, close_func=None, is_iterable=False, name=None):
    ''' Initialise the PushQueue with the Later `L`, callable
	`pushfunc` and the output queue `outQ`.
	`pushfunc` is a one-to-many function which accepts a single
	  item of input and returns an iterable of outputs; it may
	  be a generator.
        `outQ` accepts results from the callable via its .put() method.
        `closefunc`, if specified an not None, is called after completion of
          all calls to `pushfunc`.
        If `is_iterable``, submit `pushfunc(item)` via L.defer_iterable() to
          allow a progressive feed to `outQ`.
        Otherwise, submit `pushfunc` with `item` via L.defer().
    '''
    O.__init__(self)
    if name is None:
      name = "%s-%d" % (self.__class__.__name__, seq())
    self.name = name
    self.later = L
    self.func = pushfunc
    self.outQ = outQ
    self.is_iterable = is_iterable
    self.opens = 0
    self.closed = False
    self.LFs = []

  def __str__(self):
    return "PQ<%s>" % (self.name,)

  def put(self, item):
    ''' Receive a new item.
        If self.func returns an iterator, submit via defer_iterable.
        Otherwise, submit self.func(item) and after completion,
        queue its results to outQ.
    '''
    if self.closed:
      warning("%s.put(%s) when closed" % (self, item))
    L = self.later
    if self.is_iterable:
      # add to the outQ opens; defer_iterable will close it
      L.defer_iterable( self.func(item), self.outQ )
    else:
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

  ##@trace_caller
  def open(self):
    self.opens += 1

  ##@trace_caller
  def close(self):
    self.opens -= 1
    if self.opens < 1:
      self.closed = True
      LFs = self.LFs
      if self.closefunc:
        # run closefunc to completion before closing outQ
        LFclose = self.later.after( LFs, None, self.closefunc )
        LFs = (LFclose,)
      self.later.after( LFs, None, self.outQ.close )
