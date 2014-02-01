#!/usr/bin/python -tt
#
# Stuff to do with sequences and iterables.
#       - Cameron Simpson <cs@zip.com.au> 20jul2008
#

import bisect
import unittest
import heapq
import itertools
from threading import Lock, Condition

__seq = 0
__seqLock = Lock()

def seq():
  ''' Allocate a new sequential number.
      Useful for creating unique tokens.
  '''
  global __seq
  global __seqLock
  __seqLock.acquire()
  __seq += 1
  n = __seq
  __seqLock.release()
  return n

def the(list, context=None):
  ''' Returns the first element of an iterable, but requires there to be
      exactly one.
  '''
  icontext="expected exactly one value"
  if context is not None:
    icontext=icontext+" for "+context

  first=True
  for elem in list:
    if first:
      it=elem
      first=False
    else:
      raise IndexError("%s: got more than one element (%s, %s, ...)"
                        % (icontext, it, elem)
                      )

  if first:
    raise IndexError("%s: got no elements" % (icontext,))

  return it

def get0(seq, default=None):
  ''' Return first element of a sequence, or the default.
  '''
  for i in seq:
    return i
  return default

class Range(list):
  def __init__(self,values=(),step=1):
    self.__step=step
    self.__spans=[]
    for v in values:
      try:
        assert len(v) == 2
      except TypeError:
        v=(v,v+step)
      self.add(*v)

  def __str__(self):
    return ", ".join("%d-%d" % (lo, hi) for lo, hi in self)

  def add(self,lo,hi=None):
    if hi is None:
      hi=lo+step
    else:
      assert lo < hi

    ndx=bisect.bisect_left(self,(lo,))
    if ndx > 0 and self[ndx-1][1] >= lo:
      # incorporate left hand range
      ndx-=1
      R=self[ndx]
      lo=min(R[0],lo)
      hi=max(R[1],hi)
      del self[ndx]

    if ndx < len(self):
      # incorporate overlapping ranges
      R=self[ndx]
      while R[0] <= hi:
        hi=max(R[1],hi)
        del self[ndx]
        if ndx == len(self):
          break
        R=self[ndx]

    self.insert(ndx,(lo,hi))

  def remove(self,lo,hi=None):
    if hi is None:
      hi=lo+step
    else:
      assert lo < hi

    ndx=bisect.bisect_left(self,(lo,))
    if ndx < len(self):
      R=self[ndx]
      while R[0] < hi:
        if R[1] <= hi:
          del self[ndx]
          if ndx == len(self):
            break
        else:
          R[0]=max(R[0], hi)
          break
        R=self[ndx]

def NamedTupleClassFactory(*fields):
  ''' Construct classes for named tuples a bit like the named tuples
      coming in Python 2.6/3.0.
      NamedTupleClassFactory('a','b','c') returns a subclass of "list"
      whose instances have properties .a, .b and .c as references to
      elements 0, 1 and 2 respectively.
  '''
  class NamedTuple(list):
    for i in range(len(fields)):
      f=fields[i]
      exec('def getx(self): return self[%d]' % i)
      exec('def setx(self,value): self[%d]=value' % i)
      exec('%s=property(getx,setx)' % f)
  return NamedTuple

def NamedTuple(fields,iter=()):
  ''' Return a named tuple with the specified fields.
      Useful for one-off tuples/lists.
  '''
  return NamedTupleClassFactory(*fields)(iter)

class _MergeHeapItem(tuple):
  def __lt__(self, other):
    return self[0] < other[0]

def imerge(*iters):
  ''' Merge an iterable of ordered iterables in order.
      It relies on the source iterables being ordered and their elements
      being comparable, through slightly misordered iterables (for example,
      as extracted from web server logs) will produce only slightly
      misordered results, as the merging is done on the basis of the front
      elements of each iterable.
  '''
  # prime the list of head elements with (value, iter)
  heap = []
  for I in iters:
    I = iter(I)
    try:
      head = next(I)
    except StopIteration:
      pass
    else:
      heapq.heappush(heap, _MergeHeapItem( (head, I)))
  while heap:
    head, I = heapq.heappop(heap)
    yield head
    try:
      head = next(I)
    except StopIteration:
      pass
    else:
      heapq.heappush(heap, _MergeHeapItem( (head, I)))

def onetoone(func):
  ''' A decorator for a method of a sequence to merge the results of
      passing every element of the sequence to the function, expecting a
      single value back.
      Example:
        class X(list):
          @onetoone
          def lower(self, item):
            return item.lower()
        strs = X(['Abc', 'Def'])
        lower_strs = X.lower()
  '''
  def gather(self, *a, **kw):
    for item in self:
      yield func(item, *a, **kw)
  return gather

def onetomany(func):
  ''' A decorator for a method of a sequence to merge the results of
      passing every element of the sequence to the function, expecting
      multiple values back.
      Example:
        class X(list):
          @onetoone
          def chars(self, item):
            return item
        strs = X(['Abc', 'Def'])
        all_chars = X.chars()
  '''
  def gather(self, *a, **kw):
    return itertools.chain(*[ func(item) for item in self ])
  return gather

class TrackingCounter(object):
  ''' A wrapper for a counter which can be incremented and decremented.
      A facility is provided to wait for the counter to reach a specifi value.
  '''

  def __init__(self, value=0, name=None):
    ''' Initialise the counter to `value` (default 0) with the optional `name`.
    '''
    if name is None:
      name = "TrackingCounter-%d" % (seq(),)
    self.value = value
    self.name = name
    self._lock = Lock()
    self._watched = {}

  def __str__(self):
    return "%s:%d" % (self.name, self.value)

  def __repr__(self):
    return "<TrackingCounter %r %r>" % (str(self), self._watched)

  def __nonzero__(self):
    return self.value != 0

  def _notify(self):
    ''' Notify any waiters on the current counter value.
        This should be called inside self._lock.
    '''
    value = self.value
    watcher = self._watched.get(value)
    if watcher:
      del self._watched[value]
      debug("%s.notify_all(value=%d)...", self, value)
      watcher.acquire()
      watcher.notify_all()
      watcher.release()

  def inc(self):
    ''' Increment the counter.
        Wake up any threads waiting for its new value.
    '''
    with self._lock:
      self.value += 1
      self._notify()

  def dec(self):
    ''' Decrement the counter.
        Wake up any threads waiting for its new value.
    '''
    with self._lock:
      self.value -= 1
      self._notify()

  def wait(self, value):
    ''' Wait for the counter to reach the specified `value`.
    '''
    debug("%s.wait()...", self)
    with self._lock:
      if value == self.value:
        return
      if value not in self._watched:
        watcher = self._watched[value] = Condition()
      else:
        watcher = self._watched[value]
      watcher.acquire()
    debug("%s.wait(): got lock, calling inner wait", self)
    watcher.wait()

if __name__ == '__main__':
  import sys
  import cs.seq_tests
  cs.seq_tests.selftest(sys.argv)
