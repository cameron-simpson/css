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
  from struct import pack, unpack
  from .py3_for3 import raise3, exec_code, bytes, BytesFile

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
  from .py3_for2 import raise3, exec_code, bytes, BytesFile, pack, unpack

if __name__ == '__main__':
  import cs.py3_tests
  cs.py3_tests.selftest(sys.argv)
