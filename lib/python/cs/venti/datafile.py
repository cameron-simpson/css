#!/usr/bin/python -tt
#
# The basic data store for venti blocks.
#       - Cameron Simpson <cs@zip.com.au>
#

import sys
from collections import namedtuple
import os
import os.path
from threading import Lock, RLock
from zlib import compress, decompress
from cs.logutils import D
from cs.obj import O
from cs.queues import NestingOpenCloseMixin
from cs.serialise import get_bs, put_bs, get_bsfp
from cs.threads import locked_property

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

class DataFile(NestingOpenCloseMixin):
  ''' A cs.venti data file, storing data chunks in compressed form.
  '''

  def __init__(self, pathname):
    self._lock = RLock()
    NestingOpenCloseMixin.__init__(self)
    self.pathname = pathname
    self._fp = None

  def open(self, name=None):
    return NestingOpenCloseMixin.open(self, name=name)

  @locked_property
  def fp(self):
    ''' Property returning the file object of the current open file.
    '''
    return open(self.pathname, "a+b")

  def shutdown(self):
    ''' Close the current .fp if open.
    '''
    with self._lock:
      if self._fp:
        self._fp.close()
        self._fp = None

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
    if flags is None:
      raise RuntimeError("no data read from offset %d" % (offset,))
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
    self.ping()
    return offset

  def flush(self):
    if self._fp:
      self._fp.flush()
    self.ping()

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
    ''' Subclasses must implement the _openIndex method, which returns a
        mapping of hashcode to (datafile_index, datafile_offset).
    '''
    raise NotImplementedError("%s: no _openIndex() method" % (self.__class__.__name__,))

  def pathto(self, rpath):
    ''' Return a pathname within the DataDir given `rpath`, a path
        relative to the DataDir.
    '''
    return os.path.join(self.dir, rpath)

  @locked_property
  def index(self):
    ''' Property returning the index mapping.
    '''
    return self._openIndex()

  @property
  def indexpath(self):
    ''' The file pathname of the index file.
    '''
    return self.pathto(self.indexname)

  @property
  def hasindexfile(self):
    ''' Property testing whether the index file exists.
    '''
    return os.path.exists(self.indexpath)

  def scan(self, hashfunc, indices=None):
    ''' Scan the specified datafiles (or all if `indices` is missing or None).
        Record the data locations against the data hashcode in the index.
    '''
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

  # without this "in" tries to iterate over the mapping with int indices
  def __contains__(self, hash):
    return hash in self.index
    
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

  @locked_property
  def n(self):
    ''' The index of the currently open data file.
    '''
    return self.next_n()

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

  def open(self, n):
    ''' Obtain the Datafile indexed `n`.
    '''
    O = self._open
    with self._lock:
      D = O.get(n)
      if D is None:
        D = O[n] = DataFile(self.pathto(self.datafilename(n)))
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

  def __str__(self):
    return "GDBMDataDir(%s)" % (self.dir,)

  def _openIndex(self):
    import dbm.gnu
    gdbmpath = self.pathto(self.indexname)
    return dbm.gnu.open(gdbmpath, "c")

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
