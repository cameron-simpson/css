#!/usr/bin/python

import sys
from bisect import bisect_left
import unittest

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
  ''' Return an iterable of [start, end] for all contiguous sequences in
      `items`. Example:
        spans([1,2,3,7,8,11,5]) == [ [1,4], [7:9], [11:12], [5:6] ]
  '''
  if type(items) is Range:
    # quick version for a Range
    for span in items._spans:
      yield span
    return

  # otherwise compute by examination
  prev = None
  for i in items:
    if prev is None:
      prev = [i, i+1]
    elif i == prev[1]:
      prev[1] += 1
    else:
      yield prev
      prev = [i, i+1]
  if prev is not None:
    yield prev

class Range(object):
  ''' A collection of ints that collates adjacent ints.
      The interface is as for a set with additional methods:
        - spans(): return an iterable of (start, end) tuples, with start
          included in the span and end just beyond
      Additionally, the update/remove/etc methods have a secondary
      calling signature: (start, end), which is the same as passing
      in range(start, end) but much more efficient.
  '''

  def __init__(self, start=None, end=None):
    self._spans = []    # TODO: maybe a deque?
    if start is not None:
      if end is None:
        # "start" is an iterable
        for start, end in spans(start):
          self.update(start, end)
      else:
        self.update(start, end)

  def __str__(self):
    return str(self._spans)

  def __eq__(self, other):
    if type(other) is Range:
      return self._spans == other._spans
    return list(self) == list(other)

  def spans(self):
    ''' Return an interable of (start, end) tuples.
    '''
    for span in self._spans:
      yield list(span)

  def _check(self):
    self._check_spans()

  def _check_spans(self):
    ''' Sanity check the ._spans attribute.
        Raises TypeError or ValueError on failure.
    '''
    _spans = self._spans
    if type(_spans) is not list:
      raise TypeError, "._spans should be a list"
    ospan = None
    for span in _spans:
      if type(span) is not list:
        raise TypeError, "._spans elements should be lists, found "+repr(span)
      if len(span) != 2:
        raise ValueError, "._spans elements should have length 2, found "+repr(span)
      lo, hi = span
      if type(lo) is not int or type(hi) is not int:
        raise TypeError, "._spans elements should be a pair of ints, found "+repr(span)
      if lo >= hi:
        raise ValueError, "._spans elements should have low < high, found "+repr(span)
      if ospan is not None:
        if ospan[1] >= lo:
          raise ValueError, "._spans elements should be strictly greater than their predecessors, found "+repr(ospan)+", "+repr(span)
      ospan = span

  def __iter__(self):
    ''' Yield all the elements.
    '''
    for _span in self._spans:
      for x in range( *_span ):
        yield x

  def __len__(self):
    return sum( [ end-start for start, end in self._spans ] )

  def __contains__(self, x):
    _spans = self._spans
    if type(x) is int:
      x = [x, x+1]
    else:
      x = list(x)
    ndx = bisect_left(self._spans, x)
    if ndx >= len(_spans):
      return False
    span = _spans[ndx]
    if x[0] < span[0]:
      return False
    if x[1] > span[1]:
      return False
    return True

  def issubset(self, other):
    for x in self:
      if x not in other:
        return False
    return True

  def issuperset(self, other):
    for x in other:
      if x not in self:
        return False
    return True

  def copy(self):
    R2 = Range()
    R2._spans = list(self._spans)
    return R2

  def union(self, other):
    R2 = self.copy()
    R2.update(other)
    return R2

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

    # TODO: if type(other) is Range, do fast ordered merge
    for x in other:
      if x in self:
        R2.add(x)
    return R2

  def difference(self, other):
    R2 = Range()
    # TODO: if type(other) is Range, do fast ordered difference
    for x in self:
      if x not in other:
        R2.add(x)
    return R2

  def symmetric_difference(self, other):
    R2 = self.difference(other)
    for x in other:
      if x not in self:
        R2.add(x)
    return R2

  def update(self, start, end=None):
    if end is None:
      # conventional iterable single argument
      for span in spans(start):
        self.update( *span )
      return
    if start >= end:
      return
    _spans = self._spans
    londx = bisect_left(_spans, [start, end])
    # find list of spans to replace
    # i.e. those overlapping the new span
    hindx = londx
    if londx > 0 and _spans[londx-1][1] >= start:
      londx -= 1
      start = _spans[londx][0]
    while hindx < len(_spans) and end >= _spans[hindx][1]:
      hindx += 1
    # merge final span if overlapping
    if hindx < len(_spans) and _spans[hindx][0] <= end:
      end = _spans[hindx][1]
      hindx += 1
    # replace overlapped spans with new span
    _spans[londx:hindx] = [ [start, end] ]

class TestAll(unittest.TestCase):
  def setUp(self):
    self.items1 = [1,2,3,7,8,11,5]
    self.spans1 = [ [1,4], [5,6], [7,9], [11,12] ]
    self.items2 = [3,5,6,8,9,10,15,16,19]
    self.spans2 = [ [3,4], [5,7], [8,11], [15,17], [19,20] ]
    self.items1plus2 = [1,2,3,5,6,7,8,9,10,11,15,16,19]
    self.spans1plus2 = [ [1,4], [5,12], [15,17], [19,20] ]

  def test00spans(self):
    self.assertNotEqual(list(spans(self.items1)), self.spans1)
    self.assertEqual(list(spans(sorted(self.items1))), self.spans1)
    self.assertEqual(list(spans(self.items2)), self.spans2)

  def test01overlap(self):
    self.assertEqual( overlap([1,2], [3,4]), [3,3] )

  def test10init(self):
    R0 = Range()
    R0._check()
    self.assertEqual(list(R0.spans()), [])
    R0.update(self.items1)
    R0._check()
    self.assertEqual(list(R0.spans()), self.spans1)
    R1 = Range(self.items1)
    R1._check()
    self.assertEqual(list(R1.spans()), self.spans1)
    R2 = Range(self.items2)
    R2._check()
    self.assertEqual(list(R2.spans()), self.spans2)

  def test11equals(self):
    R1 = Range(self.items1)
    self.assertEqual(R1, R1)
    self.assertEqual(list(iter(R1)), sorted(self.items1))

  def test12copy(self):
    R1 = Range(self.items1)
    R2 = R1.copy()
    R2._check()
    self.assertEqual(R1, R2)
    self.assertEqual(R1._spans, R2._spans)
    self.assertEqual(list(R1.spans()), list(R2.spans()))

  def test13union(self):
    R1 = Range(self.items1)
    R1._check()
    R2 = Range(self.items2)
    R2._check()
    R3 = R1.union(R2)
    R3._check()
    self.assertEqual(list(R3), self.items1plus2)
    self.assertEqual(list(R3.spans()), self.spans1plus2)

if __name__ == '__main__':
  unittest.main()
