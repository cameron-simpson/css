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
from zlib import decompress
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
  ''' A data chunk file record for storage in a `.vtd` file.

      The record format is:
      * `flags`: `BSUInt`
      * `data`: `BSData`
  '''

  TEST_CASES = ((b'', b'\x00\x00'),)

  def __init__(self, data, is_compressed=False):
    self._data = data
    self.is_compressed = is_compressed

  def __str__(self):
    return "%s(%d-bytes,%s,%r)" % (
        type(self).__name__,
        len(self._data),
        "compressed" if self.is_compressed else "raw",
        self._data,
    )

  __repr__ = __str__

  def __eq__(self, other):
    return self.data == other.data

  @classmethod
  def from_buffer(cls, bfr):
    ''' Parse a DataRecord from a buffer.
    '''
    flags = BSUInt.value_from_buffer(bfr)
    data = BSData.value_from_buffer(bfr)
    is_compressed = (flags & DataFlag.COMPRESSED) != 0
    if is_compressed:
      flags &= ~DataFlag.COMPRESSED
    if flags:
      raise ValueError("unsupported flags: 0x%02x" % (flags,))
    return cls(data, is_compressed=is_compressed)

  def transcribe(self):
    ''' Transcribe this data chunk as a data record.
    '''
    yield BSUInt.transcribe_value(self.flags)
    yield BSData.transcribe_value(self._data)

  @property
  def data(self):
    ''' The uncompressed data.
    '''
    data = self._data
    if self.is_compressed:
      return decompress(data)
    return data

  @property
  def flags(self):
    ''' The flags for this `DataRecord`.
    '''
    flags = 0x00
    if self.is_compressed:
      flags |= DataFlag.COMPRESSED
    return flags

  @property
  def data_offset(self):
    ''' The offset of the data chunk within the transcribed `DataRecord`.
    '''
    return (
        len(BSUInt.transcribe_value(self.flags)) +
        BSData.data_offset_for(self._data)
    )

  @property
  def raw_data_length(self):
    ''' The length of the raw data.
    '''
    return len(self._data)

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
    bfr = CornuCopyBuffer(self.datafrom(offset), offset=offset)
    for pre_offset, DR in DataRecord.parse_buffer_with_offsets(bfr):
      if runstate and runstate.cancelled:
        return False
      data = DR.data
      Q.put((data, bfr.offset - pre_offset))
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
