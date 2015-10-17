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
from cs.cache import LRU_Cache
from cs.excutils import LogExceptions
from cs.logutils import D, X, debug, warning, Pfx
from cs.obj import O
from cs.resources import MultiOpenMixin
from cs.serialise import get_bs, put_bs, read_bs, put_bsdata, read_bsdata
from cs.threads import locked, locked_property
from . import defaults
from .hash import DEFAULT_HASHCLASS

F_COMPRESSED = 0x01

# 100MiB rollover
DEFAULT_ROLLOVER = 100 * 1024 * 1024

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

def read_chunk(fp, offset, do_decompress=False):
  ''' Read a data chunk from the specified offset. Return (flags, chunk, post_offset).
      If do_decompress is true and flags&F_COMPRESSED, strip that
      flag and decompress the data before return.
      Raises EOFError on end of file.
  '''
  if fp.tell() != offset:
    fp.flush()
    fp.seek(offset)
  flags = read_bs(fp)
  if (flags & ~F_COMPRESSED) != 0:
    raise ValueError("flags other than F_COMPRESSED: 0x%02x" % ((flags & ~F_COMPRESSED),))
  flags = DataFlags(flags)
  data = read_bsdata(fp)
  offset = fp.tell()
  if do_decompress and (flags & F_COMPRESSED):
    data = decompress(data)
    flags &= ~F_COMPRESSED
  return flags, data, offset

def append_chunk(fp, data, no_compress=False):
  ''' Append a data chunk to the file, return the store offset.
      If not no_compress, try to compress the chunk.
  '''
  flags = 0
  if not no_compress:
    data2 = compress(data)
    if len(data2) < len(data):
      data = data2
      flags |= F_COMPRESSED
    fp.seek(0, SEEK_END)
    offset = fp.tell()
    fp.write(put_bs(flags))
    fp.write(put_bsdata(data))
  return offset

def decode_index_entry(entry):
  ''' Parse a binary index entry, return (n, offset).
  '''
  n, offset = get_bs(entry)
  file_offset, offset = get_bs(entry, offset)
  if offset != len(entry):
    raise ValueError("unparsed data from index entry; full entry = %s" % (hexlify(entry),))
  return n, file_offset

def encode_index_entry(n, offset):
  ''' Encode (n, offset) to binary form for use as an index entry.
  '''
  return put_bs(n) + put_bs(offset)

class DataFile(MultiOpenMixin):
  ''' A cs.venti data file, storing data chunks in compressed form.
      This is the usual persistence layer of a local venti Store.
  '''

  def __init__(self, pathname):
    MultiOpenMixin.__init__(self)
    self.pathname = pathname

  def __str__(self):
    return "DataFile(%s)" % (self.pathname,)

  def startup(self):
    self.fp = open(self.pathname, "a+b")

  def shutdown(self):
    self.fp.close()
    self.fp = None

  def scan(self, do_decompress=False):
    ''' Scan the data file and yield (offset, flags, zdata) tuples.
        If `do_decompress` is true, decompress the data and strip that flag value.
        This can be used in parallel with other activity.
    '''
    with self:
      fp = self.fp
      offset = 0
      while True:
        with self._lock:
          try:
            flags, data, offset = read_chunk(self.fp, offset, do_decompress=do_decompress)
          except EOFError:
            break
        yield offset, flags, data

  def get(self, offset):
    ''' Fetch data bytes from the supplied offset.
    '''
    fp = self.fp
    with self._lock:
      flags, data, offset = read_chunk(self.fp, offset, do_decompress=True)
    if flags:
      raise ValueError("unhandled flags: 0x%02x" % (flags,))
    return data

  def put(self, data, no_compress=False):
    ''' Append a chunk of data to the file, return the store offset.
    '''
    with self._lock:
      return append_chunk(self.fp, data, no_compress=no_compress)

class DataDir(MultiOpenMixin):
  ''' A class for managing a directory of DataFiles. An instance
      of this may be shared between different DataDirMaps.

      A DataDir directory contains DataFiles named n.vtd where n
      is an nonnegative integer.
  '''

  def __init__(self, dirpath, rollover=None):
    ''' Initialise this DataDir with the directory path `dirpath` and the optional DataFile rollover size `rollover`.
    '''
    if rollover is None:
      rollover = DEFAULT_ROLLOVER
    elif rollover < 1024:
      raise ValueError("rollover < 1024 (a more normal size would be in megabytes or gigabytes): %r" % (rollover,))
    MultiOpenMixin.__init__(self)
    self.dirpath = dirpath
    self._rollover = rollover
    # cache of open DataFiles
    self._datafile_cache = LRU_Cache(maxsize=4,
                                     on_remove=lambda k, datafile: datafile.close())
    # current append DataFile for new data
    current = self._datafile_indices()
    self.n = current[0] if current else 0

  def __str__(self):
    return "%s(rollover=%s)" % (self.__class__.__name__, self.rollover)

  def startup(self):
    pass

  def shutdown(self):
    ''' Called on final close of the DataDir.
        Close and release any open indices.
        Close any open datafiles.
    '''
    with self._lock:
      self._datafile_cache.flush()

  def pathto(self, rpath):
    ''' Return a pathname within the DataDir given `rpath`, a path
        relative to the DataDir.
    '''
    return os.path.join(self.dirpath, rpath)

  def datafile(self, n):
    ''' Obtain the Datafile with index `n`.
    '''
    datafiles = self._datafile_cache
    with self._lock:
      D = datafiles.get(n)
      if D is None:
        D = datafiles[n] = DataFile(self.pathto(self.datafilename(n)))
        D.open()
    return D

  def datafilename(self, n):
    ''' Return the file basename for file index `n`.
    '''
    return str(n) + '.vtd'

  def _datafile_indices(self):
    ''' Return the indices of datafiles present.
    '''
    indices = []
    for name in os.listdir(self.dirpath):
      if name.endswith('.vtd'):
        prefix = name[:-4]
        if prefix.isdigit():
          n = int(prefix)
          if str(n) == prefix:
            if os.path.isfile(self.pathto(name)):
              indices.append(n)
    return indices

  def next_n(self):
    ''' Compute an available index for the next data file.
    '''
    ns = self._datafile_indices()
    return max(ns)+1 if ns else 0

  @property
  def datafile_paths(self):
    ''' A list of the current datafile pathnames.
    '''
    dfpaths = [ self.pathto(self.datafilename(n)) for n in self._datafile_indices() ]
    X("datafile_paths=%r", dfpaths)
    return dfpaths

  def scan(self, indices=None):
    ''' Generator which scans the specified datafiles (or all if `indices` is missing or None).
        Yields (n, offset, data) for each data chunk.
    '''
    if indices is None:
      indices = self._datafile_indices()
    for dfn in indices:
      with Pfx("scan %d", dfn):
        D = self.datafile(dfn)
        for offset, flags, data in D.scan(do_decompress=True):
          yield dfn, offset, data

  def add(self, data):
    ''' Add the supplied data chunk to the current DataFile, return (n, offset).
        Roll the internal state over to a new file if the current
        datafile has reached the rollover threshold.
    '''
    # save the data in the current datafile, record the file number and offset
    n = self.n
    D = self.datafile(n)
    with D:
      offset = D.put(data)
    rollover = self._rollover
    if rollover is not None and offset >= rollover:
      with self._lock:
        # we're still the current file? then advance to a new file
        if self.n == n:
          self.n = self.next_n()
    return n, offset

  def get(self, n, offset):
    return self.datafile(n).get(offset)

class DataDirMapping(MultiOpenMixin):
  ''' Access to a DataDir as a mapping by using a dbm index per hash type.
  '''

  def __init__(self, dirpath, indexclass=None, rollover=None, hashclass=None):
    ''' Initialise this DataDirMapping.
        `dirpath`: if a str the path to the DataDir, otherwise an existing DataDir
        `indexclass`: class implementing the dbm, initialised with the path
                      to the dbm file; if this is a str it will be looked up
                      in INDEX_BY_NAME
        `rollover`: if `dirpath` is a str, this is passed in to the DataDir constructor
        `hashclass`: the default hashclass for operations needing one if the
                     default Store does not dictate a hashclass; defaults to
                     cs.venti.DEFAULT_HASHCLASS
        The indexclass is normally a mapping wrapper for some kind of DBM
        file stored in the DataDir. Importantly, the __getitem__
    '''
    if isinstance(dirpath, str):
      datadir = DataDir(dirpath, rollover=rollover)
    else:
      if rollover is not None:
        raise ValueError("rollover may not be supplied unless dirpath is a str: %r, rollover=%r" % (dirpath, rollover))
      datadir = dirpath
    if indexclass is None:
      indexclass = GDBMIndex
    elif isinstance(indexclass, str):
      indexclass = INDEX_BY_NAME[indexclass]
    if hashclass is None:
      hashclass = DEFAULT_HASHCLASS
    self._default_hashclass = hashclass
    # we will use the same lock as the underlying DataDir
    MultiOpenMixin.__init__(self, lock=datadir._lock)
    self.datadir = datadir
    self.indexclass = indexclass
    self._indices = {}  # map hash name to instance of indexclass

  def startup(self):
    self.datadir.open()
    pass

  def shutdown(self):
    with self._lock:
      for index in self._indices.values():
        index.close()
      self._indices = {}
    self.datadir.close()

  @property
  def default_hashclass(self):
    ''' The default hashclass.
        If there is a prevailing Store, use its hashclass otherwise
        self._default_hashclass.
    '''
    S = defaults.S
    if S is None:
      hashclass = self._default_hashclass
    else:
      hashclass = S.hashclass
    return hashclass

  @property
  def dirpath(self):
    return self.datadir.dirpath

  def reindex(self, hashclass=None):
    ''' Rescan all the data files, update the index.
    '''
    if hashclass is None:
      hashclass = self.defaults_hashclass
    I = self._index(hashclass)
    for n, offset, data in self.datadir.scan():
      hashcode = hashclass.from_data(data)
      if hashcode not in I:
        X("add %s => %d, %d", hashcode, n, offset)
        I[hashcode] = n, offset

  def _indexpath(self, hashname, suffix):
    ''' Return the pathname for a specific type of index.
    '''
    return self.datadir.pathto('index-' + hashname + '.' + suffix)

  def _index(self, hashclass):
    ''' Obtain the index to which to store/access this hashcode class.
    '''
    hashname = hashclass.HASHNAME
    try:
      # fast path: return already instantiated index
      return self._indices[hashname]
    except KeyError:
      # slow path: make index if we've not been outraced
      with self._lock:
        try:
          index = self._indices[hashname]
        except KeyError:
          indexclass = self.indexclass
          indexpath = self._indexpath(hashname, indexclass.suffix)
          index = self._indices[hashname] \
                = indexclass(indexpath, hashclass, lock=self._lock)
          index.open()
        return index

  # without this "in" tries to iterate over the mapping with int indices
  def __contains__(self, hashcode):
    return hashcode in self._index(hashcode.__class__)

  def __getitem__(self, hashcode):
    ''' Return the decompressed data associated with the supplied `hashcode`.
    '''
    n, offset = self._index(hashcode.__class__)[hashcode]
    return self.datadir.get(n, offset)

  def __setitem__(self, hashcode, data):
    ''' Store the supplied `data` indexed by `hashcode`.
    '''
    index = self._index(hashcode.__class__)
    if hashcode not in index:
      n, offset = self.datadir.add(data)
      index[hashcode] = n, offset

  @locked
  def flush(self):
    self.datadir.flush()
    with self._lock:
      indices = list(self._indices.values())
    for index in indices:
      index.flush()

  def first(self, hashclass=None):
    ''' Return the first hashcode in the database or None if empty.
        `hashclass`: specify the hashcode type, default from defaults.S
    '''
    if hashclass is None:
      hashclass = defaults.S
    return self._index(hashclass).first()

  def iter_keys(self, hashclass=None, hashcode=None, reverse=False, after=False):
    ''' Generator yielding the hashcodes from the database in order starting with optional `hashcode`.
        `hashclass`: specify the hashcode type, default from defaults.S
        `hashcode`: the first hashcode; if missing or None, iteration
                    starts with the first key in the index
        `reverse`: iterate backwards if true, otherwise forwards
        `after`: commence iteration after the first hashcode
    '''
    if hashclass is None:
      hashclass = defaults.S
    return self._index(hashclass).iter_keys(hashcode=hashcode,
                                            reverse=reverse,
                                            after=after)

  def merge_other(self, other, hashcodes=None):
    ''' Iterate over the hashcodes in `other` and fetch anything we don't have.
    '''
    if hashcodes is None:
      hashcodes = other.iter_keys()
    for hashcode in hashcodes:
      if hashcode not in self:
        X("pull %s", hashcode)
        self[hashcode] = other[hashcode]

class GDBMIndex(MultiOpenMixin):
  ''' GDBM index for a DataDir.
  '''

  indexname = 'gdbm'
  suffix = 'gdbm'

  def __init__(self, gdbmpath, hashclass, lock=None):
    import dbm.gnu
    MultiOpenMixin.__init__(self, lock=lock)
    self._hashclass = hashclass
    self._gdbm_path = gdbmpath
    self._gdbm = None

  def startup(self):
    import dbm.gnu
    self._gdbm = dbm.gnu.open(self._gdbm_path, 'cf')

  def shutdown(self):
    self._gdbm.close()
    self._gdbm = None

  def flush(self):
    self._gdbm.sync()

  __contains__ = lambda self, hashcode: hashcode in self._gdbm
  __getitem__  = lambda self, hashcode: decode_index_entry(self._gdbm[hashcode])
  get          = lambda self, hashcode, default=None: \
                    decode_index_entry(self._gdbm.get(hashcode, default))

  def __setitem__(self, hashcode, value):
    self._gdbm[hashcode] = encode_index_entry(*value)

def GDBMDataDirMapping(dirpath, rollover=None):
  return DataDirMapping(dirpath, indexclass=GDBMIndex, rollover=rollover)

class KyotoIndex(MultiOpenMixin):
  ''' Kyoto Cabinet index for a DataDir.
      Notably this uses a B+ tree for the index and thus one can
      traverse from one key forwards and backwards, which supports
      the coming Store synchronisation processes.
  '''

  indexname = 'kyoto'
  suffix = 'kct'

  def __init__(self, kyotopath, hashclass, lock=None):
    MultiOpenMixin.__init__(self, lock=lock)
    self._hashclass = hashclass
    self._kyoto_path = kyotopath
    self._kyoto = None

  def startup(self):
    from kyotocabinet import DB
    self._kyoto = DB()
    self._kyoto.open(self._kyoto_path, DB.OWRITER | DB.OCREATE)

  def shutdown(self):
    self._kyoto.close()
    self._kyoto = None

  def flush(self):
    self._kyoto.synchronize(hard=False)

  def __len__(self):
    return self._kyoto.count()

  def __contains__(self, hashcode):
    return self._kyoto.check(hashcode) >= 0

  def get(self, hashcode):
    record = self._kyoto.get(hashcode)
    if record is None:
      return None
    return decode_index_entry(record)

  def __getitem__(self, hashcode):
    entry = self.get(hashcode)
    if entry is None:
      raise IndexError(str(hashcode))
    return entry

  def __setitem__(self, hashcode, value):
    self._kyoto[hashcode] = encode_index_entry(*value)

  def first(self):
    ''' Return the first hashcode in the database or None if empty.
    '''
    cursor = self._kyoto.cursor()
    if not cursor.jump():
      return None
    hashcode = self._hashclass.from_hashbytes(cursor.get_key())
    cursor.disable()
    return hashcode

    cursor.disable()

  def iter_keys(self, hashcode=None, reverse=False, after=False):
    ''' Generator yielding the keys from the index in order starting with optional `hashcode`.
        `hashcode`: the first hashcode; if missing or None, iteration
                    starts with the first key in the index
        `reverse`: iterate backwards if true, otherwise forwards
        `after`: commence iteration after the first hashcode
    '''
    cursor = self._kyoto.cursor()
    if cursor.jump(hashcode):
      if not after:
        yield self._hashclass.from_hashbytes(cursor.get_key())
      while True:
        if reverse:
          if not cursor.step_back():
            break
        else:
          if not cursor.step():
            break
        yield self._hashclass.from_hashbytes(cursor.get_key())
    cursor.disable()

def KyotoDataDirMapping(dirpath, rollover=None):
  return DataDirMapping(dirpath, indexclass=KyotoIndex, rollover=rollover)

DATADIRMAPPING_BY_NAME = {}

def register_mapping(indexname, klass):
  global DATADIRMAPPING_BY_NAME
  if indexname in DATADIRMAPPING_BY_NAME:
    raise ValueError(
            'cannot register DataDirMapping class %s: indexname %r already registered to %s'
            % (klass, indexname, DATADIRMAPPING_BY_NAME[indexname]))
  DATADIRMAPPING_BY_NAME[indexname] = klass

register_mapping('gdbm', GDBMDataDirMapping)
register_mapping('kyoto', KyotoDataDirMapping)

if __name__ == '__main__':
  import cs.venti.datafile_tests
  cs.venti.datafile_tests.selftest(sys.argv)
