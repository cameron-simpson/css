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
  from io import BytesIO, StringIO
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

else:

  globals()['unicode'] = unicode
  from types import StringTypes
  def ustr(s, e='utf-8', errors='strict'):
    ''' Upgrade str to unicode, if it is a str. Leave other types alone.
    '''
    if isinstance(s, str):
      try:
        s = s.decode(e, errors=errors)
      except UnicodeDecodeError as ude:
        from cs.logutils import warning
        warning("cs.py3.ustr(): %s: s = %s %r", ude, type(s), s)
        s = s.decode(e, 'replace')
    return s
  try:
    from cStringIO import StringIO as BytesIO
  except ImportError:
    from StringIO import StringIO as BytesIO
  StringIO = BytesIO    # horribly wrong, I know
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

  class bytes(str):
    def __new__(cls, arg):
      from cs.logutils import X
      try:
        bytevals = iter(arg)
      except TypeError:
        bytevals = [ 0 for i in range(arg) ]
      s = ''.join( chr(b) for b in bytevals )
      self = str.__new__(cls, s)
      return self
    def __repr__(self):
      return 'b' + str.__repr__(self)
    def __getitem__(self, index):
      s2 = str.__getitem__(self, index)
      if isinstance(index, slice):
        return bytes( ord(ch) for ch in s2 )
      return ord(s2[0])
    def __contains__(self, b):
      return str.__contains__(self, chr(b))

def raise3(exc_type, exc_value, exc_traceback):
  if sys.hexversion >= 0x03000000:
    raise exc_type(exc_value).with_traceback(exc_traceback)
  else:
    # subterfuge to let this pass a python3 parser; ugly
    exec('raise exc_type, exc_value, exc_traceback')

if __name__ == '__main__':
  import cs.py3_tests
  cs.py3_tests.selftest(sys.argv)
