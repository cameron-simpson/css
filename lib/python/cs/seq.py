#!/usr/bin/python -tt
#
# Stuff to do with counters, sequences and iterables.
#       - Cameron Simpson <cs@zip.com.au> 20jul2008
#

DISTINFO = {
    'description': "Stuff to do with counters, sequences and iterables.",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
        ],
    'requires': ['cs.logutils', 'cs.py.stack'],
}

import heapq
import itertools
from threading import Lock, Condition
from cs.logutils import warning, debug, D
from cs.py.stack import caller
from cs.py3 import exec_code

class Seq(object):
  ''' A thread safe wrapper for itertools.count().
  '''

  __slots__ = ('counter', '_lock')

  def __init__(self, start=0):
    self.counter = itertools.count(start)
    self._lock = Lock()

  def __iter__(self):
    return self

  def __next__(self):
    with self._lock:
      return next(self.counter)

  next = __next__

__seq = Seq()

def seq():
  global __seq
  return next(__seq)

def the(iterable, context=None):
  ''' Returns the first element of an iterable, but requires there to be
      exactly one.
  '''
  icontext="expected exactly one value"
  if context is not None:
    icontext=icontext+" for "+context

  first=True
  for elem in iterable:
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

def first(iterable):
  ''' Return the first item from an iterable; raise IndexError on empty iterables.
  '''
  for i in iterable:
    return i
  raise IndexError("empty iterable %r" % (iterable,))

def last(iterable):
  ''' Return the last item from an iterable; raise IndexError on empty iterables.
  '''
  nothing = True
  for item in iterable:
    nothing = False
  if nothing:
    raise IndexError("no items in iterable: %r" % (iterable,))
  return item

def get0(iterable, default=None):
  ''' Return first element of an iterable, or the default.
  '''
  try:
    i = first(iterable)
  except IndexError:
    return default
  else:
    return i

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
      exec_code('def getx(self): return self[%d]' % i)
      exec_code('def setx(self,value): self[%d]=value' % i)
      exec_code('%s=property(getx,setx)' % f)
  return NamedTuple

def NamedTuple(fields,iter=()):
  ''' Return a named tuple with the specified fields.
      Useful for one-off tuples/lists.
  '''
  return NamedTupleClassFactory(*fields)(iter)

def imerge(*iters, reverse=False):
  ''' Merge an iterable of ordered iterables in order.
      `reverse`: if true, yield items in reverse order
                 this requires the iterables themselves to also be in
                 reversed order
      It relies on the source iterables being ordered and their elements
      being comparable, through slightly misordered iterables (for example,
      as extracted from web server logs) will produce only slightly
      misordered results, as the merging is done on the basis of the front
      elements of each iterable.
  '''
  if reverse:
    # tuples that compare in reverse order
    class _MergeHeapItem(tuple):
      def __lt__(self, other):
        return self[0] > other[0]
  else:
    # tuples that compare in forward order
    class _MergeHeapItem(tuple):
      def __lt__(self, other):
        return self[0] < other[0]
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
          @onetomany
          def chars(self, item):
            return item
        strs = X(['Abc', 'Def'])
        all_chars = X.chars()
  '''
  def gather(self, *a, **kw):
    return itertools.chain(*[ func(item) for item in self ])
  return gather

def isordered(s, reverse=False, strict=False):
  first = True
  for i, item in enumerate(s):
    if not first:
      if reverse:
        ordered = item < prev if strict else item <= prev
      else:
        ordered = item > prev if strict else item >= prev
      if not ordered:
        raise AssertionError(
                "isordered(reverse=%s,strict=%s): s[%d],s[%d] out of order: %s <=> %s"
                % (reverse, strict, i-1, i, prev, item))
    prev = item
    first = False

class TrackingCounter(object):
  ''' A wrapper for a counter which can be incremented and decremented.
      A facility is provided to wait for the counter to reach a specific value.
      The .inc and .dec methods also accept a `tag` argument to keep
      individual counts based on the tag to aid debugging.
      TODO: add `strict` option to error and abort if any counter tries
      to go below zero.
  '''

  def __init__(self, value=0, name=None, lock=None):
    ''' Initialise the counter to `value` (default 0) with the optional `name`.
    '''
    if name is None:
      name = "TrackingCounter-%d" % (seq(),)
    if lock is None:
      lock = Lock()
    self.value = value
    self.name = name
    self._lock = lock
    self._watched = {}
    self._tag_up = {}
    self._tag_down = {}

  def __str__(self):
    return "%s:%d" % (self.name, self.value)

  def __repr__(self):
    return "<TrackingCounter %r:%r>" % (str(self), self._watched)

  def __nonzero__(self):
    return self.value != 0

  def __int__(self):
    return self.value

  def _notify(self):
    ''' Notify any waiters on the current counter value.
        This should be called inside self._lock.
    '''
    value = self.value
    watcher = self._watched.get(value)
    if watcher:
      del self._watched[value]
      watcher.acquire()
      watcher.notify_all()
      watcher.release()

  def inc(self, tag=None):
    ''' Increment the counter.
        Wake up any threads waiting for its new value.
    '''
    if tag:
      D("INC(%s): %s", tag[:10], caller())
    with self._lock:
      self.value += 1
      if tag is not None:
        tag = str(tag)
        self._tag_up.setdefault(tag, 0)
        self._tag_up[tag] += 1
      self._notify()

  def dec(self, tag=None):
    ''' Decrement the counter.
        Wake up any threads waiting for its new value.
    '''
    if tag:
      D("DEC(%s): %s:", tag[:10], caller())
    with self._lock:
      self.value -= 1
      if tag is not None:
        tag = str(tag)
        self._tag_down.setdefault(tag, 0)
        self._tag_down[tag] += 1
        if self._tag_up.get(tag, 0) < self._tag_down[tag]:
          warning("%s.dec: more .decs than .incs for tag %r", self, tag)
          ##raise RuntimeError
      if self.value < 0:
        warning("%s.dec: value < 0!", self)
      elif self.value == 0:
        D("ZERO HERE")
        ##from time import sleep
        ##sleep(3)
        ##raise RuntimeError("ZERO HERE!")
      self._notify()

  def check(self):
    for tag in sorted(self._tag_up.keys()):
      ups = self._tag_up[tag]
      downs = self._tag_down.get(tag, 0)
      if ups != downs:
        D("%s: ups=%d, downs=%d: tag %r", self, ups, downs, tag)

  def wait(self, value):
    ''' Wait for the counter to reach the specified `value`.
    '''
    with self._lock:
      if value == self.value:
        return
      if value not in self._watched:
        watcher = self._watched[value] = Condition()
      else:
        watcher = self._watched[value]
      watcher.acquire()
    watcher.wait()

if __name__ == '__main__':
  import sys
  import cs.seq_tests
  cs.seq_tests.selftest(sys.argv)
