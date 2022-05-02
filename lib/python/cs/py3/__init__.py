#!/usr/bin/python -tt
#
# Python3 helpers to aid code sharing between python2 and python3.
#       - Cameron Simpson <cs@cskk.id.au> 28jun2012
#

r'''
Aids for code sharing between python2 and python3.

This package presents various names in python 3 flavour for common use in
python 2 and python 3.
'''

try:
  from configparser import ConfigParser
except ImportError:
  from ConfigParser import SafeConfigParser as ConfigParser  # type: ignore
from datetime import date, datetime
import os
try:
  from queue import Queue, PriorityQueue, Full as Queue_Full, Empty as Queue_Empty
except ImportError:
  from Queue import Queue, Full as Queue_Full, Empty as Queue_Empty  # type: ignore
  try:
    from Queue import PriorityQueue  # type: ignore
  except ImportError:
    pass
try:
  from subprocess import DEVNULL
except ImportError:
  DEVNULL = os.open(os.devnull, os.O_RDWR)
import sys
from time import strptime
try:
  from types import StringTypes  # type: ignore
except ImportError:
  StringTypes = (str,)

__version__ = '20200517-post'

DISTINFO = {
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [],
}

try:
  raw_input  # type: ignore
except NameError:
  raw_input = input
try:
  unicode
except NameError:
  unicode = str

if sys.hexversion >= 0x03000000:

  ustr = str

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
  joinbytes = ''.join

  def iteritems(o):
    return o.iteritems()

  def iterkeys(o):
    return o.iterkeys()

  def itervalues(o):
    return o.itervalues()

  input = raw_input
  _sorted = sorted

  def sorted(iterable, key=None, reverse=False):
    ''' Adaptor for Python 2 `sorted()` providing Python 3 API.
    '''
    return _sorted(iterable, None, key, reverse)

  input = raw_input
  from itertools import ifilter as filter, ifilterfalse as filterfalse  # type: ignore
  from ._for2 import raise3, raise_from, exec_code, ustr, \
                        bytes, BytesFile, joinbytes, \
                        pack, unpack # type: ignore

try:
  from struct import iter_unpack
except ImportError:
  from struct import calcsize

  def iter_unpack(fmt, buffer):
    ''' Drop in for `struct.iter_unpack`.
    '''
    chunk_size = calcsize(fmt)
    if chunk_size < 1:
      raise ValueError(
          "struct.calcsize(%r) gave %d, expected >= 1" % (fmt, chunk_size)
      )
    offset = 0
    while offset < len(buffer):
      yield unpack(fmt, buffer[offset:offset + chunk_size])
      offset += chunk_size

# fill in missing pread with weak workalike
try:
  pread = os.pread
except AttributeError:
  # implement our own pread
  # NB: not thread safe!
  from os import SEEK_CUR, SEEK_SET

  def pread(fd, size, offset):
    ''' Positional read from file descriptor, does not adjust the offset.

        This is a racy drop in for when `os.pread` is not provided.
    '''
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

try:
  date_fromisoformat = date.fromisoformat
except AttributeError:

  def date_fromisoformat(datestr):
    ''' Placeholder for `date.fromisoformat`.
    '''
    parsed = strptime(datestr, '%Y-%m-%d')
    return date(parsed.tm_year, parsed.tm_mon, parsed.tm_mday)

try:
  datetime_fromisoformat = datetime.fromisoformat
except AttributeError:

  def datetime_fromisoformat(datestr):
    ''' Placeholder for `datetime.fromisoformat`.
    '''
    parsed = strptime(datestr, '%Y-%m-%dT%H:%M:%S')
    return datetime(
        parsed.tm_year, parsed.tm_mon, parsed.tm_mday, parsed.tm_hour,
        parsed.tm_min, parsed.tm_sec
    )

if __name__ == '__main__':
  import cs.py3.tests  # type: ignore
  cs.py3.tests.selftest(sys.argv)
