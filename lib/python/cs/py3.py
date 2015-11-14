#!/usr/bin/python -tt
#
# Python3 helpers to aid code sharing between python2 and python3.
#       - Cameron Simpson <cs@zip.com.au> 28jun2012
#

DISTINFO = {
    'description': "Aids for code sharing between python2 and python3.",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
        ],
}

import sys

if sys.hexversion >= 0x03000000:

  unicode = str
  StringTypes = (str,)
  def ustr(s, e='utf-8', errors='strict'):
    ''' Upgrade string to unicode: no-op for python 3.
    '''
    return s
  from queue import Queue, PriorityQueue, Full as Queue_Full, Empty as Queue_Empty
  from configparser import ConfigParser
  def iteritems(o):
    return o.items()
  def iterkeys(o):
    return o.keys()
  def itervalues(o):
    return o.values()
  from builtins import sorted, filter, bytes, input
  from itertools import filterfalse
  from .py3_for3 import raise3, exec_code

else:

  globals()['unicode'] = unicode
  from types import StringTypes
  def ustr(s, e='utf-8', errors='strict'):
    ''' Upgrade str to unicode, if it is a str. Leave other types alone.
    '''
    if isinstance(s, str):
      try:
        s = s.decode(e, errors)
      except UnicodeDecodeError as ude:
        from cs.logutils import warning
        warning("cs.py3.ustr(): %s: s = %s %r", ude, type(s), s)
        s = s.decode(e, 'replace')
    return s
  from Queue import Queue, PriorityQueue, Full as Queue_Full, Empty as Queue_Empty
  from ConfigParser import SafeConfigParser as ConfigParser
  def iteritems(o):
    return o.iteritems()
  def iterkeys(o):
    return o.iterkeys()
  def itervalues(o):
    return o.itervalues()
  input = raw_input
  _sorted = sorted
  def sorted(iterable, key=None, reverse=False):
    return _sorted(iterable, None, key, reverse)
  input = raw_input
  from itertools import ifilter as filter, ifilterfalse as filterfalse
  from .py3_for2 import raise3, exec_code

  class bytes(list):
    ''' Trite bytes implementation.
    '''
    def __init__(self, arg):
      try:
        bytevals = iter(arg)
      except TypeError:
        bytevals = [ 0 for i in range(arg) ]
      self.__s = ''.join( chr(b) for b in bytevals )
    def __repr__(self):
      return 'b' + repr(self.__s)
    def __iter__(self):
      for _ in self.__s:
        yield ord(_)
    def __getitem__(self, index):
      return ord(self.__s[index])
    def __getslice__(self, i, j):
        return bytes( ord(_) for _ in self.__s[i:j] )
    def __contains__(self, b):
      return chr(b) in self.__s
    def __eq__(self, other):
      if type(other) is type(self):
        return self.__s == other.__s
      if len(other) != len(self):
        return False
      for i, b in enumerate(self):
        if b != other[i]:
          return False
      return True
    def __len__(self):
      return len(self.__s)
    def __add__(self, other):
      return bytes( list(self) + list(other) )
    def as_str(self):
      ''' Back convert to a str, only meaningful for Python 2.
      '''
      return self.__s
      ##return ''.join( chr(_) for _ in self )

if __name__ == '__main__':
  import cs.py3_tests
  cs.py3_tests.selftest(sys.argv)
