#!/usr/bin/python -tt
#
# Python3 helpers to aid code sharing between python2 and python3.
#       - Cameron Simpson <cs@cskk.id.au> 28jun2012
#

r'''
Aids for code sharing between python2 and python3.

Presents various names in python 3 flavour for common use in python 2 and python 3.
'''

try:
  from configparser import ConfigParser
except ImportError:
  from ConfigParser import SafeConfigParser as ConfigParser
import os
try:
  from queue import Queue, PriorityQueue, Full as Queue_Full, Empty as Queue_Empty
except ImportError:
  from Queue import Queue, Full as Queue_Full, Empty as Queue_Empty
  try:
    from Queue import PriorityQueue
  except ImportError:
    pass
import sys
try:
  from types import StringTypes
except ImportError:
  StringTypes = (str,)

DISTINFO = {
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
        ],
    'install_requires': [],
}

if sys.hexversion >= 0x03000000:

  unicode = str
  def ustr(s, e='utf-8', errors='strict'):
    ''' Upgrade string to unicode: no-op for python 3.
    '''
    return s
  def iteritems(o):
    return o.items()
  def iterkeys(o):
    return o.keys()
  def itervalues(o):
    return o.values()
  from builtins import sorted, filter, bytes, input
  from itertools import filterfalse
  from struct import pack, unpack
  from ._for3 import raise3, raise_from, exec_code, BytesFile, joinbytes

else:

  globals()['unicode'] = unicode
  bytesjoin = ''.join
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
  from ._for2 import raise3, raise_from, exec_code, ustr, \
                        bytes, BytesFile, joinbytes, \
                        pack, unpack

try:
  from struct import iter_unpack
except ImportError:
  from struct import calcsize
  def iter_unpack(fmt, buffer):
    chunk_size = calcsize(fmt)
    if chunk_size < 1:
      raise ValueError("struct.calcsize(%r) gave %d, expected >= 1" % (fmt, chunk_size))
    offset = 0
    while offset < len(buffer):
      yield unpack(fmt, buffer[offset:offset+chunk_size])
      offset += chunk_size

# fill in missing pread with weak workalike
try:
  pread = os.pread
except AttributeError:
  # implement our own pread
  # NB: not thread safe!
  from os import SEEK_CUR, SEEK_SET
  def pread(fd, size, offset):
    offset0 = os.lseek(fd, 0, SEEK_CUR)
    os.lseek(fd, offset, SEEK_SET)
    chunks = []
    while size > 0:
      data = os.read(fd, size)
      if not data:
        break
      chunks.append(data)
      size -= len(data)
    os.lseek(fd, offset0, SEEK_SET)
    data = b''.join(chunks)
    return data

if __name__ == '__main__':
  import cs.py3_tests
  cs.py3_tests.selftest(sys.argv)
