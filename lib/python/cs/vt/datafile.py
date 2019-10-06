#!/usr/bin/env python3
#
# The basic flat file data store for vt blocks.
# These are kept in a directory accessed by a DataDir class.
# The file extension is .vtd
#       - Cameron Simpson <cs@cskk.id.au>
#

''' Implementation of DataFile: a file containing Block records.
'''

from enum import IntFlag
import os
from os import (
    fstat,
)
from stat import S_ISREG
import sys
from zlib import compress, decompress
from icontract import require
from cs.binary import BSUInt, BSData, PacketField
from cs.buffer import CornuCopyBuffer
from cs.fileutils import ReadMixin, datafrom_fd
from cs.resources import MultiOpenMixin
from . import Lock
from .util import createpath, openfd_read, openfd_append, append_data

DATAFILE_EXT = 'vtd'
DATAFILE_DOT_EXT = '.' + DATAFILE_EXT

class DataFlag(IntFlag):
  ''' Flag values for DataFile records.

      `COMPRESSED`: the data are compressed using zlib.compress.
  '''
  COMPRESSED = 0x01

class DataRecord(PacketField):
  ''' A data chunk file record.
  '''

  TEST_CASES = ((b'', b'\x01\x08x\x9c\x03\x00\x00\x00\x00\x01'),)

  def __init__(self, data, is_compressed=False):
    self._data = data
    self._is_compressed = is_compressed

  def __str__(self):
    return "%s(%d-bytes,%s)" % (
        type(self).__name__,
        len(self._data),
        "compressed" if self._is_compressed else "raw",
    )

  def __eq__(self, other):
    return self.data == other.data

  @classmethod
  def from_buffer(cls, bfr):
    ''' Parse a DataRecord from a buffer.
    '''
    flags = BSUInt.value_from_buffer(bfr)
    data = BSData.value_from_buffer(bfr)
    is_compressed = (flags & DataFlag.COMPRESSED) != 0
    flags &= ~DataFlag.COMPRESSED
    if flags:
      raise ValueError("unsupported flags: 0x%02x" % (flags,))
    return cls(data, is_compressed=is_compressed)

  @classmethod
  def from_fd(cls, rfd, offset=None):
    ''' Parse a DataRecord from a readable file descriptor.
    '''
    return cls.from_buffer(CornuCopyBuffer(datafrom_fd(rfd, offset=offset)))

  def transcribe(self, uncompressed=False):
    ''' Transcribe this data chunk as a data record.
    '''
    data = self._data
    is_compressed = self._is_compressed
    if uncompressed:
      flags = 0x00
      if is_compressed:
        data = decompress(data)
    else:
      flags = DataFlag.COMPRESSED
      if not is_compressed:
        data = compress(data)
    yield BSUInt.transcribe_value(flags)
    yield BSData.transcribe_value(data)

  @property
  def data(self):
    ''' The uncompressed data.
    '''
    raw_data = self._data
    if self._is_compressed:
      raw_data = decompress(raw_data)
      self._data = raw_data
      self._is_compressed = False
    return raw_data

class DataFileReader(MultiOpenMixin, ReadMixin):
  ''' Read access to a data file, storing data chunks in compressed form.
      This is the usual file based persistence layer of a local Store.
  '''

  def __init__(self, pathname):
    MultiOpenMixin.__init__(self)
    self.pathname = pathname
    self._rfd = None
    self._rlock = None

  def __str__(self):
    return "%s(%s)" % (
        type(self).__name__,
        self.pathname,
    )

  def startup(self):
    ''' Start up the DataFile: open the read and write file descriptors.
    '''
    rfd = openfd_read(self.pathname)
    S = fstat(rfd)
    if not S_ISREG(S.st_mode):
      raise RuntimeError(
          "fd %d: not a regular file: mode=0o%o: %r" %
          (rfd, S.st_mode, self.pathname)
      )
    self._rfd = rfd
    self._rlock = Lock()

  def shutdown(self):
    ''' Shut down the DataFIle: close read and write file descriptors.
    '''
    os.close(self._rfd)
    self._rfd = None
    self._rlock = None

  def __len__(self):
    return os.fstat(self._rfd).st_size

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

  def fetch_record(self, offset):
    ''' Fetch a DataRecord from the supplied `offset`.
    '''
    return DataRecord.from_buffer(self.bufferfrom(offset))

  def fetch(self, offset):
    ''' Fetch the nucompressed data at `offset`.
    '''
    return self.fetch_record(offset).data

  @staticmethod
  def scanbuffer(bfr):
    ''' Generator yielding `(DataRecords,post_offset)` from a DataFile.

        Parameters:
        * `bfr`: the buffer.
    '''
    for record in DataRecord.parse_buffer(bfr):
      yield record, bfr.offset

  def scanfrom(self, offset=0):
    ''' Generator yielding `(DataRecord,post_offset)` from the
        DataFile starting from `offset`, default `0`.
    '''
    return self.scanbuffer(self.bufferfrom(offset))

  @require(lambda self, offset: 0 <= offset <= len(self))
  def pushto_queue(self, Q, offset=0, runstate=None, progress=None):
    ''' Push the Blocks from this DataFile to the Store `S2`.

        Note that if the target store is a DataDirStore
        it is faster and simpler to move/copy the .vtd file
        into its `data` subdirectory directly.
        Of course, that may introduce redundant block copies.

        Parameters:
        * `Q`: queue on which to put blocks
        * `offset`: starting offset, default `0`.
        * `runstate`: optional RunState used to cancel operation.
    '''
    if progress:
      progress.total += len(self) - offset
    for DR, post_offset in self.scanfrom(offset=offset):
      if runstate and runstate.cancelled:
        return False
      data = DR.data
      Q.put((data, post_offset - offset))
      offset = post_offset
    return True

class DataFileWriter(MultiOpenMixin):
  ''' Append access to a data file, storing data chunks in compressed form.
  '''

  def __init__(self, pathname, do_create=False):
    MultiOpenMixin.__init__(self)
    self.pathname = pathname
    if do_create:
      createpath(pathname)
    self._wfd = None
    self._wlock = None

  def __str__(self):
    return "%s(%s)" % (
        type(self).__name__,
        self.pathname,
    )

  def startup(self):
    ''' Start up the DataFile: open the read and write file descriptors.
    '''
    self._wfd = openfd_append(self.pathname)
    self._wlock = Lock()

  def shutdown(self):
    ''' Shut down the DataFIle: close read and write file descriptors.
    '''
    os.close(self._wfd)
    self._wfd = None
    self._wlock = None

  def add(self, data):
    ''' Append a chunk of data to the file, return the store start
        and end offsets.

        The fcntl.flock function is used to hold an OS level lock
        for the duration of the write to support shared use of the
        file.
    '''
    bs = bytes(DataRecord(data))
    wfd = self._wfd
    with self._wlock:
      offset = append_data(wfd, bs)
    return offset, offset + len(bs)

if __name__ == '__main__':
  from .datafile_tests import selftest
  selftest(sys.argv)
