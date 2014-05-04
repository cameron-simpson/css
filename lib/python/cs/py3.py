#!/usr/bin/python -tt
#
# Python3 helpers to aid code sharing between python2 and python3.
#       - Cameron Simpson <cs@zip.com.au> 28jun2012
#

import sys

if sys.hexversion < 0x03000000:

  globals()['unicode'] = unicode
  from types import StringTypes
  makebytes = lambda bytevals: b''.join( chr(bv) for bv in bytevals )
  def ustr(s, e='utf-8'):
    ''' Upgrade str to unicode, if it is a str. Leave other types alone.
    '''
    if isinstance(s, str):
      try:
        s = s.decode(e, 'strict')
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
  def sorted(iterable, key=None, reversed=False):
    if key is None:
      key = lambda x: x
    return _sorted(iterable, cmp, key, reversed)

else:

  unicode = str
  StringTypes = (str,)
  makebytes = bytes
  def ustr(s, e='utf-8'):
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
  from builtins import sorted

def raise3(exc_type, exc_value, exc_traceback):
  if sys.hexversion >= 0x03000000:
    raise exc_type(exc_value).with_traceback(exc_traceback)
  else:
    # subterfuge to let this pass a python3 parser; ugly
    exec('raise exc_type, exc_value, exc_traceback')
