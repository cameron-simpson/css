#!/usr/bin/env python3

''' The base classes for files storing data.
'''

from os import SEEK_END, lseek, write, pread
from collections.abc import Mapping, MutableMapping
from threading import RLock
from zlib import compress, decompress
from typeguard import typechecked
from cs.binary import PacketField, BSUInt, BSData
from cs.fileutils import shortpath
from cs.lex import cropped_repr
from cs.pfx import pfx_method
from .hash import HashCode
from cs.logutils import warning
from cs.pfx import Pfx, pfx_method, XP
from cs.resources import MultiOpenMixin
from .util import openfd_append, openfd_read

class BackingFileIndexEntry(PacketField):
  ''' An index entry for a backing file.
  '''

  # pylint: disable=super-init-not-called
  def __init__(self, offset, length):
    self.offset = offset
    self.length = length

  def __str__(self):
    return "%s(offset=%r,length=%r)" % (
        type(self).__name__, self.offset, self.length
    )

  @classmethod
  def from_value(cls, value):
    ''' Construct an instance from an `(offset,length)` tuple.
    '''
    return cls(*value)

  # pylint: disable=arguments-differ
  @classmethod
  def from_buffer(cls, bfr):
    ''' Construct a `BackingFileIndexEntry` from a `CornuCopyBuffer`.
    '''
    offset = BSUInt.value_from_buffer(bfr)
    length = BSUInt.value_from_buffer(bfr)
    return cls(offset, length)

  def transcribe(self):
    ''' Transcribe the offset and length.
    '''
    yield BSUInt.transcribe_value(self.offset)
    yield BSUInt.transcribe_value(self.length)

class BaseBackingFile(MutableMapping, MultiOpenMixin):
  ''' The basics of a data backing file.

      These store data chunks persistently
      and keep an index of their `(offset,length)` locations.
  '''

  @pfx_method
  ##@typechecked
  def __init__(
      self, path: str, *, hashclass: HashCode, data_record_class: PacketField,
      index: Mapping
  ):
    ''' Initialise the file.

        Parameters:
        * `path`: the pathname of the file
        * `hashclass`: the `HashCode` subclass
        * `data_record_class`: a `PacketField` subclass
          encoding the data for storage in the file
        * `index`: a `HashCode`->`(offset,length)` mapping
    '''
    self.path = path
    self.hashclass = hashclass
    self.data_record_class = data_record_class
    self.index = index
    self._lock = RLock()

  def __str__(self):
    return "%s:%s:%s(%r,index=%s)" % (
        type(self).__name__, self.hashclass.HASHNAME,
        self.data_record_class.__name__, shortpath(self.path), self.index
    )

  __repr__ = __str__

  @pfx_method
  def startup(self):
    ''' Open index.
    '''
    index = self.index
    with Pfx("open %s", index):
      try:
        index_open = index.open
      except AttributeError:
        warning("no .open method")
      else:
        open_index()

  @pfx_method
  def shutdown(self):
    ''' Close the index, close the file.
    '''
    for fd_name in '_rfd', '_wfd':
      fd = self.__dict__.get(fd_name)
      if fd is not None:
        closefd(fd)
        del self.__dict__[fd_name]
    index = self.index
    with Pfx("close %d", index):
      try:
        index_close = index.close
      except AttributeError:
        warning("no .close method")
      else:
        index_close()

  def __len__(self):
    return len(self.index)

  def keys(self):
    ''' Return an iterator of the index keys.
    '''
    return self.index.keys()

  __iter__ = keys

  def add(self, data: bytes):
    ''' Add `data` to the backing file. Return the `HashCode`.

        Note: if the data are already present, do not append to the file.
    '''
    index = self.index
    h = self.hashclass(data)
    if h not in index:
      data_record = self.data_record_class(data)
      data_record_bs = bytes(data_record)
      offset = self._append(data_record_bs)
      index[h] = offset, len(data_record_bs)
    return h

  def __setitem__(self, h, data):
    ''' Assignment form of `add(data)`.
    '''
    h2 = self.add(data)
    if h != h2:
      raise ValueError(
          "%s.__setitem__(h=%s,data=%d-bytes): self.add(data) returned %s" %
          (type(self).__name__, h, len(data), h2)
      )
    return h2

  def __delitem__(self, h):
    raise NotImplementedError("cannot delete")

  def _append(self, data_record_bs: bytes):
    ''' Append the binary record `data_record_bs` to the file,
        return the starting `offset`.
    '''
    wfd = self._wfd
    with self._lock:
      offset = lseek(wfd, 0, SEEK_END)
      n = write(wfd, data_record_bs)
    if n != len(data_record_bs):
      raise ValueError(
          "os.write(%d-bytes) wrote only %d bytes" % (len(data_record_bs), n)
      )
    return offset

  # pylint: disable=attribute-defined-outside-init
  def __getattr__(self, attr):
    if attr == '_rfd':
      # no ._rfd: create a new write data file and return the new rfd
      with self._lock:
        rfd = self.__dict__.get('_rfd')
        if rfd is None:
          rfd = self._rfd = openfd_read(self.path)
      return rfd
    if attr == '_wfd':
      # no ._wfd: create a new write data file and return the new wfd
      with self._lock:
        wfd = self.__dict__.get('_wfd')
        if wfd is None:
          wfd = self._wfd = openfd_append(self.path)
      return wfd
    raise AttributeError(attr)

  def data_record_for(self, h):
    ''' Obtain the data record for a `HashCode`.
    '''
    offset, length = self.index[h]
    rfd = self._rfd
    data_record_bs = pread(rfd, length, offset)
    if len(data_record_bs) != length:
      raise ValueError(
          "short pread from fd %d: asked for %d, got %d" %
          (rfd, length, len(data_record_bs))
      )
    data_record, post_offset = self.data_record_class.from_bytes(
        data_record_bs
    )
    if post_offset != len(data_record_bs):
      raise ValueError(
          "%s.value_from_bytes(%d-bytes): %d unparsed bytes: %r" % (
              type(self.data_record_class).__name__, len(data_record_bs),
              len(data_record_bs) - post_offset, data_record_bs[post_offset:]
          )
      )
    return data_record

  def __getitem__(self, h):
    ''' Return the data for the `HashCode` `h`.
    '''
    data_record = self.data_record_for(h)
    return data_record.value

class RawDataRecord(PacketField):
  ''' A raw data record ha no encoding: the bytes are read and written directly.
      The index has the offset and length.
  '''

  # pylint: disable=arguments-differ
  @classmethod
  def from_bytes(cls, bs, *, offset=0, length=None):
    ''' Decode a `RawDataRecord`: nothing to decode, use the supplied bytes directly.
    '''
    assert offset == 0
    assert length is None or length == len(bs)
    return cls(bs), len(bs)

  def transcribe(self):
    ''' Transcribe the `RawDataRecord`: the value is the bytes.
    '''
    return self.value

def RawBackingFile(path: str, **kw):
  ''' Return a backing file for raw data.
  '''
  return BaseBackingFile(path, data_record_class=RawDataRecord, **kw)

class CompressibleDataRecord(PacketField):
  ''' A data chunk file record for storage in a `.vtd` file.

      The record format is:
      * `flags`: `BSUInt`
      * `data`: `BSData`
  '''

  TEST_CASES = ((b'', b'\x00\x00'),)

  FLAG_COMPRESSED = 0x01

  # pylint: disable=super-init-not-called
  def __init__(self, data, *, is_compressed=None):
    ''' Initialise a `CompressibleDataRecord` directly.

        Parameters:
        * `data`: the data to store
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
    return "%s:%d(%s,%s)" % (
        type(self).__name__,
        len(self._data),
        "compressed" if self.is_compressed else "raw",
        cropped_repr(self._data),
    )

  __repr__ = __str__

  def __eq__(self, other):
    return self.data == other.data

  # pylint: disable=arguments-differ
  @classmethod
  def from_buffer(cls, bfr):
    ''' Parse a `CompressibleDataRecord` from a buffer.
    '''
    flags = BSUInt.value_from_buffer(bfr)
    data = BSData.value_from_buffer(bfr)
    is_compressed = (flags & cls.FLAG_COMPRESSED) != 0
    if is_compressed:
      flags &= ~cls.FLAG_COMPRESSED
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
      flags |= self.FLAG_COMPRESSED
    return flags

  @property
  def value(self):
    ''' `PacketField.value` support: the data bytes.
    '''
    return self.data

##  @property
##  def data_offset(self):
##    ''' The offset of the data chunk within the transcribed `DataRecord`.
##    '''
##    return (
##        len(BSUInt.transcribe_value(self.flags)) +
##        BSData.data_offset_for(self._data)
##    )
##
##  @property
##  def raw_data_length(self):
##    ''' The length of the raw data.
##    '''
##    return len(self._data)

@typechecked
def CompressibleBackingFile(path: str, **kw):
  ''' Return a `BackingFile` for `CompressibleDataRecord`s,
      the format used for `.vtd` files.
  '''
  return BaseBackingFile(path, data_record_class=CompressibleDataRecord, **kw)
