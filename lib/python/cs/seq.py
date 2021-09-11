#!/usr/bin/python -tt
#
# Stuff to do with counters, sequences and iterables.
#       - Cameron Simpson <cs@cskk.id.au> 20jul2008
#

r'''
Stuff to do with counters, sequences and iterables.

Note that any function accepting an iterable
will consume some or all of the derived iterator
in the course of its function.
'''

import heapq
import itertools
from threading import Lock, Condition
from cs.gimmicks import warning

__version__ = '20201025-post'

DISTINFO = {
    'description':
    "Stuff to do with counters, sequences and iterables.",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': ['cs.gimmicks'],
}

class Seq(object):
  ''' A thread safe wrapper for itertools.count().
  '''

  __slots__ = ('counter', '_lock')

  def __init__(self, start=0, lock=None):
    if lock is None:
      lock = Lock()
    self.counter = itertools.count(start)
    self._lock = lock

  def __iter__(self):
    return self

  def __next__(self):
    with self._lock:
      return next(self.counter)

  next = __next__

__seq = Seq()

def seq():
  ''' Return a new sequential value.
  '''
  global __seq  # pylint: disable=global-statement
  return next(__seq)

def the(iterable, context=None):
  ''' Returns the first element of an iterable, but requires there to be
      exactly one.
  '''
  icontext = "expected exactly one value"
  if context is not None:
    icontext = icontext + " for " + context
  is_first = True
  for elem in iterable:
    if is_first:
      it = elem
      is_first = False
    else:
      raise IndexError(
          "%s: got more than one element (%s, %s, ...)" % (icontext, it, elem)
      )
  if is_first:
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
  return item  # pylint: disable=undefined-loop-variable

def get0(iterable, default=None):
  ''' Return first element of an iterable, or the default.
  '''
  try:
    i = first(iterable)
  except IndexError:
    return default
  else:
    return i

def tee(iterable, *Qs):
  ''' A generator yielding the items from an iterable
      which also copies those items to a series of queues.

      Parameters:
      * `iterable`: the iterable to copy
      * `Qs`: the queues, objects accepting a `.put` method.

      Note: the item is `.put` onto every queue
      before being yielded from this generator.
  '''
  for item in iterable:
    for Q in Qs:
      Q.put(item)
    yield item

def imerge(*iters, **kw):
  ''' Merge an iterable of ordered iterables in order.

      Parameters:
      * `iters`: an iterable of iterators
      * `reverse`: keyword parameter: if true, yield items in reverse order.
        This requires the iterables themselves to also be in
        reversed order.

      This function relies on the source iterables being ordered
      and their elements being comparable, through slightly misordered
      iterables (for example, as extracted from web server logs)
      will produce only slightly misordered results, as the merging
      is done on the basis of the front elements of each iterable.
  '''
  reverse = kw.get('reverse', False)
  if kw:
    raise ValueError("unexpected keyword arguments: %r" % (kw,))
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
  for it in iters:
    it = iter(it)
    try:
      head = next(it)
    except StopIteration:
      pass
    else:
      heapq.heappush(heap, _MergeHeapItem((head, it)))
  while heap:
    head, it = heapq.heappop(heap)
    yield head
    try:
      head = next(it)
    except StopIteration:
      pass
    else:
      heapq.heappush(heap, _MergeHeapItem((head, it)))

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
    ''' Yield the results of calling the function on each item.
    '''
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
    ''' Chain the function results together.
    '''
    return itertools.chain(*[func(item, *a, **kw) for item in self])

  return gather

def isordered(items, reverse=False, strict=False):
  ''' Test whether an iterable is ordered.
      Note that the iterable is iterated, so this is a destructive
      test for nonsequences.
  '''
  is_first = True
  prev = None
  for item in items:
    if not is_first:
      if reverse:
        ordered = item < prev if strict else item <= prev
      else:
        ordered = item > prev if strict else item >= prev
      if not ordered:
        return False
    prev = item
    is_first = False
  return True

def common_prefix_length(*seqs):
  ''' Return the length of the common prefix of sequences `seqs`.
  '''
  if not seqs:
    return 0
  if len(seqs) == 1:
    return len(seqs[0])
  for i, items in enumerate(zip(*seqs)):
    item0 = items[0]
    # pylint: disable=cell-var-from-loop
    if not all(map(lambda item: item == item0, items)):
      return i
  # return the length of the shorted sequence
  return len(min(*seqs, key=len))

def common_suffix_length(*seqs):
  ''' Return the length of the common suffix of sequences `seqs`.
  '''
  return common_prefix_length(list(map(lambda s: list(reversed(s)), *seqs)))

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
    with self._lock:
      self.value -= 1
      if tag is not None:
        tag = str(tag)
        self._tag_down.setdefault(tag, 0)
        self._tag_down[tag] += 1
        if self._tag_up.get(tag, 0) < self._tag_down[tag]:
          warning("%s.dec: more .decs than .incs for tag %r", self, tag)
      if self.value < 0:
        warning("%s.dec: value < 0!", self)
      self._notify()

  def check(self):
    ''' Internal consistency check.
    '''
    for tag in sorted(self._tag_up.keys()):
      ups = self._tag_up[tag]
      downs = self._tag_down.get(tag, 0)
      if ups != downs:
        warning("%s: ups=%d, downs=%d: tag %r", self, ups, downs, tag)

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

class StatefulIterator(object):
  ''' A trivial iterator which wraps another iterator to expose some tracking state.

      This has 2 attributes:
      * `.it`: the internal iterator which should yield `(item,new_state)`
      * `.state`: the last state value from the internal iterator

      The originating use case is resuse of an iterator by independent
      calls that are typically sequential, specificly the .read
      method of file like objects. Naive sequential reads require
      the underlying storage to locate the data on every call, even
      though the previous call has just performed this task for the
      previous read. Saving the iterator used from the preceeding
      call allows the iterator to pick up directly if the file
      offset hasn't been fiddled in the meantime.
  '''

  def __init__(self, it):
    self.it = it
    self.state = None

  def __iter__(self):
    return self

  def __next__(self):
    item, new_state = next(self.it)
    self.state = new_state
    return item

def splitoff(sq, *sizes):
  ''' Split a sequence into (usually short) prefixes and a tail,
      for example to construct subdirectory trees based on a UUID.

      Example:

          >>> from uuid import UUID
          >>> uuid = 'd6d9c510-785c-468c-9aa4-b7bda343fb79'
          >>> uu = UUID(uuid).hex
          >>> uu
          'd6d9c510785c468c9aa4b7bda343fb79'
          >>> splitoff(uu, 2, 2)
          ['d6', 'd9', 'c510785c468c9aa4b7bda343fb79']
  '''
  if len(sizes) < 1:
    raise ValueError("no sizes")
  offset = 0
  parts = []
  for size in sizes:
    if size < 1:
      raise ValueError("size:%s < 1" % (size,))
    end_offset = offset + size
    if end_offset >= len(sq):
      raise ValueError(
          "size:%s consumes up to or beyond"
          " the end of the sequence (length %d)" % (size, len(sq))
      )
    parts.append(sq[offset:end_offset])
    offset = end_offset
  parts.append(sq[offset:])
  return parts

def unrepeated(it, seen=None, signature=None):
  ''' A generator yielding items from the iterable `it` with no repetitions.

      Parameters:
      * `it`: the iterable to process
      * `seen`: an optional setlike container supporting `in` and `.add()`
      * `signature`: an optional signature function for items from `it`
        which produces the value to compare to recognise repeated items;
        its values are stored in the `seen` set

      The default `signature` function is identity - items are stored and compared.
      This requires the items to be hashable and support equality tests.
      The same applies to whatever values the `signature` function produces.

      Since `seen` accrues all the signature values for yielded items
      generally it will grow monotonicly as iteration proceeeds.
      If the items are complaex or large it is well worth providing a signature
      function even it the items themselves can be used in a set.
  '''
  if seen is None:
    seen = set()
  if signature is None:
    signature = lambda item: item
  for item in it:
    sig = signature(item)
    if sig in seen:
      continue
    seen.add(sig)
    yield item

if __name__ == '__main__':
  import sys
  import cs.seq_tests
  cs.seq_tests.selftest(sys.argv)
