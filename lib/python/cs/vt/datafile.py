#!/usr/bin/python -tt
#
# The basic flat file data store for vt blocks.
# These are kept in a directory accessed by a DataDir class.
# The file extension is .vtd
#       - Cameron Simpson <cs@cskk.id.au>
#

import os
from os import SEEK_SET, SEEK_CUR, SEEK_END, \
               O_CREAT, O_EXCL, O_RDONLY, O_WRONLY, O_APPEND
import sys
from threading import Lock
import time
from zlib import compress, decompress
from cs.fileutils import ReadMixin
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

class DataFile(MultiOpenMixin, ReadMixin):
  ''' A data file, storing data chunks in compressed form.
      This is the usual file based persistence layer of a local Store.

      A DataFile is a MultiOpenMixin and supports:
        .fetch(offset)  Fetch the uncompressed data chunk from `offset`.
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

  def tell(self):
    return lseek(self._rfd, 0, SEEK_CUR)

  def seek(self, offset):
    return lseek(self._rfd, offset, how=SEEK_SET)

  def datafrom(self, offset, readsize=None):
    ''' Yield data from the file starting at `offset`.
    '''
    if readsize is None:
      readsize = 512
    fd = self._rfd
    while True:
      data = os.pread(fd, readsize, offset)
      yield data
      offset += len(data)

  @staticmethod
  def data_record(data, no_compress=False):
    ''' Compose a data record for transcription to a DataFile.
    '''
    flags = 0
    if not no_compress:
      data2 = compress(data)
      if len(data2) < len(data):
        data = data2
        flags |= F_COMPRESSED
    return put_bs(flags) + put_bsdata(data)

  @staticmethod
  def read_record(fp, do_decompress=False):
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
    post_offset = fp.tell()
    if do_decompress and (flags & F_COMPRESSED):
      data = decompress(data)
      flags &= ~F_COMPRESSED
    return flags, data, post_offset

  def fetch_record(self, offset, do_decompress=False):
    ''' Fetch a record from the supplied `offset`. Return (flags, data, new_offset).
    '''
    return self.read_record(self.bufferfrom(offset), do_decompress=do_decompress)

  def fetch(self, offset):
    ''' Fetch the nucompressed data at `offset`.
    '''
    flags, data, _ = self.fetch_record(offset, do_decompress=True)
    assert flags == 0
    return data

  @staticmethod
  def scan_records(fp, do_decompress=False):
    ''' Generator yielding (flags, data, post_offset) from a data file from its current offset.
        `do_decompress`: decompress the scanned data, default False
    '''
    while True:
      yield read_record(fp, do_decompress=do_decompress)

  def scanfrom(self, offset, do_decompress=False):
    ''' Generator yielding (flags, data, post_offset) from the DataFile.
        `offset`: the starting offset for the scan
        `do_decompress`: decompress the scanned data, default False
    '''
    return self.scan_records(datafrom(offset), do_decompress=do_decompress)

  def add(self, data, no_compress=False):
    ''' Append a chunk of data to the file, return the store start and end offsets.
    '''
    if not self.readwrite:
      raise RuntimeError("%s: not readwrite" % (self,))
    bs = self.data_record(data, no_compress=no_compress)
    wfd = self._wfd
    with self._wlock:
      offset = os.lseek(wfd, 0, SEEK_CUR)
      os.write(wfd, bs)
    return offset, offset + len(bs)

if __name__ == '__main__':
  from .datafile_tests import selftest
  selftest(sys.argv)
