#!/usr/bin/python
#
# Some Queue subclasses and ducktypes.
#       - Cameron Simpson <cs@zip.com.au>
#

from cs.py3 import Queue, PriorityQueue, Queue_Full, Queue_Empty

class IterableQueue(Queue):
  ''' A Queue implementing the iterator protocol.
      Note: Iteration stops when the sentinel comes off the Queue.
  '''

  sentinel = object()

  def __init__(self, *args, **kw):
    ''' Initialise the queue.
    '''
    Queue.__init__(self, *args, **kw)
    self.closed = False

  def get(self, *a):
    item = Queue.get(self, *a)
    if item is self.sentinel:
      Queue.put(self, self.sentinel)
      raise Queue_Empty
    return item

  def get_nowait(self):
    item = Queue.get_nowait(self)
    if item is self.sentinel:
      Queue.put(self, self.sentinel)
      raise Queue_Empty
    return item

  def put(self, item, *args, **kw):
    ''' Put an item on the queue.
    '''
    if self.closed:
      raise Queue_Full("put() on closed IterableQueue")
    if item is self.sentinel:
      raise ValueError("put(sentinel) on IterableQueue")
    return Queue.put(self, item, *args, **kw)

  def _closeAtExit(self):
    if not self.closed:
      self.close()

  def close(self):
    if self.closed:
      error("close() on closed IterableQueue")
    else:
      self.closed = True
      Queue.put(self, self.sentinel)

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

class IterablePriorityQueue(PriorityQueue):
  ''' A PriorityQueue implementing the iterator protocol.
      Note: Iteration stops when a None comes off the Queue.
      TODO: supply sentinel item, default None.
  '''

  sentinel = object()

  def __init__(self, *args, **kw):
    ''' Initialise the queue.
    '''
    PriorityQueue.__init__(self, *args, **kw)
    self.closed = False

  def put(self, item, *args, **kw):
    ''' Put an item on the queue.
    '''
    if self.closed:
      raise Queue_Full("put() on closed IterablePriorityQueue")
    if item is self.sentinel:
      raise ValueError("put(sentinel) on IterablePriorityQueue")
    return PriorityQueue.put(self, item, *args, **kw)

  def _closeAtExit(self):
    if not self.closed:
      self.close()

  def close(self):
    if self.closed:
      error("close() on closed IterablePriorityQueue")
    else:
      self.closed=True
      PriorityQueue.put(self, self.sentinel)

  def __iter__(self):
    ''' Iterable interface for the queue.
    '''
    return self

  def __next__(self):
    item = self.get()
    if item is self.sentinel:
      PriorityQueue.put(self, self.sentinel)      # for another iterator
      raise StopIteration
    return item

  next = __next__
