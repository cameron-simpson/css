#!/usr/bin/python -tt
#
# The basic flat file data store for venti blocks.
# These are kept in a directory accessed by a DataDir class.
#       - Cameron Simpson <cs@cskk.id.au>
#

import os
from os import SEEK_SET, SEEK_CUR, SEEK_END, \
               O_CREAT, O_EXCL, O_RDONLY, O_WRONLY, O_APPEND
import sys
from threading import Lock
import time
from zlib import compress, decompress
from cs.buffer import CornuCopyBuffer
from cs.fileutils import fdreader
from cs.logutils import info
from cs.pfx import Pfx
from cs.resources import MultiOpenMixin
from cs.serialise import put_bs, read_bs, put_bsdata, read_bsdata

DATAFILE_EXT = 'vtd'
DATAFILE_DOT_EXT = '.' + DATAFILE_EXT

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

def scan_chunks(fp, do_decompress=False):
  ''' Read data chunks from `fp` and yield (offset, flags, data, offset2).
      Raises EOFError on premature end of file.
  '''
  offset = fp.tell()
  while True:
    flags, data, offset2 = read_chunk(fp)
    yield offset, flags, data, offset2
    offset = offset2

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
      fd = os.open(pathname, O_CREAT | O_EXCL | O_WRONLY)
      os.close(fd)

  def __str__(self):
    return "DataFile(%s)" % (self.pathname,)

  def startup(self):
    with Pfx("%s.startup: open(%r)", self, self.pathname):
      rfd = os.open(self.pathname, O_RDONLY)
      self._rfd = rfd
      self._rbuf = CornuCopyBuffer(fdreader(rfd, 16384))
      self._rlock = Lock()
      if self.readwrite:
        self._wfd = os.open(self.pathname, O_WRONLY | O_APPEND)
        os.lseek(self._wfd, 0, SEEK_END)
        self._wlock = Lock()

  def shutdown(self):
    if self.readwrite:
      os.close(self._wfd)
      del self._wfd
    os.close(self._rfd)
    del self._rfd

  def fetch(self, offset):
    flags, data, offset2 = self._fetch(offset, do_decompress=True)
    if flags:
      raise ValueError("unhandled flags: 0x%02x" % (flags,))
    return data

  def _fetch(self, offset, do_decompress=False):
    ''' Fetch data bytes from the supplied offset.
    '''
    rfd = self._rfd
    bfr = self._rbuf
    with self._rlock:
      if bfr.offset != offset:
        os.lseek(rfd, offset, SEEK_SET)
        bfr = self._rbuf = CornuCopyBuffer(fdreader(rfd, 16384), offset=offset)
      flags, data, offset2 = read_chunk(bfr, do_decompress=do_decompress)
    return flags, data, offset2

  def add(self, data, no_compress=False):
    ''' Append a chunk of data to the file, return the store start and end offsets.
    '''
    if not self.readwrite:
      raise RuntimeError("%s: not readwrite" % (self,))
    data2 = compress(data)
    flags = 0
    if len(data2) < len(data):
      data = data2
      flags |= F_COMPRESSED
    bs = put_bs(flags) + put_bsdata(data)
    wfd = self._wfd
    with self._wlock:
      offset = os.lseek(wfd, 0, SEEK_CUR)
      os.write(wfd, bs)
    return offset, offset + len(bs)

def scan_datafile(pathname, offset=None, do_decompress=False):
  ''' Scan a data file and yield (start_offset, flags, zdata, end_offset) tuples.
      Start the scan ot `offset`, default 0.
      If `do_decompress` is true, decompress the data and strip
      that flag value.
  '''
  start = time.time()
  if offset is None:
    offset = 0
  offset0 = offset
  D = DataFile(pathname)
  with D:
    while True:
      try:
        flags, data, offset2 = D._fetch(offset, do_decompress=do_decompress)
      except EOFError:
        break
      yield offset, flags, data, offset2
      offset = offset2
  end = time.time()
  if offset > offset0 and end > start:
    info("%r: scanned %d bytes in %ss at %sB/s",
         pathname, offset - offset0, end - start, (offset - offset0) / (end - start))

if __name__ == '__main__':
  import cs.venti.datafile_tests
  cs.venti.datafile_tests.selftest(sys.argv)
