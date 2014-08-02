#!/usr/bin/python -tt
#
# The basic data store for venti blocks.
#       - Cameron Simpson <cs@zip.com.au>
#

import sys
from collections import namedtuple
import os
from os import SEEK_SET, SEEK_END
import os.path
from threading import Lock, RLock
from zlib import compress, decompress
from cs.logutils import D
from cs.obj import O
from cs.queues import NestingOpenCloseMixin
from cs.serialise import get_bs, put_bs, get_bsfp
from cs.threads import locked, locked_property

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
    self.fp = None

  def on_open(self, count):
    if count == 1:
      self.fp = open(self.pathname, "a+b")

  def shutdown(self):
    self.fp.close()
    self.fp = None

  def scan(self, uncompress=False):
    ''' Scan the data file and yield (offset, flags, zdata) tuples.
        If `uncompress` is true, decompress the data and strip that flag value.
        This can be used in parallel with other activity.
    '''
    with self:
      fp = self.fp
      offset = 0
      while True:
        with self._lock:
          fp.seek(offset)
          flags, data = self._readRawDataHere(fp)
          offset = fp.tell()
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
      fp.seek(offset, SEEK_SET)
      flags, data = self._readhere(fp)
    if flags is None:
      raise RuntimeError("no data read from offset %d" % (offset,))
    if flags & F_COMPRESSED:
      data = decompress(data)
    return data

  def _readhere(self, fp):
    ''' Retrieve the data bytes stored at the current file offset.
        The offset points at the flags ahead of the data bytes.
        Presumes the ._lock is already taken.
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
      fp.seek(0, SEEK_END)
      offset = fp.tell()
      fp.write(put_bs(flags))
      fp.write(put_bs(len(data)))
      fp.write(data)
    self.ping()
    return offset

  @locked
  def flush(self):
    if self.fp:
      self.fp.flush()

class DataDir(NestingOpenCloseMixin):
  ''' A mapping of hash->Block that manages a directory of DataFiles.
      Subclasses must implement the _openIndex() method, which
      should return a mapping to store and retrieve index information.
  '''

  def __init__(self, dir):
    self.dir = dir
    self.index = None
    self._open = {}
    self._n = None
    self._lock = Lock()

  def on_open(self, count):
    if count == 1:
      self.index = self._openIndex()

  def shutdown(self):
    self.index.sync()
    self.index = None

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
        D = self.datafile(n)
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
    return self.datafile(n).readdata(offset)

  def __setitem__(self, hash, data):
    ''' Store the supplied `data` indexed by `hash`.
    '''
    I = self.index
    if hash not in I:
      n = self.n
      D = self.datafile(n)
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

  def datafile(self, n):
    ''' Obtain the Datafile with index `n`.
    '''
    datafiles = self._open
    with self._lock:
      D = datafiles.get(n)
      if D is None:
        D = datafiles[n] = DataFile(self.pathto(self.datafilename(n)))
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
