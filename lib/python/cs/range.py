#!/usr/bin/python
#
# A Range in an object resembling a set but optimised for contiguous
# ranges of int members.
#       - Cameron Simpson <cs@zip.com.au>
#
# TODO: add __getitem__, __getslice__, __delitem__, __delslice__ methods.
#

from __future__ import print_function

DISTINFO = {
    'description': "a Range class implementing compact integer ranges with a set-like API, and associated functions",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
        ],
    'requires': ['cs.logutils'],
}

import sys
from bisect import bisect_left
from collections import namedtuple
from cs.logutils import ifdebug

def overlap(span1, span2):
  ''' Return a list [start, end] denoting the overlap of two spans.
  '''
  # no item lower than either lows
  lo = max(span1[0], span2[0])
  # no item higher than either high
  hi = min(span1[1], span2[1])
  # hi must be >= lo
  return [lo, max(lo, hi)]

def spans(items):
  ''' Return an iterable of Spans for all contiguous sequences in
      `items`. Example:
        spans([1,2,3,7,8,11,5]) == [ [1,4], [7:9], [11:12], [5:6] ]
  '''
  # see if the object has a .spans() method
  try:
    item_spans = items.spans()
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
  def __str__(self):
    return "%d:%d" % (self.start, self.end)
  __repr__ = __str__
  def __eq__(self, other):
    return self[0] == other[0] and self[1] == other[1]
  def __lt__(self, other):
    return self[0] < other[0] or (self[0] == other[0] and self[1] < other[1])
  @property
  def size(self):
    return self.end - self.start

class Range(object):
  ''' A collection of ints that collates adjacent ints.
      The interface is as for a set with additional methods:
        - spans(): return an iterable of Spans, with .start
          included in each span and .end just beyond
      Additionally, the update/remove/etc methods have a secondary
      calling signature: (start, end), which is the same as passing
      in range(start, end) but much more efficient.
  '''

  def __init__(self, start=None, end=None, debug=None):
    if debug is None:
      debug = ifdebug()
    self._debug = debug
    self._spans = []
    if start is not None:
      if end is None:
        # "start" must be an iterable of ints
        for start, end in spans(start):
          self.add_span(start, end)
      else:
        self.add_span(start, end)

  def __str__(self):
    return "[%s]" % (",".join( [ "[%d:%d)" % (S.start, S.end) for S in self._spans ] ))
    ##spans = [ "%d"%(start,) if start == end-1
    ##          else "%d,%d"%(start,end-1) if start == end-2
    ##          else "%d..%d"%(start,end-1) for start, end in self._spans ]
    ##return "[%s]" % (",".join(spans))

  def __eq__(self, other):
    if type(other) is Range:
      return self._spans == other._spans
    return list(self) == list(other)

  def __ne__(self, other):
    return not self == other

  __hash__ = None

  def clear(self):
    self._spans = []

  def spans(self):
    ''' Return an iterable of [start, end].
    '''
    for span in self._spans:
      yield Span(*span)

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
      if type(span) is not Span:
        raise TypeError("._spans elements should be lists, found "+repr(span))
      if len(span) != 2:
        raise ValueError("._spans elements should have length 2, found "+repr(span))
      start, end = span
      if type(start) is not int or type(end) is not int:
        raise TypeError("._spans elements should be a pair of ints, found "+repr(span))
      if start >= end:
        raise ValueError("._spans elements should have low < high, found "+repr(span))
      if ospan is not None:
        if ospan[1] >= start:
          raise ValueError("._spans elements should be strictly greater than their predecessors, found %s then %s" % (ospan, span))
      ospan = span

  def __iter__(self):
    ''' Yield all the elements.
    '''
    for _span in self._spans:
      for x in range( *_span ):
        yield x

  def __len__(self):
    return sum( [ end-start for start, end in self._spans ] )

  @property
  def end(self):
    ''' Return the end offset of the Range - the maximum Span .end or 0 if the Range is empty.
    '''
    spans = self._spans
    if len(spans) > 0:
      return spans[-1].end
    else:
      return 0

  def isempty(self):
    ''' Test if the Range is empty; it has no spans.
    '''
    return len(self._spans) == 0

  def __contains__(self, x):
    ''' Test `x` to see if it is wholly contained in this Range.
        `x` may be another Range, a single int or an iterable
        yielding a pair of ints.
    '''
    if isinstance(x, Range):
      return self.issuperset(x)
    if isinstance(x, int):
      start, end = x, x+1
    else:
      start, end = list(x)
    _spans = self._spans
    ndx = bisect_left(self._spans, Span(start, start))
    if ndx > 0 and start < _spans[ndx].start:
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
    ''' Somewhat like bisect_left, return indices (i, j) such that all spans with indices < i strictly preceed start amd all spans with indices > j strictly follow end.
    '''
    _spans = self._spans
    i = bisect_left(_spans, Span(start, start))
    # check preceeding span
    assert(i == 0 or _spans[i-1].end <= start)
    # check current span
    assert(i == len(_spans) or _spans[i].start >= start)
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
      start = min(0, spans[0].start) if spans else 0
    if end is None:
      end = max(start, spans[-1].end) if spans else start
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
      assert(span.start <= end and span.end >= start)
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
    ''' Like set.add but with an extended signature.
    '''
    if end is not None:
      self.add_span(start, end)
    elif isinstance(start, Range):
      for span in start.spans():
        self.add_span(*span)
    else:
      self.add_span(start, start+1)

  def discard(self, start, end=None):
    ''' Like set.discard but with an extended signature.
    '''
    if end is not None:
      self.discard_span(start, end)
    elif isinstance(start, Range):
      for span in start.spans():
        self.discard_span(*span)
    else:
      self.discard_span(start, start+1)

  def remove(self, start, end=None):
    ''' Like set.remove but with an extended signature.
    '''
    if end is not None:
      self.discard_span(start, end, remove_mode=True)
    elif isinstance(start, Range):
      for span in start.spans():
        self.discard_span(*span, remove_mode=True)
    else:
      self.discard_span(start, start+1, remove_mode=True)

  def update(self, iterable):
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
    # TODO: more efficient way to do this? probably not, actually
    R2 = self.intersection(other)
    self._spans = R2._spans
    R2._spans = []

  def __iand__(self, other):
    self.intersection_update(other)
    return self

  def union(self, other):
    R2 = self.copy()
    R2.update(other)
    return R2

  __or__ = union

  def intersection(self, other):
    R2 = Range()
    if type(other) is Range:
      spans = other._spans
    else:
      spans = Range.range(other)
    _spans = self._spans
    for ostart, oend in spans:
      ndx = bisect_left(_spans, [ostart, oend])
      while ndx < len(_spans):
        _span = _spans[ndx]
        start, end = Range.overlap(_span, [start, oend])
        if start < end:
          R2.update(start, end)
        ostart = _span[1]
        ndx += 1
    return R2

  __and__ = intersection

  def difference(self, start, end=None):
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
        Raise KeyError if the Range is empty.
    '''
    _spans = self._spans
    if len(_spans) == 0:
      raise KeyError("pop() from empty Range")
    span = _spans[-1]
    start, end = _spans[-1]
    end -= 1
    if end > start:
      span[1] = end
    else:
      del spans[-1]
    return end

  def symmetric_difference(self, other):
    ''' Return a new Range with elements in self or other but not both.
    '''
    if type(other) is not Range:
      other = Range(other)
    R1 = self.difference(other)
    R2 = other.difference(self)
    R2.update(R1)
    return R2

  __xor__ = symmetric_difference

  def symmetric_difference_update(self, other):
    R2 = self.symmetric_difference(other)
    self._spans = R2._spans
    R2._spans = []

  def __ixor__(self, other):
    self.symmetric_difference_update(other)
    return self

if __name__ == '__main__':
  import cs.range_tests
  cs.range_tests.selftest(sys.argv)
