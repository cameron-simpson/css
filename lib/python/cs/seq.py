#!/usr/bin/python -tt
#
# Stuff to do with sequences.
#       - Cameron Simpson <cs@zip.com.au> 20jul2008
#

import bisect
import unittest

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

def imerge(*seqs):
  ''' Merge a list of sequences in order.
      It relies on the source sequences being ordered and their elements
      being comparable, through slightly misordered sequences (for example,
      as extracted from web server logs) will produce slightly misordered
      results, as the merging is done on the basis of the front elements
      of each sequence.
  '''
  #TODO: don't pop(), iterate
  seqs = list( iter(s) for s in seqs )

  # prime the list of head elements
  heads = list(None for i in range(len(seqs)))
  i=0
  while i < len(seqs):
    try:
      heads[i] = seqs[i].next()
    except StopIteration:
      seqs.pop(i)
      heads.pop(i)
      continue
    i += 1

  # yield leading items in order
  while len(heads) > 0:
    choice = 0
    head = heads[0]
    for i in range(1, len(seqs)):
      if heads[i] < head:
        choice = i
        head = heads[i]
    yield head
    try:
      heads[choice] = seqs[choice].next()
    except StopIteration:
      heads.pop(choice)
      seqs.pop(choice)

if __name__ == '__main__':
  import sys
  import cs.seq_tests
  cs.seq_tests.selftest(sys.argv)
