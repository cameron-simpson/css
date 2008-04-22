#!/usr/bin/python -tt
#

import sys
from cs.misc import isdebug, reportElapsedTime

class ChunkyString(str):
  ''' An object that looks like a char array with eough string functionality
      to be used as the "data" argument to urllib2.urlopen().
      It is intended for large "data" chunks that seem prohibitive to
      assemble into a real string.
  '''
  def __init__(self):
    self.__strs=[]
    self.__len=0
    self.__rewind()

  def __rewind(self):
    self.__offset=0
    self.__ndx=0
    self.__suboff=0

  def __skipto(self,pos):
    assert pos >= 0, "pos(%d) < 0" % pos
    if pos < self.__offset:
      self.__rewind()
    self.__rskipto(pos-self.__offset)

  def __rskipto(self,rpos):
    assert rpos >= 0, "rpos(%d) < 0" % rpos
    pos=self.__offset
    ndx=self.__ndx
    suboff=self.__suboff

    hops=0
    nskip=rpos
    while nskip > 0:
      hops+=1
      s=self.__strs[ndx]
      taillen=len(s)-suboff
      if taillen <= nskip:
        nskip-=taillen
        ndx+=1
        suboff=0
      else:
        suboff+=nskip
        nskip=0
        break

    if isdebug:
      print >>sys.stderr, "__rskipto(%d)@pos=%d took %d hops" % (rpos, self.__offset, hops)

    self.__offset+=rpos
    self.__ndx=ndx
    self.__suboff=suboff

  def __read(self,nread):
    assert nread >= 1, "nread(%d) < 1"
    s=self.__strs[self.__ndx]
    suboff=self.__suboff
    taillen=len(s)-suboff
    assert taillen > 0
    n=min(taillen,nread)
    rs=s[suboff:suboff+n]
    if taillen > nread:
      self.__offset+=nread
      self.__suboff+=nread
    else:
      assert taillen == n, "taillen(%d) != n(%d)" % (taillen,n)
      self.__offset+=taillen
      self.__ndx+=1
      self.__suboff=0

    return rs

  def __str__(self):
    s=''.join(self.__strs)
    assert len(s) == self.__len
    return s

  def __nonzero__(self):
    return self.__len > 0

  def write(self,s):
    self.__strs.append(s)
    self.__len+=len(s)

  def __getattr__(self,attr,*args,**kw):
    assert False, "asked for unsupported attribute \"%s(*args=%s,**kw=%s)\"" % (attr,args,kw)

  def __len__(self):
    return self.__len

  def __getslice__(self,low,high):
    print >>sys.stderr, "getslice(%d,%d)" % (low,high)
    assert low >= 0
    low=min(low,self.__len)

    assert high >= 0
    high=min(high,self.__len)

    if low >= self.__len or high <= low:
      return ''

    self.__skipto(low)
    n=high-low
    strs=[]
    while n > 0:
      s=self.__read(n)
      assert len(s) <= n, "len(\"%s\") > n(%d)" % (s,n)
      strs.append(s)
      n-=len(s)

    return ''.join(strs)
