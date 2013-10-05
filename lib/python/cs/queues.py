#!/usr/bin/python
#
# Some Queue subclasses and ducktypes.
#       - Cameron Simpson <cs@zip.com.au>
#

from cs.debug import Lock, RLock, Thread
from cs.py3 import Queue, PriorityQueue, Queue_Full, Queue_Empty
from cs.obj import O

class QueueIterator(O):

  sentinel = object()

  def __init__(self, q):
    O.__init__(self, q=q)
    self.closed = False
    self.opens = 0

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
      raise Queue_Full("queue closed")
    if item is self.sentinel:
      raise ValueError("put(sentinel)")
    return self.q.put(item, *args, **kw)

  def open(self):
    self.opens += 1

  def close(self):
    if self.closed:
      error("queue already closed")
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
      raise StopIteration
    return item

  next = __next__

def IterableQueue(*args, **kw):
  return QueueIterator(Queue(*args, **kw))

def IterablePriorityQueue(*args, **kw):
  return QueueIterator(PriorityQueue(*args, **kw))

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
