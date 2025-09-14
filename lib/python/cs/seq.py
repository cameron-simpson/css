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
from threading import Lock, Condition, Thread
from typing import Callable, Hashable, Iterable, Iterator, Optional, Tuple, TypeVar

from cs.deco import decorator
from cs.gimmicks import warning

__version__ = '20250914'

DISTINFO = {
    'description':
    "Stuff to do with counters, sequences and iterables.",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.deco',
        'cs.gimmicks',
    ],
    'python_requires':
    '>=3',
}

class Seq(object):
  ''' A numeric sequence implemented as a thread safe wrapper for
      `itertools.count()`.

      A `Seq` is iterable and both iterating and calling it return
      the next number in the sequence.
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
  __call__ = __next__

__seq = Seq()

def seq():
  ''' Return a new sequential value.
  '''
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
  ''' Return the first item from an iterable; raise `IndexError` on empty iterables.
  '''
  for first in iterable:
    return first
  raise IndexError("empty iterable %r" % (iterable,))

def last(iterable):
  ''' Return the last item from an iterable; raise `IndexError` on empty iterables.
  '''
  nothing = True
  for item in iterable:
    nothing = False
    last = item
  if nothing:
    raise IndexError("no items in iterable: %r" % (iterable,))
  return last  # pylint: disable=undefined-loop-variable

def get0(iterable, default=None):
  ''' Return first element of an iterable, or the default.
  '''
  try:
    i = first(iterable)
  except IndexError:
    return default
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
  for iterable in iters:
    it = iter(iterable)
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
      calls that are typically sequential, specificly the `.read`
      method of file like objects. Naive sequential reads require
      the underlying storage to locate the data on every call, even
      though the previous call has just performed this task for the
      previous read. Saving the iterator used from the preceeding
      call allows the iterator to pick up directly if the file
      offset hasn't been modified in the meantime.
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

class ClonedIterator(Iterable):
  ''' A thread safe clone of some orginal iterator.

      `next()` of this yields the next item from the supplied iterator.
      `iter()` of this returns a generator yielding from the
      historic items and then from the original iterator.

      Note that this accrues all of the items from the original
      iterator in memory.
  '''

  def __init__(self, it: Iterable):
    ''' Initialise the clone with the iterable `it`.
    '''
    self._iterator = iter(it)
    self._cloned = []
    self._lock = Lock()

  def __next__(self):
    ''' Return the next item from the original iterator.
    '''
    with self._lock:
      item = next(self._iterator)
      self._cloned.append(item)
    return item

  def __iter__(self):
    ''' Iterate over the clone, returning a new iterator.

        In mild violation of the iterator protocol, instead of
        returning `self`, `iter(self)` returns a generator yielding
        the historic and then current contents of the original iterator.
    '''
    i = 0
    while True:
      with self._lock:
        try:
          item = self._cloned[i]
        except IndexError:
          try:
            item = next(self._iterator)
          except StopIteration:
            return
          self._cloned.append(item)
      yield item
      i += 1

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

      The default `signature` function is equality;
      the items are stored n `seen` and compared.
      This requires the items to be hashable and support equality tests.
      The same applies to whatever values the `signature` function produces.

      Another common signature is identity: `id`, useful for
      traversing a graph which may have cycles.

      Since `seen` accrues all the signature values for yielded items
      generally it will grow monotonicly as iteration proceeeds.
      If the items are complex or large it is well worth providing a signature
      function even if the items themselves can be used in a set.
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

@decorator
def _greedy_decorator(g, queue_depth=0):

  def greedy_generator(*a, **kw):
    return greedy(g(*a, **kw), queue_depth=queue_depth)

  return greedy_generator

def greedy(g=None, queue_depth=0):
  ''' A decorator or function for greedy computation of iterables.

      If `g` is omitted or callable
      this is a decorator for a generator function
      causing it to compute greedily,
      capacity limited by `queue_depth`.

      If `g` is iterable
      this function dispatches it in a `Thread` to compute greedily,
      capacity limited by `queue_depth`.

      Example with an iterable:

          for packet in greedy(parse_data_stream(stream)):
              ... process packet ...

      which does some readahead of the stream.

      Example as a function decorator:

          @greedy
          def g(n):
              for item in range(n):
                  yield n

      This can also be used directly on an existing iterable:

          for item in greedy(range(n)):
              yield n

      Normally a generator runs on demand.
      This function dispatches a `Thread` to run the iterable
      (typically a generator)
      putting yielded values to a queue
      and returns a new generator yielding from the queue.

      The `queue_depth` parameter specifies the depth of the queue
      and therefore how many values the original generator can compute
      before blocking at the queue's capacity.

      The default `queue_depth` is `0` which creates a `Channel`
      as the queue - a zero storage buffer - which lets the generator
      compute only a single value ahead of time.

      A larger `queue_depth` allocates a `Queue` with that much storage
      allowing the generator to compute as many as `queue_depth+1` values
      ahead of time.

      Here's a comparison of the behaviour:

      Example without `@greedy`
      where the "yield 1" step does not occur until after the "got 0":

          >>> from time import sleep
          >>> def g():
          ...   for i in range(2):
          ...     print("yield", i)
          ...     yield i
          ...   print("g done")
          ...
          >>> G = g(); sleep(0.1)
          >>> for i in G:
          ...   print("got", i)
          ...   sleep(0.1)
          ...
          yield 0
          got 0
          yield 1
          got 1
          g done

      Example with `@greedy`
      where the "yield 1" step computes before the "got 0":

          >>> from time import sleep
          >>> @greedy
          ... def g():
          ...   for i in range(2):
          ...     print("yield", i)
          ...     yield i
          ...   print("g done")
          ...
          >>> G = g(); sleep(0.1)
          yield 0
          >>> for i in G:
          ...   print("got", repr(i))
          ...   sleep(0.1)
          ...
          yield 1
          got 0
          g done
          got 1

      Example with `@greedy(queue_depth=1)`
      where the "yield 1" step computes before the "got 0":

          >>> from cs.x import X
          >>> from time import sleep
          >>> @greedy
          ... def g():
          ...   for i in range(3):
          ...     X("Y")
          ...     print("yield", i)
          ...     yield i
          ...   print("g done")
          ...
          >>> G = g(); sleep(2)
          yield 0
          yield 1
          >>> for i in G:
          ...   print("got", repr(i))
          ...   sleep(0.1)
          ...
          yield 2
          got 0
          yield 3
          got 1
          g done
          got 2

  '''
  assert queue_depth >= 0

  if g is None:
    # the parameterised @greedy(queue_depth=n) form
    # pylint: disable=no-value-for-parameter
    return _greedy_decorator(queue_depth=queue_depth)

  if callable(g):
    # the direct @greedy form
    return _greedy_decorator(g, queue_depth=queue_depth)

  # presumably an iterator - dispatch it in a Thread
  try:
    it = iter(g)
  except TypeError as e:
    # pylint: disable=raise-missing-from
    raise TypeError("g=%r: neither callable nor iterable: %s" % (g, e))

  # pylint: disable=import-outside-toplevel
  from cs.queues import Channel, IterableQueue
  if queue_depth == 0:
    q = Channel()
  else:
    q = IterableQueue(queue_depth)

  def run_generator():
    ''' Thread body for greedy generator.
    '''
    try:
      for item in it:
        q.put(item)
    finally:
      q.close()

  Thread(target=run_generator).start()
  return iter(q)

def skip_map(func, *iterables, except_types, quiet=False):
  ''' A version of `map()` which will skip items where `func(item)`
      raises an exception in `except_types`, a tuple of exception types.
      If a skipped exception occurs a warning will be issued unless
      `quiet` is true (default `False`).
  '''
  if not isinstance(except_types, tuple):
    raise TypeError(
        "except types must be a tuple of exception types but has type %s" %
        (type(except_types),)
    )
  for iterable in iterables:
    for item in iterable:
      try:
        yield func(item)
      except except_types as e:
        quiet or warning(
            "skip_map(func=%s): item=%s: skip exception: %s", func, item, e
        )

# infill object generic type
_infill_T = TypeVar('_infill_T')
# infill object key generic type
_infill_K = TypeVar('_infill_K', bound=Hashable)

def infill(
    objs: Iterable[_infill_T],
    *,
    obj_keys: Callable[[_infill_T], _infill_K],
    existing_keys: Callable[[_infill_T], _infill_K],
    all: Optional[bool] = False,
) -> Iterable[Tuple[_infill_T, _infill_K]]:
  ''' A generator accepting an iterable of objects
      which yields `(obj,missing_keys)` 2-tuples
      indicating missing records requiring infill for each object.

      Parameters:
      * `objs`: an iterable of objects
      * `obj_keys`: a callable accepting an object and returning
        an iterable of the expected keys
      * `existsing_keys`: a callable accepting an object and returning
        an iterable of the existing keys
      * `all`: optional flag, default `False`: if true then yield
        `(obj,())` for objects with no missing records

      Example:

          for obj, missing_key in infill(objs,...):
            ... infill a record for missing_key ...
  '''
  for obj in objs:
    required = set(obj_keys(obj))
    if not required:
      if all:
        yield obj, ()
      continue
    existing = set(existing_keys(obj))
    missing = required - existing
    if all or missing:
      yield obj, missing

def infill_from_batches(
    objss: Iterable[Iterable[_infill_T]],
    *,
    obj_keys: Callable[[_infill_T], _infill_K],
    existing_keys: Callable[[_infill_T], _infill_K],
    all: Optional[bool] = False,
    amend_batch: Optional[Callable[
        [Iterable[_infill_T]],
        Iterable[_infill_T],
    ]] = lambda obj_batch: obj_batch,
):
  ''' A batched version of `infill(objs)` accepting an iterable of
      batches of objects which yields `(obj,obj_key)` 2-tuples
      indicating missing records requiring infill for each object.

      This is aimed at processing batches of objects where it is
      more efficient to prepare each batch as a whole, such as a
      Django `QuerySet` which lets the caller make single database
      queries for a batch of `Model` instances.
      Thus this function can be used with `cs.djutils.model_batches_qs`
      for more efficient infill processing.

      Parameters:
      * `objss`: an iterable of iterables of objects
      * `obj_keys`: a callable accepting an object and returning
        an iterable of the expected keys
      * `existsing_keys`: a callable accepting an object and returning
        an iterable of the existing keys
      * `all`: optional flag, default `False`: if true then yield
        `(obj,())` for objects with no missing records
      * `amend_batch`: optional callable to amend the batch of objects,
        for example to amend a `QuerySet` with `.select_related()` or similar
  '''
  for objs in map(amend_batch, objss):
    yield from infill(
        objs, obj_keys=obj_keys, existing_keys=existing_keys, all=all
    )

if __name__ == '__main__':
  import sys
  import cs.seq_tests
  cs.seq_tests.selftest(sys.argv)
