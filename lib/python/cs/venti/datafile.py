#!/usr/bin/python -tt
#
# The basic data store for venti blocks.
#       - Cameron Simpson <cs@zip.com.au>
#

import sys
from collections import namedtuple
import os
import os.path
from threading import Lock
from zlib import compress, decompress
from cs.obj import O
from cs.serialise import get_bs, put_bs, get_bsfp

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

class DataFile(O):
  ''' A cs.venti data file, storing data chunks in compressed form.
  '''

  def __init__(self, pathname):
    self.pathname = pathname
    self._fp = None
    self._size = None
    self._lock = Lock()

  @property
  def fp(self):
    ''' Property returning the file object of the open file.
    '''
    fp = self._fp
    if not fp:
      with self._lock:
        fp = self._fp
        if fp is None:
          fp = self._fp = open(self.pathname, "a+b")
    return fp

  @property
  def size(self):
    ''' Property returning the size of this file.
    '''
    size = self._size
    if size is None:
      with self._lock:
        size = self._size = os.fstat(self.fp.fileno).st_size
    return size

  def scan(self, uncompress=False):
    ''' Scan the data file and yield (offset, flags, zdata) tuples.
        If `uncompress` is true, decompress the data and strip that flag value.
    '''
    fp = self.fp
    with self._lock:
      fp.seek(0)
      while True:
        offset = fp.tell()
        flags, data = self._readRawDataHere(fp)
        if flags is None:
          break
        if uncompress:
          if flags & F_COMPRESSED:
            data = decompress(data)
            flags &= ~F_COMPRESSED
        yield offset, flags, data

  def readdata(self, offset):
    ''' Read data bytes from the supplied offset.
    '''
    fp = self.fp
    with self._lock:
      fp.seek(offset)
      flags, data = self._readhere(fp)
    assert flags is not None, "no data read from offset %d" % (offset,)
    if flags & F_COMPRESSED:
      data = decompress(data)
    return data

  def _readhere(self, fp):
    ''' Retrieve the data bytes stored at the current file offset.
        The offset points at the flags ahead of the data bytes.
    '''
    flags = get_bsfp(fp)
    if flags is None:
      return None, None
    if (flags & ~F_COMPRESSED) != 0:
      raise ValueError("flags other than F_COMPRESSED: 0x%02x" % ((flags & ~F_COMPRESSED),))
    flags = DataFlags(flags)
    dsize = get_bsfp(fp)
    if dsize == 0:
      data = b''
    else:
      data = fp.read(dsize)
    assert len(data) == dsize
    return flags, data

  def savedata(self, data, noCompress=False):
    ''' Append a chunk of data to the file, return the store offset.
    '''
    flags = 0
    if not noCompress:
      zdata = compress(data)
      if len(zdata) < len(data):
        data = zdata
        flags |= F_COMPRESSED
    fp = self.fp
    with self._lock:
      fp.seek(0, 2)
      offset = fp.tell()
      fp.write(put_bs(flags))
      fp.write(put_bs(len(data)))
      fp.write(data)
      self._size = fp.tell()
    return offset

  def flush(self):
    if self._fp:
      self._fp.flush()

  def close(self):
    with self._lock:
      if self._fp:
        self._fp.close()
        self._fp = None

class DataDir(O):
  ''' A mapping of hash->Block that manages a directory of DataFiles.
      Subclasses must implement the _openIndex() method, which
      should return a mapping to store and retrieve index information.
  '''

  def __init__(self, dir):
    self.dir = dir
    self._index = None
    self._open = {}
    self._n = None
    self._lock = Lock()

  def _openIndex(self):
    raise NotImplementedError

  def pathto(self, rpath):
    ''' Return a pathname within the DataDir given `rpath`, a path
        relative to the DataDir.
    '''
    return os.path.join(self.dir, rpath)

  @property
  def index(self):
    ''' Property returning the index mapping.
    '''
    I = self._index
    if not I:
      with self._lock:
        I = self._index
        if not I:
          I = self._index = self._openIndex()
    return I

  @property
  def indexpath(self):
    return self.pathto(self.indexname)

  @property
  def hasindexfile(self):
    return os.path.exists(self.indexpath)

  def scan(self, hashfunc, indices=None):
    if indices is None:
      indices = self._datafileindices()
    with Pfx("scan %d", n):
      for n in indices:
        D = self.open(n)
        for offset, flags, data in D.scan():
          I[hashfunc(data)] = self.encodeIndexEntry(n, offset)

  @staticmethod
  def decodeIndexEntry(entry):
    ''' Parse an index entry into n (data file index) and offset.
    '''
    n, offset = get_bs(entry)
    file_offset, offset = get_bs(entry, offset)
    if offset != len(entry):
      raise ValueError("can't decode index entry: %s" % (hexlify(entry),))
    return n, file_offset

  @staticmethod
  def encodeIndexEntry(n, offset):
    ''' Prepare an index entry from data file index and offset.
    '''
    return put_bs(n) + put_bs(offset)

  def __getitem__(self, hash):
    ''' Return the uncompressed data associated with the supplied hash.
    '''
    n, offset = self.decodeIndexEntry(self.index[hash])
    return self.open(n).readdata(offset)

  def __setitem__(self, hash, data):
    ''' Store the supplied `data` indexed by `hash`.
    '''
    I = self.index
    if hash not in I:
      n = self.n
      D = self.open(n)
      offset = D.savedata(data)
      I[hash] = self.encodeIndexEntry(n, offset)

  @property
  def n(self):
    ''' The index of the currently open data file.
    '''
    with self._lock:
      if self._n is None:
        self._n = self.next_n()
    return self._n

  def next_n(self):
    ''' Return a free index not mapping to an existing data file.
    '''
    try:
      n = max(self._datafileindices()) + 1
    except ValueError:
      n = 0
    while os.path.exists(self.pathto(self.datafilename(n))):
      n += 1
    return n

  def set_n(self, n):
    ''' Set the current file index to `n`.
    '''
    self._n = n

  def contains(self, h):
    ''' Check if the specified hash is present in the store.
    '''
    with self._lock:
      return h in self._index

  def flush(self):
    for datafile in self._open:
      datafile.flush()
    self.index.flush()

  def open(n):
    ''' Obtain the Datafile indexed `n`.
    '''
    O = self._open
    with self._lock:
      D = O.get(n)
      if D is None:
        D = O[n] = Datafile(self.pathto(self.datafilename(n)))
    return D

  def datafilename(self, n):
    ''' Return the file basename for file index `n`.
    '''
    return str(n) + '.vtd'

  def _datafileindices(self):
    ''' Return the indices of datafiles present.
    '''
    indices = []
    for name in os.listdir(self.dir):
      if name.endswith('.vtd'):
        prefix = name[:-4]
        if prefix.isdigit():
          n = int(prefix)
          if str(n) == prefix:
            if os.path.isfile(self.pathto(name)):
              indices.append(n)
    return indices

class GDBMDataDir(DataDir):
  ''' A DataDir with a GDBM index.
  '''

  indexname = "index.gdbm"

  def _openIndex(self):
    import gdbm
    gdbmpath = self.pathto(self.indexname)
    return gdbm.open(gdbmpath, "cf")


class KyotoCabinetDataDir(DataDir):
  ''' An DataDir attached to a KyotoCabinet index.
  '''
  indexname = "index.kch"
  def _getIndex(self):
    from cs.kyoto import KyotoCabinet
    return KyotoIndex(os.path.join(self.dirpath, self.indexname))

if __name__ == '__main__':
  import cs.venti.datafile_tests
  cs.venti.datafile_tests.selftest(sys.argv)
