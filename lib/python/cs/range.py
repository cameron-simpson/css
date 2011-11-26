#!/usr/bin/python
#
# A Range in an object resembling a set but optimised for contiguous
# ranges of int members.
#       - Cameron Simpson <cs@zip.com.au>
#

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
    spans = [ "%d"%(start,) if start == end-1
              else "%d,%d"%(start,end-1) if start == end-2
              else "%d..%d"%(start,end-1) for start, end in self._spans ]
    return "[%s]" % (",".join(spans))

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
    ''' Test `x` to see if it is wholly contained in this Range.
	`x` may be another Range, a single int or an iterable
	yielding a pair of ints.
    '''
    t = type(x)
    if t is Range:
      return self.issuperset(x)
    if t is int:
      x = [x, x+1]
    else:
      x = list(x)
      if len(x) != 2:
        raise ValueError, "__contains__ requires a Range, int or pair of ints, got %s" % (x,)
    _spans = self._spans
    ndx = bisect_left(self._spans, x)
    if ndx > 0 and x[0] < _spans[ndx][0]:
      ndx -= 1
    elif ndx >= len(_spans):
      return False
    span = _spans[ndx]
    if x[0] < span[0]:
      return False
    if x[1] > span[1]:
      return False
    return True

  def dual(self, start=None, end=None):
    ''' Return an iterable of the spans not in this range.
	If `start` is omitted, start at the minimum of 0 and the
	lowest span in the Range.
        If `end` is omitted, use the maximum span in the Range.
    '''
    # TODO: implement this!
    raise NotImplementedError

  def issubset(self, other):
    ''' Test that self is a subset of other.
    '''
    # TODO: handle ranges specially
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
    R2._spans = list( [span[0], span[1]] for span in self._spans )
    return R2

  def update(self, start, end=None):
    ''' Update self with [start,end].
    '''
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
    R2.discard(start, end)
    return R2

  __sub__ = difference

  def discard(self, start, end=None):
    ''' Remove [start,end] from Range if present.
    '''
    if end is None:
      if type(start) is int:
        end = start+1
      else:
        # start is a Range or iterable
        for span in ( start.spans() if type(start) is Range
                                    else spans(sorted(start)) ):
          self.discard( *span )
        return
    if start < end:
      _spans = self._spans
      londx = bisect_left(_spans, [start, end])
      hindx = londx
      # crop preceeding block if overlapping
      if londx > 0:
        prev_span = _spans[londx-1]
        if prev_span[1] > start:
          prev_span[1] = start
          # remove preceeding block if now empty
          if prev_span[0] == prev_span[1]:
            londx -= 1
      # locate spans to delete
      while hindx < len(_spans) and _spans[hindx][1] <= end:
        hindx += 1
      # crop following block if overlapping
      if hindx < len(_spans) and _spans[hindx][0] < end:
        _spans[hindx][0] = end
      # remove swallowed spans
      del _spans[londx:hindx]

  def __isub__(self, other):
    self.discard(other)
    return self

  def remove(self, start, end=None):
    ''' Remove [start,end] from Range.
        Raise KeyError if [start,end] not a subset of Range.
    '''
    if end is None:
      if type(start) is int:
        end = start+1
      else:
        # start is a Range or iterable
        for span in ( start.spans() if type(start) is Range
                                    else spans(sorted(start)) ):
          self.remove( *span )
        return
    if start < end:
      _spans = self._spans
      ndx = bisect_left(_spans, [start, end])
      if ndx > 0 and _spans[ndx-1][1] > start:
        ndx -= 1
      span = _spans[ndx]
      if start < span[0] or end > span[1]:
        raise KeyError, "[%s:%s] not a subset of %s" % (start, end, self)
      if start == span[0]:
        if end == span[1]:
          print >>sys.stderr, "remove %s:%s from %s: delete span" % (start,end,span)
          del _spans[ndx]
        else:
          span[0] = end
      else:
        if end == span[1]:
          span[1] = start
        else:
          _spans[ndx:ndx+1] = [ [span[0], start], [end, span[1]] ]
    self._check()

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

class TestAll(unittest.TestCase):
  def setUp(self):
    self.items1 = [1,2,3,7,8,11,5]
    self.spans1 = [ [1,4], [5,6], [7,9], [11,12] ]
    self.items2 = [3,5,6,8,9,10,15,16,19]
    self.spans2 = [ [3,4], [5,7], [8,11], [15,17], [19,20] ]
    self.items1plus2 = [1,2,3,5,6,7,8,9,10,11,15,16,19]
    self.spans1plus2 = [ [1,4], [5,12], [15,17], [19,20] ]
    self.items1minus2 = [1,2,7,11]
    self.spans1minus2 = [ [1,3], [7,8], [11,12] ]
    self.items1xor2 = [1,2,6,7,9,10,11,15,16,19]
    self.spans1xor2 = [ [1,3], [6,8], [9,12], [15,17], [19,20] ]

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
    self.assertEqual(R0, R1)
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
    self.assertIsNot(R1, R2)
    self.assertEqual(R1, R2)
    self.assertEqual(R1._spans, R2._spans)
    self.assertEqual(list(R1.spans()), list(R2.spans()))

  def test13update00fromItems(self):
    R1 = Range(self.items1)
    R1._check()
    R1.update(self.items2)
    R1._check()
    self.assertEqual(list(R1), self.items1plus2)
    self.assertEqual(list(R1.spans()), self.spans1plus2)

  def test13update01fromSpans(self):
    R1 = Range(self.items1)
    R1._check()
    for span in self.spans2:
      R1.update(span[0], span[1])
      R1._check()
    self.assertEqual(list(R1), self.items1plus2)
    self.assertEqual(list(R1.spans()), self.spans1plus2)

  def test13update02fromRange(self):
    R1 = Range(self.items1)
    R1._check()
    R2 = Range(self.items2)
    R2._check()
    R1.update(R2)
    R1._check()
    self.assertEqual(list(R1), self.items1plus2)
    self.assertEqual(list(R1.spans()), self.spans1plus2)

  def test14union(self):
    R1 = Range(self.items1)
    R1._check()
    R2 = Range(self.items2)
    R2._check()
    R3 = R1.union(R2)
    R3._check()
    self.assertEqual(list(R3), self.items1plus2)
    self.assertEqual(list(list(R3.spans())), self.spans1plus2)

  def test15discard(self):
    R1 = Range(self.items1)
    R1._check()
    R2 = Range(self.items2)
    R2._check()
    R1.discard(R2)
    R1._check()
    self.assertEqual(list(R1), self.items1minus2)
    self.assertEqual(list(list(R1.spans())), self.spans1minus2)

  def test16remove(self):
    R1 = Range(self.items1)
    R1._check()
    R1.remove(3)
    R1._check()
    self.assertRaises(KeyError, R1.remove, 3)
    R1._check()

  def test17difference_subset_superset(self):
    R1 = Range(self.items1)
    R1._check()
    R2 = Range(self.items2)
    R2._check()
    R3 = R1.difference(R2)
    R3._check()
    self.assertEqual(list(R3), self.items1minus2)
    self.assertEqual(list(list(R3.spans())), self.spans1minus2)
    self.assert_(R1.issuperset(R3))
    self.assert_(R3.issubset(R1))
    R4 = R1 - R2
    R4._check()
    self.assertEqual(list(R4), self.items1minus2)
    self.assertEqual(list(list(R4.spans())), self.spans1minus2)
    self.assert_(R1.issuperset(R4))
    self.assert_(R4.issubset(R1))

  def test17symmetric_difference(self):
    R1 = Range(self.items1)
    R1._check()
    R2 = Range(self.items2)
    R2._check()
    R3 = R1.symmetric_difference(R2)
    R3._check()
    self.assertEqual(list(R3), self.items1xor2)
    self.assertEqual(list(list(R3.spans())), self.spans1xor2)
    R4 = R1 ^ R2
    R4._check()
    self.assertEqual(list(R4), self.items1xor2)
    self.assertEqual(list(list(R4.spans())), self.spans1xor2)
    self.assertEqual(R4, R3)
    self.assertIsNot(R4, R3)

if __name__ == '__main__':
  unittest.main()
