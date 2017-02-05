#!/usr/bin/python -tt
#
# The basic flat file data store for venti blocks.
# These are kept in a directory accessed by a DataDir class.
#       - Cameron Simpson <cs@zip.com.au>
#

import sys
from collections import namedtuple
import os
from os import SEEK_SET, SEEK_END
import errno
from zlib import compress, decompress
from cs.excutils import LogExceptions
import cs.logutils; cs.logutils.X_via_tty = True
from cs.logutils import D, X, XP, debug, warning, error, exception, Pfx
from cs.obj import O
from cs.resources import MultiOpenMixin
from cs.seq import imerge
from cs.serialise import get_bs, put_bs, read_bs, put_bsdata, read_bsdata

F_COMPRESSED = 0x01

class DataFlags(int):
  ''' Subclass of int to label stuff nicely.
  '''

  def __repr__(self):
    return "<DataFlags %d>" % (self,)

  def __str__(self):
    if self == 0:
      return '_'
    flags = self
    s = ''
    if flags & F_COMPRESSED:
      s += 'Z'
      flags &= ~F_COMPRESSED
    assert flags == 0
    return s

  @property
  def compressed(self):
    return self & F_COMPRESSED

def read_chunk(fp, do_decompress=False):
  ''' Read a data chunk from a file at its current offset. Return (flags, chunk, post_offset).
      If do_decompress is true and flags&F_COMPRESSED, strip that
      flag and decompress the data before return.
      Raises EOFError on premature end of file.
  '''
  flags = read_bs(fp)
  if (flags & ~F_COMPRESSED) != 0:
    raise ValueError("flags other than F_COMPRESSED: 0x%02x" % ((flags & ~F_COMPRESSED),))
  flags = DataFlags(flags)
  data = read_bsdata(fp)
  offset = fp.tell()
  if do_decompress and (flags & F_COMPRESSED):
    data = decompress(data)
    flags &= ~F_COMPRESSED
  return flags, data, offset

def write_chunk(fp, data, no_compress=False):
  ''' Write a data chunk to a file at the current position, return the starting and ending offsets.
      If not no_compress, try to compress the chunk.
      Note: does _not_ call .flush().
  '''
  flags = 0
  if not no_compress:
    data2 = compress(data)
    if len(data2) < len(data):
      data = data2
      flags |= F_COMPRESSED
    offset = fp.tell()
    fp.write(put_bs(flags))
    fp.write(put_bsdata(data))
  return offset, fp.tell()

class DataFile(MultiOpenMixin):
  ''' A cs.venti data file, storing data chunks in compressed form.
      This is the usual file based persistence layer of a local venti Store.

      A DataFile is a MultiOpenMixin and supports:
        .flush()        Flush any pending output to the file.
        .fetch(offset)  Fetch the data chunk from `offset`.
        .add(data)      Store data chunk, return (offset, offset2) indicating its location.
        .scan([do_decompress=],[offset=0])
                        Scan the data file and yield (offset, flags, zdata, offset2) tuples.
                        This can take place during other activity.
  '''

  def __init__(self, pathname, do_create=False, readwrite=False, lock=None):
    MultiOpenMixin.__init__(self, lock=lock)
    self.pathname = pathname
    self.readwrite = readwrite
    if do_create and not readwrite:
      raise ValueError("do_create=true requires readwrite=true")
    self.appending = False
    if do_create:
      fd = os.open(pathname, os.O_CREAT|os.O_EXCL|os.O_RDWR)
      os.close(fd)

  def __str__(self):
    return "DataFile(%s)" % (self.pathname,)

  def startup(self):
    with Pfx("%s.startup: open(%r)", self, self.pathname):
      self.fp = open(self.pathname, ( "a+b" if self.readwrite else "r+b" ))

  def shutdown(self):
    self.flush()
    self.fp.close()
    self.fp = None

  def flush(self):
    ''' Flush any buffered writes to the filesystem.
    '''
    with self._lock:
      if self.appending:
        self.fp.flush()

  def fetch(self, offset):
    flags, data, offset2 = self._fetch(offset, do_decompress=True)
    if flags:
      raise ValueError("unhandled flags: 0x%02x" % (flags,))
    return data

  def _fetch(self, offset, do_decompress=False):
    ''' Fetch data bytes from the supplied offset.
    '''
    fp = self.fp
    with self._lock:
      if self.appending:
        fp.flush()
        self.appending = False
      if fp.tell() != offset:
        fp.flush()
        fp.seek(offset, SEEK_SET)
      flags, data, offset2 = read_chunk(fp, do_decompress=do_decompress)
    return flags, data, offset2

  def add(self, data, no_compress=False):
    ''' Append a chunk of data to the file, return the store start and end offsets.
    '''
    if not self.readwrite:
      raise RuntimeError("%s: not readwrite" % (self,))
    fp = self.fp
    with self._lock:
      if not self.appending:
        self.appending = True
        fp.flush()
      fp.seek(0, SEEK_END)
      return write_chunk(fp, data, no_compress=no_compress)

  def scan(self, do_decompress=False, offset=0):
    ''' Scan the data file and yield (start_offset, flags, zdata, end_offset) tuples.
        Start the scan ot `offset`, default 0.
        If `do_decompress` is true, decompress the data and strip
        that flag value.
        This can be used in parallel with other activity, though
        it may impact performance.
    '''
    with self:
      while True:
        try:
          flags, data, offset2 = self._fetch(offset, do_decompress=do_decompress)
        except EOFError:
          break
        yield offset, flags, data, offset2
        offset = offset2

if __name__ == '__main__':
  import cs.venti.datafile_tests
  cs.venti.datafile_tests.selftest(sys.argv)
