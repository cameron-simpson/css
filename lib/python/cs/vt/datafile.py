#!/usr/bin/python -tt
#
# The basic flat file data store for vt blocks.
# These are kept in a directory accessed by a DataDir class.
# The file extension is .vtd
#       - Cameron Simpson <cs@cskk.id.au>
#

''' Implementation of DataFile: a file containing Block records.
'''

from enum import IntFlag
from fcntl import flock, LOCK_EX, LOCK_UN
import os
from os import SEEK_END, \
               O_CREAT, O_EXCL, O_RDONLY, O_WRONLY, O_APPEND
import sys
from threading import Lock
from zlib import compress, decompress
from cs.fileutils import ReadMixin, datafrom_fd
from cs.pfx import Pfx
from cs.resources import MultiOpenMixin
from cs.serialise import put_bs, read_bs, put_bsdata, read_bsdata

DATAFILE_EXT = 'vtd'
DATAFILE_DOT_EXT = '.' + DATAFILE_EXT

class DataFlag(IntFlag):
  ''' Flag values for DataFile records.
      COMPRESSED: the data are compressed using zlib.compress.
  '''
  COMPRESSED = 0x01

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
    self._rfd = None
    self._rlock = None
    self._wfd = None
    self._wlock = None

  def __str__(self):
    return "DataFile(%s)" % (self.pathname,)

  def startup(self):
    ''' Start up the DataFile: open the read and write file descriptors.
    '''
    with Pfx("%s.startup: open(%r)", self, self.pathname):
      rfd = os.open(self.pathname, O_RDONLY)
      self._rfd = rfd
      self._rlock = Lock()
      if self.readwrite:
        self._wfd = os.open(self.pathname, O_WRONLY | O_APPEND)
        self._wlock = Lock()

  def shutdown(self):
    ''' Shut down the DataFIle: close read and write file descriptors.
    '''
    if self.readwrite:
      os.close(self._wfd)
      self._wfd = None
    os.close(self._rfd)
    self._rfd = None

  def datafrom(self, offset, readsize=None):
    ''' Yield data from the file starting at `offset`.
    '''
    if readsize is None:
      # Default read size.
      # This number is arbitrary, chosen on the basis that the
      # average size of blocks for random data is around 4093 bytes
      # (from vt.scan) and the size for parsed data is often much
      # smaller.
      readsize = 2048
    return datafrom_fd(self._rfd, offset, readsize)

  @staticmethod
  def data_record(data, no_compress=False):
    ''' Compose a data record for transcription to a DataFile.
    '''
    flags = DataFlag(0)
    if not no_compress:
      data2 = compress(data)
      if len(data2) < len(data):
        data = data2
        flags |= DataFlag.COMPRESSED
    return put_bs(flags) + put_bsdata(data)

  @staticmethod
  def read_record(fp, do_decompress=False):
    ''' Read a data chunk from a file at its current offset. Return (flags, chunk, post_offset).
        If do_decompress is true and flags&DataFlag.COMPRESSED, strip that
        flag and decompress the data before return.
        Raises EOFError on premature end of file.
    '''
    flags = DataFlag(read_bs(fp))
    data = read_bsdata(fp)
    post_offset = fp.tell()
    if do_decompress and (flags & DataFlag.COMPRESSED):
      data = decompress(data)
      flags &= ~DataFlag.COMPRESSED
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
        The fcntl.flock function is used to hold an OS level lock
        for the duration of the write to support shared use of the
        file.
    '''
    if not self.readwrite:
      raise RuntimeError("%s: not readwrite" % (self,))
    bs = self.data_record(data, no_compress=no_compress)
    wfd = self._wfd
    with self._wlock:
      try:
        flock(wfd, LOCK_EX)
      except OSError:
        is_locked = False
      else:
        is_locked = True
      offset = os.lseek(wfd, 0, SEEK_END)
      os.write(wfd, bs)
      if is_locked:
        flock(wfd, LOCK_UN)
    return offset, offset + len(bs)

if __name__ == '__main__':
  from .datafile_tests import selftest
  selftest(sys.argv)
