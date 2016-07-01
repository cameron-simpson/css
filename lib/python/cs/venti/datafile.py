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
from threading import Lock, RLock, Thread
from zlib import compress, decompress
from cs.cache import LRU_Cache
from cs.excutils import LogExceptions
from cs.logutils import D, X, XP, debug, warning, error, exception, Pfx
from cs.obj import O
from cs.queues import IterableQueue
from cs.resources import MultiOpenMixin
from cs.serialise import get_bs, put_bs, read_bs, put_bsdata, read_bsdata
from cs.threads import locked, locked_property
from . import defaults
from .hash import HASHCLASS_BY_NAME, DEFAULT_HASHCLASS, HashCodeUtilsMixin

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

def read_chunk(fp, offset, do_decompress=False):
  ''' Read a data chunk from a file at its current offset. Return (flags, chunk, post_offset).
      If do_decompress is true and flags&F_COMPRESSED, strip that
      flag and decompress the data before return.
      Raises EOFError on premature end of file.
  '''
  fp = self.fp
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

def write_chunk(fp, data, no_compress=False):
  ''' Write a data chunk to a file at the current position, return the starting offset.
      If not no_compress, try to compress the chunk.
  '''
  flags = 0
  if not no_compress:
    data2 = compress(data)
    if len(data2) < len(data):
      data = data2
      flags |= F_COMPRESSED
    offset = fp.tell()
    fp.write(put_bs(flags))
    fp.write(put_bsdata(data))
  return offset

class DataFile(MultiOpenMixin):
  ''' A cs.venti data file, storing data chunks in compressed form.
      This is the usual persistence layer of a local venti Store.
  '''

  def __init__(self, pathname):
    MultiOpenMixin.__init__(self)
    self.pathname = pathname
    self.appending = False

  def __str__(self):
    return "DataFile(%s)" % (self.pathname,)

  def startup(self):
    self.fp = open(self.pathname, "a+b")

  def shutdown(self):
    if self.appending:
      self.fp.flush()
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
          if self.appending:
            fp.flush()
            self.appending = False
          fp.seek(offset, SEEK_SET)
          try:
            flags, data, offset = read_chunk(fp, do_decompress=do_decompress)
          except EOFError:
            break
        yield offset, flags, data

  def get(self, offset):
    ''' Fetch data bytes from the supplied offset.
    '''
    fp = self.fp
    with self._lock:
      if self.appending:
        fp.flush()
        self.appending = False
      fp.seek(offset, SEEK_SET)
      flags, data, offset = read_chunk(fp, do_decompress=True)
    if flags:
      raise ValueError("unhandled flags: 0x%02x" % (flags,))
    return data

  def put(self, data, no_compress=False):
    ''' Append a chunk of data to the file, return the store offset.
    '''
    fp = self.fp
    with self._lock:
      if not self.appending:
        self.appending = True
        fp.seek(0, SEEK_END)
      return write_chunk(fp, data, no_compress=no_compress)

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
    return "%s(rollover=%s)" % (self.__class__.__name__, self._rollover)

  def startup(self):
    pass

  def shutdown(self):
    ''' Called on final close of the DataDir.
        Close and release any open indices.
        Close any open datafiles.
    '''
    self.flush()

  def flush(self):
    ''' Flush all the open datafiles.
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
    return [ self.pathto(self.datafilename(n)) for n in self._datafile_indices() ]

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

class DataDirMapping(MultiOpenMixin, HashCodeUtilsMixin):
  ''' Access to a DataDir as a mapping by using a hashtype specific dbm index.
  '''

  def __init__(self, dirpath, hashclass, indexclass=None, rollover=None):
    ''' Initialise this DataDirMapping.
        `dirpath`: if a str the path to the DataDir, otherwise an existing DataDir
        `hashclass`: the hashclass for operations
        `indexclass`: class implementing the dbm, initialised with the path
                      to the dbm file; if this is a str it will be looked up
                      in INDEXCLASS_BY_NAME
        `rollover`: if `dirpath` is a str, this is passed in to the DataDir constructor
        The indexclass is normally a mapping wrapper for some kind of DBM
        file stored in the DataDir.
    '''
    global INDEXCLASS_BY_NAME
    if isinstance(dirpath, str):
      datadir = DataDir(dirpath, rollover=rollover)
    else:
      if rollover is not None:
        raise ValueError("rollover may not be supplied unless dirpath is a str: %r, rollover=%r" % (dirpath, rollover))
      datadir = dirpath
    if indexclass is None:
      indexclass = GDBMIndex
    elif isinstance(indexclass, str):
      indexclass = INDEXCLASS_BY_NAME[indexclass]
    self.hashclass = hashclass
    # we will use the same lock as the underlying DataDir
    MultiOpenMixin.__init__(self, lock=datadir._lock)
    self.datadir = datadir
    self.indexclass = indexclass
    # map individual hashcodes to locations before being persistently stored
    self._unindexed = {}
    self._indexQ = IterableQueue()
    T = self._index_Thread = Thread(name="%s-index-thread" % (self,),
                                    target=self._update_index)
    T.start()

  def spec(self):
    ''' Return a datadir_spec for this DataDirMapping.
    '''
    return ':'.join( (self.indexclass.indexname,
                      self.hashclass.HASHNAME,
                      self.dirpath) )

  __str__ = spec

  def __len__(self):
    return len(self._default_index)

  def startup(self):
    self.datadir.open()
    hashclass = self.hashclass
    hashname = hashclass.HASHNAME
    indexpath = self.datadir.localpathto('index-' + hashname + '.' + suffix)
    index = indexclass(indexpath, hashclass, lock=self._lock)
    index.open()
    self.index = index

  def shutdown(self):
    with self._lock:
      # shut down new pending index updates and wait for them to be applied
      self._indexQ.close()
      self._index_Thread.join()
      if self._unindexed:
        error("UNINDEXED BLOCKS: %r", self._unindexed)
    self.index.close()
    self.datadir.close()

  def hashcodes_from(self, start_hashcode=None, reverse=False):
    return self.index.hashcodes_from(hashclass=self.hashclass,
                                     start_hashcode=start_hashcode,
                                     reverse=reverse)

  @property
  def dirpath(self):
    return self.datadir.dirpath

  # TODO: turn into "ingest" function wrapper?
  def update(self, from_start=False):
    ''' Rescan all the data files, update the index with new content.
    '''
    hashclass = self.hashclass
    I = self.index
    for n, filename, flags, data, offset, offset2 \
        in self.datadir.updates(from_start=from_start, save_update=True):
      hashcode = hashclass.from_data(data)
      if hashcode not in I:
        I[hashcode] = n, offset

  # without this "in" tries to iterate over the mapping with int indices
  def __contains__(self, hashcode):
    return hashcode in self._unindexed or hashcode in self.index

  def __getitem__(self, hashcode):
    ''' Return the decompressed data associated with the supplied `hashcode`.
    '''
    unindexed = self._unindexed
    try:
      n, offset = unindexed[hashcode]
    except KeyError:
      index = self.index
      try:
        n, offset = index[hashcode]
      except KeyError:
        error("%s[%s]: hash not in index", self, hashcode)
        raise
    try:
      return self.datadir.get(n, offset)
    except Exception as e:
      exception("%s[%s]:%d:%d not available: %s", self, hashcode, n, offset, e)
      raise KeyError(str(hashcode))

  def __setitem__(self, hashcode, data):
    ''' Store the supplied `data` indexed by `hashcode`.
        If the hashcode is already known, do not both storing the `data`.
    '''
    unindexed = self._unindexed
    if hashcode in unindexed:
      # already received
      pass
    else:
      index = self.index
      with self._lock:
        # might have arrived outside the lock
        if hashcode in unindexed:
          pass
        elif hashcode in index:
          pass
        else:
          n, offset = self.datadir.add(data)
          # cache the location
          unindexed[hashcode] = n, offset
          # queue the location for persistent storage
          self._indexQ.put( (index, hashcode, n, offset) )

  def _update_index(self):
    ''' Thread body to collect hashcode index data and store it.
    '''
    with Pfx("_update_index"):
      unindexed = self._unindexed
      for index, hashcode, n, offset in self._indexQ:
        index[hashcode] = n, offset
        del unindexed[hashcode]

  def add(self, data):
    ''' Add a data chunk. Return the hashcode.
    '''
    hashclass = self.hashclass
    h = hashclass.from_data(data)
    self[h] = data
    return h

  @locked
  def flush(self):
    self.datadir.flush()
    self.index.flush()

  def first(self, hashclass=None):
    ''' Return the first hashcode in the database or None if empty.
        `hashclass`: specify the hashcode type, default from defaults.S
    '''
    hashclass = self.hashclass
    index = self.index
    try:
      first_method = index.first
    except AttributeError:
      raise NotImplementedError("._index(%s) has no .first" % (hashclass,))
    return first_method()

  def hashcodes_from(self, start_hashcode=None, reverse=False):
    ''' Generator yielding the hashcodes from the database in order starting with optional `start_hashcode`.
        `start_hashcode`: the first hashcode; if missing or None, iteration
                          starts with the first key in the index
        `reverse`: iterate backwards if true, otherwise forwards
    '''
    hashclass = self.hashclass
    return self.index.hashcodes_from(start_hashcode=start_hashcode,
                                     reverse=reverse)

class GDBMIndex(HashCodeUtilsMixin, MultiOpenMixin):
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
    ##X("GDBMIndex ADD %s (%r)", hashcode, value)
    self._gdbm[hashcode] = encode_index_entry(*value)

def GDBMDataDirMapping(dirpath, rollover=None):
  return DataDirMapping(dirpath, indexclass=GDBMIndex, rollover=rollover)

class KyotoIndex(HashCodeUtilsMixin, MultiOpenMixin):
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
    try:
      self._kyoto.synchronize(hard=False)
    except TypeError:
      self._kyoto.synchronize()

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

  def hashcodes_from(self, hashclass=None, start_hashcode=None, reverse=None):
    ''' Generator yielding the keys from the index in order starting with optional `start_hashcode`.
        `start_hashcode`: the first hashcode; if missing or None, iteration
                    starts with the first key in the index
        `reverse`: iterate backwards if true, otherwise forwards
    '''
    if hashclass is not None and hashclass is not self._hashclass:
      raise ValueError("tried to get hashcodes of class %s from %s<_hashclass=%s>"
                       % (hashclass, self, self._hashclass))
    cursor = self._kyoto.cursor()
    if reverse:
      if cursor.jump_back(start_hashcode):
        yield self._hashclass.from_hashbytes(cursor.get_key())
        while cursor.step_back():
          yield self._hashclass.from_hashbytes(cursor.get_key())
    else:
      if cursor.jump(start_hashcode):
        yield self._hashclass.from_hashbytes(cursor.get_key())
        while cursor.step():
          yield self._hashclass.from_hashbytes(cursor.get_key())
    cursor.disable()

def KyotoDataDirMapping(dirpath, rollover=None):
  return DataDirMapping(dirpath, indexclass=KyotoIndex, rollover=rollover)

INDEXCLASS_BY_NAME = {}

def register_index(indexname, indexclass):
  global INDEXCLASS_BY_NAME
  if indexname in INDEXCLASS_BY_NAME:
    raise ValueError(
            'cannot register index class %s: indexname %r already registered to %s'
            % (indexclass, indexname, INDEXCLASS_BY_NAME[indexname]))
  INDEXCLASS_BY_NAME[indexname] = indexclass

register_index('gdbm', GDBMIndex)
register_index('kyoto', KyotoIndex)

DEFAULT_INDEXCLASS = GDBMIndex

def DataDirMapping_from_spec(datadir_spec, **kw):
  ''' Accept `datadir_spec` of the form [indextype:[hashname:]]/dirpath and return a DataDirMapping.
  '''
  global INDEXCLASS_BY_NAME, DEFAULT_HASHCLASS, HASHCLASS_BY_NAME
  with Pfx(datadir_spec):
    indexclass = None
    hashname = None
    # leading indextype
    if not datadir_spec.startswith('/'):
      indexname, datadir_spec = datadir_spec.split(':', 1)
      try:
        indexclass = INDEXCLASS_BY_NAME[indexname]
      except KeyError:
        raise ValueError("invalid indextype: %r (I know %r)"
                         % (indexname,), sorted(INDEXCLASS_BY_NAME.keys()))
    if not datadir_spec.startswith('/'):
      hashname, datadir_spec = datadir_spec.split(':', 1)
      try:
        hashclass = HASHCLASS_BY_NAME[hashname]
      except KeyError:
        raise ValueError("invalid hashname: %r (I know %r)"
                         % (hashname, sorted(HASHCLASS_BY_NAME.keys())))
    else:
      hashclass = DEFAULT_HASHCLASS
      hashname = hashclass.HASHNAME
    dirpath = datadir_spec
    if not os.path.isdir(dirpath):
      raise ValueError("not a directory: %r" % (dirpath,))
    # no indextype yet? look for an index file
    if indexclass is None:
      found = False
      for indexname, indexclass in INDEXCLASS_BY_NAME.items():
        suffix = indexclass.suffix
        indexfilename = DataDirMapping._indexfilename(hashname, suffix)
        entries = list(os.listdir(dirpath))
        if indexfilename in entries:
          found = True
          break
        if not found:
          indexclass = DEFAULT_INDEXCLASS
          warning("no index file found, using %s (EMPTY)", indexclass)
    return DataDirMapping(dirpath, indexclass=indexclass, **kw)

if __name__ == '__main__':
  import cs.venti.datafile_tests
  cs.venti.datafile_tests.selftest(sys.argv)
