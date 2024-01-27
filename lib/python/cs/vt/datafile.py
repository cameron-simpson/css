#!/usr/bin/env python3
#
# The basic flat file data store for vt blocks.
# These are kept in a directory accessed by a DataDir class.
# The file extension is .vtd
#       - Cameron Simpson <cs@cskk.id.au>
#

''' Implementation of DataFile: a file containing Block records.
'''

from contextlib import contextmanager
from enum import IntFlag
from os import SEEK_SET
from os.path import realpath
import sys
from threading import Lock, RLock
from zlib import compress, decompress

from icontract import require

from cs.binary import BSUInt, BSData, SimpleBinary
from cs.buffer import CornuCopyBuffer
from cs.context import stackattrs
from cs.fs import HasFSPath
from cs.logutils import warning
from cs.obj import SingletonMixin
from cs.pfx import pfx_call, pfx_method
from cs.resources import MultiOpenMixin

from .block import HashCodeBlock

DATAFILE_EXT = 'vtd'
DATAFILE_DOT_EXT = '.' + DATAFILE_EXT

class DataFlag(IntFlag):
  ''' Flag values for `DataFile` records.

      Defined flags:
      * `COMPRESSED`: the data are compressed using `zlib.compress`.
  '''
  COMPRESSED = 0x01

class DataRecord(SimpleBinary):
  ''' A data chunk file record for storage in a `.vtd` file.

      The record format is:
      * `flags`: `BSUInt`
      * `data`: `BSData`
  '''

  TEST_CASES = ((b'', b'\x00\x00'),)

  def __init__(self, data, is_compressed=None):
    ''' Initialise a `DataRecord` directly.

        Parameters:
        * `data`: the data to store
        * `is_compressed`: whether the data are already compressed

        Note that if `is_compressed` is not set
        we presume `data` is uncompressed
        and try to compress it if it is 16 bytes or more;
        we keep the compressed form if it achieves more than 10% compression.
    '''
    if is_compressed is None:
      if len(data) < 16:
        is_compressed = False
      else:
        zdata = compress(data)
        if len(zdata) < len(data) * 0.9:
          data = zdata
          is_compressed = True
        else:
          is_compressed = False
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
  def parse(cls, bfr):
    ''' Parse a `DataRecord` from a buffer.
    '''
    flags = BSUInt.parse_value(bfr)
    data = BSData.parse_value(bfr)
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

class DataFilePushable:
  ''' Read access to a data file, which stores data chunks in compressed form.
      This is the usual file based persistence layer of a local Store.
  '''

  def __init__(self, pathname):
    self.pathname = pathname

  @require(lambda self, offset: 0 <= offset <= len(self))
  def pushto_queue(self, Q, offset=0, runstate=None, progress=None):
    ''' Push the `Block`s from this `DataFile` to the Queue `Q`.

        Note that if the target store is a DataDirStore
        it is faster and simpler to move/copy the `.vtd` file
        into its `data` subdirectory directly.
        Of course, that may introduce redundant block copies.

        Parameters:
        * `Q`: queue on which to put blocks
        * `offset`: starting offset, default `0`.
        * `runstate`: optional `RunState` used to cancel operation.
    '''
    if progress:
      progress.total += len(self) - offset
    bfr = CornuCopyBuffer.from_filename(self.pathname, offset=offset)
    for DR in DataRecord.scan(bfr):
      if runstate and runstate.cancelled:
        return False
      data = DR.data
      Q.put(HashCodeBlock.promote(data))
      if progress:
        progress += len(data)
    return True

class DataFile(SingletonMixin, HasFSPath, MultiOpenMixin):
  ''' Management for a `.vtd` data file.
  '''

  @classmethod
  def _singleton_key(cls, fspath: str):
    return realpath(fspath)

  @pfx_method
  def __init__(self, fspath: str):
    if not fspath.endswith(DATAFILE_DOT_EXT):
      warning(f'fspath does not end with {DATAFILE_DOT_EXT!r}')
    self.fspath = fspath
    self.rf = None
    self._af = None
    self._lock = RLock()

  @contextmanager
  def startup_shutdown(self):
    ''' Open the file for read, close on exit.
    '''
    with open(self.fspath, 'rb') as rf:
      with stackattrs(self, rf=rf):
        try:
          yield
        finally:
          with self._lock:
            if self._af is not None:
              self._af.close()
              self._af = None

  def get_record(self, offset: int) -> DataRecord:
    ''' Read the `DataRecord` at `offset`.
    '''
    bfr = CornuCopyBuffer.from_file(self.rf)
    with self._lock:
      self.rf.seek(offset, SEEK_SET)
      DR = DataRecord.parse(bfr)
    return DR

  def scan(self, *, offset=0, **scan_kw):
    ''' Scan the file from `offset` (default `0`)
        and yield `DataRecord` instances.
        This honours the `BinaryMixin.scan` parameters
        such as `with_offsets`.
    '''
    with pfx_call(open, self.fspath, 'rb') as rf:
      if offset > 0:
        rf.seek(offset, SEEK_SET)
      bfr = CornuCopyBuffer.from_file(rf, offset=offset)
      yield from DataRecord.scan(bfr, **scan_kw)

  @property
  def wf(self):
    ''' The writable file for appending records to the `DataFile`.

        Note that the file is opened for append with no buffering.
    '''
    with self._lock:
      if self._af is None:
        X("DataFile.wf: open %r ...", self.fspath)
        self._af = pfx_call(open, self.fspath, 'ab', buffering=0)
      return self._af

  def append(self, data: bytes):
    ''' Append the `data` to the file.
        Return `(DR,offset,length)` being the `DataRecord` and the
        location and length of the resulting binary record.
    '''
    DR = DataRecord(data)
    wf = self.wf
    bs = bytes(DR)
    with self._lock:
      offset = wf.tell()
      length = wf.write(bs)
    assert length == len(bs)
    return DR, offset, length

if __name__ == '__main__':
  from .datafile_tests import selftest
  selftest(sys.argv)
