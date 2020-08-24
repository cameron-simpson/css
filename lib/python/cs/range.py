#!/usr/bin/python
#
'''
A Range is an object resembling a set but optimised for contiguous
ranges of int members.
'''

from __future__ import print_function
import sys
from bisect import bisect_left
from collections import namedtuple
from cs.logutils import ifdebug
from cs.seq import first

DISTINFO = {
    'description':
        "a Range class implementing compact integer ranges with a set-like API,"
        " and associated functions",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': ['cs.logutils', 'cs.seq'],
}

def overlap(span1, span2):
  ''' Return a list `[start,end]` denoting the overlap of two spans.

      Example:

          >>> overlap([1,9], [5,13])
          [5, 9]
  '''
  # no item lower than either lows
  lo = max(span1[0], span2[0])
  # no item higher than either high
  hi = min(span1[1], span2[1])
  # hi must be >= lo
  return [lo, max(lo, hi)]

def spans(items):
  ''' Return an iterable of `Spans` for all contiguous sequences in
      `items`.

      Example:

          >>> list(spans([1,2,3,7,8,11,5]))
          [1:4, 7:9, 11:12, 5:6]
  '''
  # see if the object has a .spans() method
  try:
    item_spans = items.spans
  except AttributeError:
    # fall through to span locator below
    pass
  else:
    # trust the .spans() method
    for span in item_spans:
      yield Span(*span)
    return

  # otherwise compute by examination
  prev = None
  for i in items:
    if prev is None:
      prev = [i, i+1]
    elif i == prev[1]:
      prev[1] += 1
    else:
      yield Span(*prev)
      prev = [i, i+1]
  if prev is not None:
    yield Span(*prev)

class Span(namedtuple('Span', 'start end')):
  ''' A namedtuple with `.start` and `.end` attributes.
  '''

  def __str__(self):
    return "%d:%d" % (self.start, self.end)
  __repr__ = __str__
  def __eq__(self, other):
    return self[0] == other[0] and self[1] == other[1]
  def __lt__(self, other):
    return self[0] < other[0] or (self[0] == other[0] and self[1] < other[1])
  def __len__(self):
    return self.end - self.start
  @property
  def size(self):
    ''' The `.size` of a `Span` is its length: `end - start`.
    '''
    return len(self)

class Range(object):
  ''' A collection of ints that collates adjacent ints.

      The interface is as for a `set` with additional methods:
      * `spans()`: return an iterable of `Spans`, with `.start`
        included in each `Span` and `.end` just beyond

      Additionally, the update/remove/etc methods have a secondary
      calling signature: `(start,end)`, which is the same as passing
      in `Range(start,end)` but much more efficient.
  '''

  def __init__(self, start=None, end=None, debug=None):
    ''' Initialise the Range.

        Called with `start` and `end`, these specify the initial
        `Span` of the `Range`.
        If called with just one argument that argument instead be an iterable
        of integer values comprising the values in the `Range`.
    '''
    if debug is None:
      debug = ifdebug()
    self._debug = debug
    self._spans = []
    if start is not None:
      if end is None:
        # "start" must be an iterable of ints
        for substart, subend in spans(start):
          self.add_span(substart, subend)
      else:
        self.add_span(start, end)

  def __str__(self):
    return "[%s]" % (",".join( [ "[%d:%d)" % (S.start, S.end) for S in self._spans ] ))
    ##spans = [ "%d"%(start,) if start == end-1
    ##          else "%d,%d"%(start,end-1) if start == end-2
    ##          else "%d..%d"%(start,end-1) for start, end in self._spans ]
    ##return "[%s]" % (",".join(spans))

  def __eq__(self, other):
    if isinstance(other, Range):
      return self._spans == other._spans
    return list(self) == list(other)

  def __ne__(self, other):
    return not self == other

  __hash__ = None

  def clear(self):
    ''' Clear the `Range`: remove all elements.
    '''
    self._spans = []

  def spans(self):
    ''' Return an iterable of `Spans` covering the `Range`.
    '''
    for span in self._spans:
      yield Span(*span)

  @property
  def span0(self):
    ''' Return the first `Span`; raises `IndexError` if there are no spans.
    '''
    return first(self.spans())

  def _check(self):
    self._check_spans()

  def _check_spans(self):
    ''' Sanity check the ._spans attribute.
        Raises TypeError or ValueError on failure.
    '''
    _spans = self._spans
    if type(_spans) is not list:
      raise TypeError("._spans should be a list")
    ospan = None
    for span in _spans:
      if not isinstance(span, Span):
        raise TypeError("._spans elements should be lists, found "+repr(span))
      start, end = span
      if not isinstance(start, int) or not isinstance(end, int):
        raise TypeError("._spans elements should be a pair of ints, found "+repr(span))
      if start >= end:
        raise ValueError("._spans elements should have low < high, found "+repr(span))
      if ospan is not None:
        if ospan[1] >= start:
          raise ValueError(
              "._spans elements should be strictly greater than their"
              " predecessors, found %s then %s"
              % (ospan, span))
      ospan = span

  def __iter__(self):
    ''' Yield all the elements.
    '''
    for _span in self._spans:
      for x in range( *_span ):
        yield x

  def __bool__(self):
    return len(self._spans) > 0

  __nonzero__ = __bool__

  def __len__(self):
    return sum( [ end-start for start, end in self._spans ] )

  @property
  def start(self):
    ''' Return the start offset of the `Range`,
        the minimum `Span` .start or `0` if the `Range` is empty.
    '''
    spans = self._spans
    if len(spans) > 0:
      return spans[0].start
    else:
      return 0

  @property
  def end(self):
    ''' Return the end offset of the `Range`,
        the maximum `Span` .end or `0` if the `Range` is empty.
    '''
    spans = self._spans
    if len(spans) > 0:
      return spans[-1].end
    else:
      return 0

  def isempty(self):
    ''' Test if the Range is empty.
    '''
    return not self

  def __contains__(self, x):
    ''' Test `x` to see if it is wholly contained in this Range.

        `x` may be another `Range`, a `Span`, or a single `int` or an iterable
        yielding a pair of `int`s.
    '''
    if isinstance(x, Range):
      return self.issuperset(x)
    if isinstance(x, Span):
      start = x.start
      end = x.end
    elif isinstance(x, int):
      start, end = x, x+1
    else:
      start, end = list(x)
    _spans = self._spans
    ndx = bisect_left(_spans, Span(start, start))
    if ndx > 0 and _spans[ndx-1].end > start:
      ndx -= 1
    elif ndx >= len(_spans):
      return False
    span = _spans[ndx]
    if start < span.start:
      return False
    if end > span.end:
      return False
    return True

  def span_position(self, start, end):
    ''' Somewhat like `bisect_left`, return indices `(i,j)`
        such that all spans with indices < `i`
        strictly preceed `start` amd all spans with indices > `j`
        strictly follow `end`.
    '''
    _spans = self._spans
    i = bisect_left(_spans, Span(start, start))
    # check preceeding span
    assert i == 0 or _spans[i-1].end <= start
    # check current span
    assert i == len(_spans) or _spans[i].start >= start
    raise RuntimeError("INCOMPLETE")

  def slices(self, start=None, end=None):
    ''' Return an iterable of (inside, Span) covering the gaps and spans in this Range.
        If `start` is omitted, start at the minimum of 0 and the
        lowest span in the Range.
        If `end` is omitted, use the maximum span in the Range.
        `inside` is true for spans and false for gaps.
        TODO: make this efficient if `start` isn't near the start of the _spans.
    '''
    spans = self._spans
    if start is None:
      start = min(0, self.start) if spans else 0
    if end is None:
      end = max(start, self.end) if spans else start
    for span in spans:
      if start < span.start:
        yield False, Span(start, span.start)
        start = span.start
        if start >= end:
          break
      if end < span.end:
        yield True, Span(start, end)
        start = end
        break
      yield True, span
      start = span.end
      if start >= end:
        break
    if start < end:
      yield False, Span(start, end)

  def dual(self, start=None, end=None):
    ''' Return an iterable of the gaps (spans not in this Range).
        If `start` is omitted, start at the minimum of 0 and the
        lowest span in the Range.
        If `end` is omitted, use the maximum span in the Range.
    '''
    for inside, span in self.slices(start=start, end=end):
      if not inside:
        yield span

  def issubset(self, other):
    ''' Test that self is a subset of other.
    '''
    # TODO: handle other Ranges specially
    for x in self:
      if x not in other:
        return False
    return True

  __le__ = issubset

  def issuperset(self, other):
    ''' Test that self is a superset of other.
    '''
    # TODO: handle ranges specially
    for x in other:
      if x not in self:
        return False
    return True

  __ge__ = issuperset

  def copy(self):
    ''' Return a copy of this Range.
    '''
    R2 = Range()
    R2._spans = list(self._spans)
    return R2

  def add_span(self, start, end):
    ''' Update self with [start,end].
    '''
    if start >= end:
      return
    _spans = self._spans
    # locate start index: all affected spans start from here
    S0 = Span(start, start)
    i = bisect_left(_spans, S0)
    if i > 0 and _spans[i-1].end >= start:
      i -= 1
    drop_from = i
    new_start = start
    new_end = end
    while i < len(_spans) and _spans[i].start <= end:
      span = _spans[i]
      # check that the spans overlap
      assert span.start <= end and span.end >= start
      new_start = min(new_start, span.start)
      new_end = max(new_end, span.end)
      i += 1
    drop_to = i
    new_span = Span(new_start, new_end)
    if self._debug:
      self._check()
    _spans[drop_from:drop_to] = [ new_span ]
    if self._debug:
      self._check()

  def discard_span(self, start, end, remove_mode=False):
    ''' Remove [start,end] from Range if present.
    '''
    if start >= end:
      # empty range, do nothing
      return
    ospan = Span(start, end)
    _spans = self._spans
    # locate start index: all affected spans start from here
    i = bisect_left(_spans, Span(start, start))
    if i > 0:
      i -= 1
    # consider all spans from just below the range to its end
    insert_spans = []
    drop_from = None
    # walk spans until span.start >= end
    while i < len(_spans) and _spans[i].start < end:
      # span starts below end
      span = _spans[i]
      if remove_mode and start < span.start:
        raise KeyError("span %s not entirely in Range" % (ospan,))
      if span.end > start:
        # this span should be considered
        if drop_from is None:
          drop_from = i
        # split span on cropping range
        low_span = Span(span.start, start)
        high_span = Span(end, span.end)
        start = max(start, span.end)
        # keep non-empty subspans
        if low_span.start < low_span.end:
          insert_spans.append(low_span)
        else:
          pass
        if high_span.start < high_span.end:
          insert_spans.append(high_span)
        else:
          pass
      i += 1

    if remove_mode and start < end:
      raise KeyError("span %s not entirely in Range" % (ospan,))

    drop_to = i
    if (drop_from is not None and drop_from < drop_to) or len(insert_spans) > 0:
      _spans[drop_from:drop_to] = insert_spans
    if self._debug:
      self._check()

  def add(self, start, end=None):
    ''' Like `set.add` but with an extended signature.
    '''
    if end is not None:
      self.add_span(start, end)
    elif isinstance(start, Range):
      for span in start.spans():
        self.add_span(*span)
    else:
      self.add_span(start, start+1)

  def discard(self, start, end=None):
    ''' Like `set.discard` but with an extended signature.
    '''
    if end is not None:
      self.discard_span(start, end)
    elif isinstance(start, Range):
      for span in start.spans():
        self.discard_span(*span)
    else:
      self.discard_span(start, start+1)

  def remove(self, start, end=None):
    ''' Like `set.remove` but with an extended signature.
    '''
    if end is not None:
      self.discard_span(start, end, remove_mode=True)
    elif isinstance(start, Range):
      for span in start.spans():
        self.discard_span(*span, remove_mode=True)
    else:
      self.discard_span(start, start+1, remove_mode=True)

  def update(self, iterable):
    ''' Update the `Range` to include the values from `iterable`.
    '''
    start = None
    for i in iterable:
      if start is None:
        start = i
        end = i + 1
      elif i == end:
        end += 1
      else:
        self.add_span(start, end)
        start = i
        end = i + 1
    if start is not None:
      self.add_span(start, end)

  def __ior__(self, other):
    self.update(other)
    return self

  def intersection_update(self, other):
    ''' Update the `Range`, keeping only elements
        found in both `self` and `other`.
    '''
    # TODO: more efficient way to do this? probably not, actually
    R2 = self.intersection(other)
    self._spans = R2._spans
    R2._spans = []

  def __iand__(self, other):
    self.intersection_update(other)
    return self

  def union(self, other):
    ''' Return a new `Range` containing the elements of `self` and `other`.
    '''
    R2 = self.copy()
    R2.update(other)
    return R2

  __or__ = union

  def intersection(self, other):
    ''' Return a new `Range` containing elements in both `self` and `other`.
    '''
    R2 = Range()
    if isinstance(other, Range):
      ospans = other._spans
    else:
      ospans = Range(other)._spans
    _spans = self._spans
    for ostart, oend in ospans:
      ndx = bisect_left(_spans, [ostart, oend])
      while ndx < len(_spans):
        _span = _spans[ndx]
        start, end = overlap(_span, [ostart, oend])
        if start < end:
          R2.update(start, end)
        ostart = _span[1]
        ndx += 1
    return R2

  __and__ = intersection

  def difference(self, start, end=None):
    ''' Subtract `start`, or `start:end`, from the `Range`.
    '''
    R2 = self.copy()
    if end is None:
      R2.discard(start)
    else:
      R2.discard_span(start, end)
    return R2

  __sub__ = difference
  difference_update = discard

  def __isub__(self, other):
    self.discard(other)
    return self

  def pop(self):
    ''' Remove and return an arbitrary element.
        Raise `KeyError` if the `Range` is empty.
    '''
    _spans = self._spans
    if not _spans:
      raise KeyError("pop() from empty Range")
    span = _spans[-1]
    start, end = span
    end -= 1
    if end > start:
      span[1] = end
    else:
      del _spans[-1]
    return end

  def symmetric_difference(self, other):
    ''' Return a new `Range` with elements in `self` or `other` but not both.
    '''
    if not isinstance(other, Range):
      other = Range(other)
    R1 = self.difference(other)
    R2 = other.difference(self)
    R2.update(R1)
    return R2

  __xor__ = symmetric_difference

  def symmetric_difference_update(self, other):
    ''' Update the `Range`, keeping only elements found in `self` or `other`,
        but not in both.
    '''
    R2 = self.symmetric_difference(other)
    self._spans = R2._spans
    R2._spans = []

  def __ixor__(self, other):
    self.symmetric_difference_update(other)
    return self

if __name__ == '__main__':
  import cs.range_tests
  cs.range_tests.selftest(sys.argv)
