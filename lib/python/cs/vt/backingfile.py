#!/usr/bin/env python3

''' The base classes for files storing data.
'''

from collections.abc import Mapping, MutableMapping
from os import SEEK_END, lseek, write, pread, close as closefd
from os.path import isfile as isfilepath, splitext
from threading import RLock
from zlib import compress, decompress
from typeguard import typechecked
from icontract import require
from cs.binary import (
    AbstractBinary, BinarySingleValue, BinaryMultiValue, SimpleBinary, BSUInt,
    BSData
)
from cs.fileutils import shortpath
from cs.lex import cropped_repr
from cs.logutils import warning
from cs.pfx import Pfx, pfx_method
from cs.resources import MultiOpenMixin
from .hash import HashCode, HashCodeUtilsMixin, DEFAULT_HASHCLASS
from .index import choose as choose_indexclass
from .store import MappingStore
from .util import openfd_append, openfd_read

class BackingFileIndexEntry(BinaryMultiValue('BackingFileIndexEntry',
                                             dict(offset=BSUInt,
                                                  length=BSUInt))):
  ''' An index entry for a backing file.
  '''

# pylint: disable=too-many-ancestors
class BackingFile(MutableMapping, MultiOpenMixin):
  ''' The basics of a data backing file.

      These store data chunks persistently
      and keep an index of their `(offset,length)` locations.
  '''

  @pfx_method
  ##@typechecked
  def __init__(
      self, path: str, *, hashclass: HashCode,
      data_record_class: AbstractBinary, index: Mapping
  ):
    ''' Initialise the file.

        Parameters:
        * `path`: the pathname of the file
        * `hashclass`: the `HashCode` subclass
        * `data_record_class`: an `AbstractBinary` subclass
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
        index_open()

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
    h = self.hashclass.from_chunk(data)
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
    index_entry = self.index[h]
    offset = index_entry.offset
    length = index_entry.length
    rfd = self._rfd
    data_record_bs = pread(rfd, length, offset)
    if len(data_record_bs) != length:
      raise ValueError(
          "short pread from fd %d: asked for %d, got %d" %
          (rfd, length, len(data_record_bs))
      )
    data_record = self.data_record_class.from_bytes(data_record_bs)
    return data_record

  def __getitem__(self, h):
    ''' Return the data for the `HashCode` `h`.
    '''
    return self.data_record_for(h).data

class RawDataRecord(BinarySingleValue):
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
    return cls(bs)

  @property
  def data(self):
    ''' Alias `.value` as `.data`.
    '''
    return self.value

  def transcribe(self):
    ''' Transcribe the `RawDataRecord`: the value is the bytes.
    '''
    return self.value

def RawBackingFile(path: str, **kw):
  ''' Return a backing file for raw data.
  '''
  return BackingFile(path, data_record_class=RawDataRecord, **kw)

class CompressibleDataRecord(SimpleBinary):
  ''' A data chunk file record for storage in a `.vtd` file.

      The record format is:
      * `flags`: `BSUInt`
      * `data`: `BSData`
  '''

  TEST_CASES = ((b'', b'\x00\x00'),)

  FLAG_COMPRESSED = 0x01

  # pylint: disable=super-init-not-called
  def __init__(self, _data, *, is_compressed=None):
    ''' Initialise a `CompressibleDataRecord` directly.

        Parameters:
        * `_data`: the data to store
    '''
    if is_compressed is None:
      if len(_data) < 16:
        is_compressed = False
      else:
        zdata = compress(_data)
        if len(zdata) < len(_data) * 0.9:
          _data = zdata
          is_compressed = True
        else:
          is_compressed = False
    self._data = _data
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
  def parse(cls, bfr):
    ''' Parse a `CompressibleDataRecord` from a buffer.
    '''
    flags = BSUInt.parse_value(bfr)
    data = BSData.parse_value(bfr)
    is_compressed = (flags & cls.FLAG_COMPRESSED) != 0
    if is_compressed:
      flags &= ~cls.FLAG_COMPRESSED
    if flags:
      raise ValueError("unsupported flags: 0x%02x" % (flags,))
    return cls(_data=data, is_compressed=is_compressed)

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
  return BackingFile(path, data_record_class=CompressibleDataRecord, **kw)

# pylint: disable=too-many-ancestors
class BinaryHashCodeIndex(Mapping, HashCodeUtilsMixin, MultiOpenMixin):
  ''' A thin wrapper for an arbitrary `bytes`->`bytes` mapping
      used to map `HashCode`s to index entries.
  '''

  @pfx_method
  @require(
      lambda hashclass: issubclass(hashclass, HashCode) and hashclass is
      not HashCode
  )
  def __init__(self, *, hashclass, binary_index, index_entry_class):
    ''' Initialise the index.

        Parameters:
        * `hashclass`: the `HashCode` subclass to index
        * `binary_index`: a mapping of `bytes`->`bytes`
          to map hashcode bytes to index entry binary records
        * `index_entry_class`: a class for index entries
          with a `.from_value(*value)` method
          and a `__bytes__()` method
    '''
    self.binary_index = binary_index
    self.hashclass = hashclass
    self.index_entry_class = index_entry_class
    self._index_entry_decode = index_entry_class.from_bytes

  def __str__(self):
    return "%s(hashclass=%s,index_entry_class=%s,binary_index=%s)" % (
        type(self).__name__, self.hashclass.HASHNAME,
        type(self.index_entry_class).__name__, self.binary_index
    )

  def startup(self):
    ''' Open the binary index.
    '''
    open_method = getattr(self.binary_index, 'open', None)
    if open_method:
      open_method()

  def shutdown(self):
    ''' Close the binary index.
    '''
    close_method = getattr(self.binary_index, 'close', None)
    if close_method:
      close_method()

  def __len__(self):
    return len(self.binary_index)

  def keys(self):
    return map(self.hashclass.from_hashbytes, self.binary_index.keys())

  __iter__ = keys

  def __getitem__(self, hashcode):
    ''' Retrieve the index entry for `hashcode`.
    '''
    index_entry_bs = self.binary_index[hashcode]
    index_entry = self._index_entry_decode(index_entry_bs)
    return index_entry

  @pfx_method
  def __setitem__(self, hashcode, index_entry):
    ''' Index `index_entry` against a `hashcode`.

        `index_entry` may be either in instance of `self.index_entry_class`
        or a value acceptable to `self.index_entry_class.from_value()`.
    '''
    index_entry_class = self.index_entry_class
    if not isinstance(index_entry, index_entry_class):
      offset, length = index_entry
      index_entry = index_entry_class(offset=offset, length=length)
    self.binary_index[hashcode] = bytes(index_entry)

@pfx_method
def VTDStore(name, path, *, hashclass, index=None, preferred_indexclass=None):
  ''' Factory to return a `MappingStore` using a `BackingFile`
      using a single `.vtd` file.
  '''
  if hashclass is None:
    hashclass = DEFAULT_HASHCLASS
  with Pfx(path):
    if not path.endswith('.vtd'):
      warning("does not end with .vtd")
    if not isfilepath(path):
      raise ValueError("missing path %r" % (path,))
    pathbase, _ = splitext(path)
    if index is None:
      index_basepath = f"{pathbase}-index-{hashclass.HASHNAME}"
      indexclass = choose_indexclass(
          index_basepath, preferred_indexclass=preferred_indexclass
      )
      binary_index = indexclass(index_basepath)
      index = BinaryHashCodeIndex(
          hashclass=hashclass,
          binary_index=binary_index,
          index_entry_class=BackingFileIndexEntry
      )
    return MappingStore(
        name,
        CompressibleBackingFile(path, hashclass=hashclass, index=index),
        hashclass=hashclass
    )
