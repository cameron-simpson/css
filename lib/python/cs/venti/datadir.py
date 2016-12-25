#!/usr/bin/python -tt
#
# The sharable directory storing DataFiles used by DataDirStores.
# - Cameron Simpson <cs@zip.com.au>
#

from collections.abc import Mapping
import csv
import os
from os.path import join as joinpath, samefile, exists as existspath
import sys
from threading import Lock, RLock, Thread
from time import sleep
from types import SimpleNamespace
from uuid import uuid4
from cs.cache import LRU_Cache
from cs.csvutils import csv_reader, csv_writerow
from cs.fileutils import makelockfile, shortpath, longpath
from cs.logutils import D, X, XP, debug, warning, error, exception, Pfx
from cs.queues import IterableQueue
from cs.resources import MultiOpenMixin
from cs.serialise import get_bs, put_bs, read_bs, put_bsdata, read_bsdata
from cs.threads import locked, locked_property
from .datafile import DataFile
from .hash import HASHCLASS_BY_NAME, DEFAULT_HASHCLASS, HashCodeUtilsMixin

# 1GiB rollover
DEFAULT_ROLLOVER = 1024 * 1024 * 1024

DATAFILE_EXT = 'vtd'
DATAFILE_DOT_EXT = '.' + DATAFILE_EXT

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

class _DataDirFile(SimpleNamespace):
  ''' General state information about a DataFile in use by a DataDir.
  '''

  @property
  def pathname(self):
    return self.datadir.datapathto(self.filename)

  @property
  def last_stat_size(self):
    return getattr(self, '_last_stat_size', None)

  @last_stat_size.setter
  def last_stat_size(self, new_size):
    self._last_stat_size = new_size

  def stat_size(self):
    ''' Stat the datafile, return its size.
    '''
    return os.stat(self.pathname).st_size

  def scan_from(self, offset):
    ''' Scan this datafile from the supplied `offset` yielding (data, offset, post_offset).
        This is used by the monitor thread to add new third party data to the index.
    '''
    with open(self.pathname, "rb") as fp:
      fp.seek(offset)
      while True:
        flags, data, post_offset = read_chunk(fp, do_decompress=True)
        yield data, offset, post_offset
        offset = post_offset

class DataDir(HashCodeUtilsMixin, MultiOpenMixin, Mapping):
  ''' Maintenance of a collection of DataFiles in a directory.
      NB: _not_ thread safe; callers must arrange that.
      The directory may be maintained by multiple instances of this
      class as they will not try to add data to the same DataFile.
      This is intended to address shared Stores such as a Store on
      a NAS or a Store replicated by an external file-level service
      such as Dropbox or plain old rsync.

      A DataDir may be used as the Mapping for a MappingStore.
  '''

  STATE_FILENAME_FORMAT = 'index-%s-state.csv'
  INDEX_FILENAME_FORMAT = 'index-%s.%s'

  def __init__(self, statedirpath, datadirpath, hashclass, indexclass, rollover=None):
    ''' Initialise the DataDir with `statedirpath` and `datadirpath`.
        `statedirpath`: a directory containing state information
          about the DataFiles; this is the index-state.csv file and
          the associated index dbm-ish files.
        `datadirpath`: the directory containing the DataFiles.
          If this is shared by other clients then it should be
          different from the `statedirpath`.
          If None, default to "statedirpath/data", which might be
          a symlink to a shared area such as a NAS.
        `hashclass`: the hash class used to index chunk contents.
        `indexclass`: the IndexClass providing the index to chunks
          in the DataFiles.
        `rollover`: data file roll over size; if a data file grows
            beyond this a new datafile is commenced for new blocks.
            Default: DEFAULT_ROLLOVER
    '''
    if datadirpath is None:
      datadirpath = joinpath(statedirpath, 'data')
    if hashclass is None:
      hashclass = DEFAULT_HASHCLASS
    if indexclass is None:
      indexclass = DEFAULT_INDEXCLASS
    if rollover is None:
      rollover = DEFAULT_ROLLOVER
    elif rollover < 1024:
      raise ValueError("rollover < 1024 (a more normal size would be in megabytes or gigabytes): %r" % (rollover,))
    self.statedirpath = statedirpath
    self.datadirpath = datadirpath
    self.hashclass = hashclass
    self.indexclass = indexclass
    self.rollover = rollover
    self._filemap = {}
    self._extra_state = {}
    self._n = None
    self._load_state()
    MultiOpenMixin.__init__(self)

  def __repr__(self):
    return ( '%s(statedirpath=%r,datadirpath=%r,hashclass=%s,indexclass=%s,rollover=%d)'
             % (self.__class__.__name__,
                self.statedirpath,
                self.datadirpath,
                self.hashclass.HASHNAME,
                self.indexclass,
                self.rollover)
           )

  def spec(self):
    ''' Return a datadir_spec for this DataDirMapping.
    '''
    return ':'.join( (self.indexclass.INDEXNAME,
                      self.hashclass.HASHNAME,
                      str(self.statedirpath),
                      str(self.datadirpath)) )

  __str__ = spec

  def startup(self):
    # cache of open DataFiles
    self._cache = LRU_Cache(maxsize=4,
                            on_remove=lambda k, datafile: datafile.close())
    # obtain lock
    self.lockpath = makelockfile(self.statefilepath)
    # open dbm index
    self.index = self.indexclass(self.indexpath, self.hashclass)
    self.index.open()
    # set up indexing thread
    # map individual hashcodes to locations before being persistently stored
    # This lets us add data, stash the location in _unindexed and
    # drop the location onto the _indexQ for persistent storage in
    # the index asynchronously.
    self._unindexed = {}
    self._indexQ = IterableQueue()
    T = self._index_Thread = Thread(name="%s-index-thread" % (self,),
                                    target=self._index_updater)
    T.start()
    self._monitor_halt = False
    T = self._monitor_Thread = Thread(name="%s-datafile-monitor" % (self,),
                                      target=self._monitor_datafiles)
    T.start()

  def shutdown(self):
    # shut down the monitor Thread
    self._monitor_halt = True
    self._monitor_Thread.join()
    # drain index update queue
    self._indexQ.close()
    self._index_Thread.join()
    if self._unindexed:
      error("UNINDEXED BLOCKS: %r", self._unindexed)
    # update state to substrate
    self.flush()
    del self._cache
    del self._filemap
    self.index.close()
    # release lockfile
    os.remove(self.lockpath)
    del self.lockpath

  def _monitor_datafiles(self):
    ''' Thread body to poll all the datafiles regularly for new data arrival.
    '''
    filemap = self._filemap
    indexQ = self._indexQ
    while not self._monitor_halt:
      # scan for new datafiles
      added = False
      for filename in os.listdir(self.datadirpath):
        if ( not filename.startswith('.')
         and filename.endswith(DATAFILE_DOT_EXT)
         and filename not in filemap):
          self._add_datafile(filename, no_save=True)
          added = True
      if added:
        self._save_state()
      # now scan datafiles for new data
      for filenum in filemap.keys():
        if self._monitor_halt:
          break
        if not isinstance(filenum, int):
          continue
        # don't monitor the current datafile: our own actions will update it
        n = self._n
        if n is not None and filenum == n:
          continue
        try:
          F = filemap[filenum]
        except KeyError:
          warning("missing entry %d in filemap", filenum)
          continue
        try:
          new_size = F.stat_size()
        except OSError as e:
          warning("%s: could not get file size: %s", F.pathname, e)
          continue
        old_size = F.last_stat_size
        if ( new_size > F.size
         and ( old_size is None or old_size < new_size )
           ):
          # scan data file for more blocks
          try:
            scan_data = F.scan_from(F.size)
          except OSError as e:
            warning("%s: could not scan: %s", F.pathname, e)
            continue
          advanced = False
          try:
            for data, offset, post_offset in scan_data:
              hashcode = self.hashclass.from_data(data)
              indexQ.put( (hashcode, filenum, offset) )
              F.size = post_offset
              advanced = True
              if self._monitor_halt:
                break
          except EOFError as e:
            warning("%s: EOF interrupts scan: %s", F.pathname, e)
          F.last_stat_size = new_size
          # update state after completion of a scan
          if advanced:
            self._save_state()
      sleep(1)

  def localpathto(self, rpath):
    return joinpath(self.statedirpath, rpath)

  def datapathto(self, rpath):
    return joinpath(self.datadirpath, rpath)

  @property
  def statefilepath(self):
    return self.localpathto(self.STATE_FILENAME_FORMAT
                            % (self.hashclass.HASHNAME,))

  @property
  def indexpath(self):
    return self.localpathto(self.INDEX_FILENAME_FORMAT
                            % (self.hashclass.HASHNAME,
                               self.indexclass.SUFFIX))

  def _queue_index(self, hashcode, n, offset):
    with self._lock:
      self._unindexed[hashcode] = n, offset
    self._indexQ.put( (hashcode, n, offset) )

  def _index_updater(self):
    ''' Thread body to collect hashcode index data from .indexQ and store it.
    '''
    with Pfx("_index_updater"):
      index = self.index
      unindexed = self._unindexed
      for hashcode, n, offset in self._indexQ:
        index[hashcode] = n, offset
        with self._lock:
          try:
            del unindexed[hashcode]
          except KeyError:
            # this can happens when the same key is indexed twice
            # entirely plausible if a new datafile is added to the datadir
            pass

  def _load_state(self):
    ''' Read STATE_FILENAME.
    '''
    statefilepath = self.statefilepath
    ##if existspath(statefilepath):
    ##  os.system('sed "s/^/IN  /" %r' % (statefilepath,))
    filemap = self._filemap
    with Pfx('_load_state(%r)', statefilepath):
      if existspath(statefilepath):
        with open(statefilepath, 'r') as fp:
          extras = self._extra_state
          for lineno, row in enumerate(csv_reader(fp), 1):
            with Pfx("%d", lineno):
              col1 = row[0]
              with Pfx(col1):
                try:
                  filenum = int(col1)
                except ValueError:
                  _, col2 = row
                  with Pfx("%s=%r", col1, col2):
                    if col1 == 'datadir':
                      datadirpath = longpath(col2)
                      if self.datadirpath is None:
                        self.datadirpath = datadirpath
                      elif not samefile(datadirpath, self.datadirpath):
                        warning("not the same directory as supplied self.datadirpath=%r, will be updated",
                                self.datadirpath)
                    elif col1 == 'current':
                      self._n = int(col2)
                    else:
                      warning("unrecognised parameter")
                      extras[col1] = col2
                else:
                  _, filename, size = row
                  size = int(size)
                  self._add_datafile(filename, filenum=filenum, size=size, no_save=True)
    # presume data in state dir if not specified
    if self.datadirpath is None:
      self.datadirpath = self.statedirpath

  @locked
  def _save_state(self):
    ''' Rewrite STATE_FILENAME.
    '''
    statefilepath = self.statefilepath
    with Pfx("_save_state(%r)", statefilepath):
      with open(statefilepath, 'w') as fp:
        csvw = csv.writer(fp)
        csvw.writerow( ('datadir', shortpath(self.datadirpath)) )
        if self._n is not None:
          csvw.writerow( ('current', self._n) )
        extras = self._extra_state
        for k in sorted(extras.keys()):
          csvw.writerow( (k, extras[k]) )
        filemap = self._filemap
        for n in sorted(n for n in filemap.keys() if isinstance(n, int)):
          F = filemap[n]
          csvw.writerow( (F.filenum, F.filename, F.size) )
    ##os.system('sed "s/^/OUT /" %r' % (statefilepath,))

  def _add_datafile(self, filename, filenum=None, size=0, no_save=False):
    ''' Add the datafile with basename `filename` to the filemap, return the _DataDirFile.
        `filenum`: optional index number.
        `size`: optional size, default 0.
    '''
    filemap = self._filemap
    if filename in filemap:
      raise KeyError('already in filemap: %r' % (filename,))
    if filenum is None:
      filenum = max([0] + list(k for k in filemap.keys() if isinstance(k, int))) + 1
    elif filenum in filemap:
      raise KeyError('already in filemap: %r' % (filennum,))
    F = _DataDirFile(datadir=self, filenum=filenum, filename=filename, size=size)
    filemap[filenum] = F
    filemap[filename] = F
    if not no_save:
      self._save_state()
    return F

  def _new_datafile(self):
    ''' Create a new datafile and return its record.
    '''
    filename = str(uuid4()) + DATAFILE_DOT_EXT
    pathname = self.datapathto(filename)
    if os.path.exists(pathname):
      raise RuntimeError("path already exists: %r", pathname)
    # create the file
    with open(pathname, "ab"):
      pass
    F = self._add_datafile(filename)
    return F

  @locked
  def _current_output_datafile(self):
    ''' Return the number and DataFile of the current datafile,
        opening one if necessary.
    '''
    n = self._n
    if n is None:
      F = self._new_datafile()
      n = self._n = F.filenum
    D = self._open_datafile(n)
    return n, D

  def _open_datafile(self, n):
    ''' Return the DataFile with index `n`.
    '''
    cache = self._cache
    D = cache.get(n)
    if D is None:
      # not in the cache, open it
      F = self._filemap[n]
      readwrite = (n == self._n)
      D = cache[n] = DataFile(self.datapathto(F.filename), readwrite=readwrite)
      D.open()
    return D

  @locked
  def flush(self):
    self._cache.flush()
    self.index.flush()
    self._save_state()

  def add(self, data):
    ''' Add the supplied data chunk to the current DataFile, return the hashcode.
        Roll the internal state over to a new file if the current
        datafile has reached the rollover threshold.
    '''
    # save the data in the current datafile, record the file number and offset
    n, D = self._current_output_datafile()
    with D:
      offset, offset2 = D.add(data)
    hashcode = self.hashclass.from_data(data)
    self._queue_index(hashcode, n, offset)
    rollover = self.rollover
    if rollover is not None and offset2 >= rollover:
      with self._lock:
        # we're still the current file? then advance to a new file
        if self.n == n:
          self.n = self.next_n()
    F = self._filemap[n]
    if offset2 <= F.size:
      raise RuntimeError("%s: offset2(%d) after adding chunk <= F.size(%d)"
                         % (F.filename, offset2, F.size))
    F.size = offset2
    return hashcode

  def __setitem__(self, hashcode, data):
    h = self.add(data)
    if hashcode != h:
      raise ValueError('hashcode %s does not match data, data added under %s instead'
                       % (hashcode, h))

  def fetch(self, n, offset):
    ''' Return the data chunk stored in DataFile `n` at `offset`.
    '''
    return self._open_datafile(n).fetch(offset)

  def __len__(self):
    return len(self.index)

  def hashcodes_from(self, start_hashcode=None, reverse=False):
    ''' Generator yielding the hashcodes from the database in order starting with optional `start_hashcode`.
        `start_hashcode`: the first hashcode; if missing or None, iteration
                          starts with the first key in the index
        `reverse`: iterate backwards if true, otherwise forwards
    '''
    unindexed = set(self._unindexed)
    indexed = self.index.hashcodes_from(start_hashcode=start_hashcode,
                                        reverse=reverse)
    unseen_indexed = ( h for h in indexed if h not in unindexed )
    return imerge(sorted(unindexed, reverse=reverse), unseen_indexed)

  def __iter__(self):
    return self.hashcodes_from()

  # without this "in" tries to iterate over the mapping with int indices
  def __contains__(self, hashcode):
    return hashcode in self._unindexed or hashcode in self.index

  def __getitem__(self, hashcode):
    ''' Return the decompressed data associated with the supplied `hashcode`.
    '''
    if not isinstance(hashcode, self.hashclass):
      raise ValueError("hashcode %r is not a %s", hashcode, self.hashclass)
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
      return self.fetch(n, offset)
    except Exception as e:
      exception("%s[%s]:%d:%d not available: %s", self, hashcode, n, offset, e)
      raise KeyError(str(hashcode))

class GDBMIndex(HashCodeUtilsMixin, MultiOpenMixin):
  ''' GDBM index for a DataDir.
  '''

  INDEXNAME = 'gdbm'
  SUFFIX = 'gdbm'

  def __init__(self, gdbmpath, hashclass, lock=None):
    import dbm.gnu
    MultiOpenMixin.__init__(self, lock=lock)
    self.hashclass = hashclass
    self._gdbm_path = gdbmpath
    self._gdbm = None

  def startup(self):
    import dbm.gnu
    self._gdbm = dbm.gnu.open(self._gdbm_path, 'cf')
    self._gdbm_lock = Lock()
    self._written = False

  def shutdown(self):
    self.flush()
    with self._gdbm_lock:
      self._gdbm.close()
      self._gdbm = None
      del self._gdbm_lock

  def flush(self):
    if self._written:
      with self._gdbm_lock:
        if self._written:
          self._gdbm.sync()
          self._written = False

  def __iter__(self):
    mkhash = self.hashclass.from_hashbytes
    self.flush()
    with self._gdbm_lock:
      hashcode = self._gdbm.firstkey()
    while hashcode is not None:
      yield mkhash(hashcode)
      self.flush()
      with self._gdbm_lock:
        hashcode = self._gdbm.nextkey(hashcode)

  def __contains__(self, hashcode):
    self.flush()
    with self._gdbm_lock:
      return hashcode in self._gdbm

  def __getitem__(self, hashcode):
    self.flush()
    with self._gdbm_lock:
      entry = self._gdbm[hashcode]
    return decode_index_entry(entry)

  def get(self, hashcode, default=None):
    self.flush()
    with self._gdbm_lock:
      entry = self._gdbm.get(hashcode, None)
    if entry is None:
      return default
    return decode_index_entry(entry)

  def __setitem__(self, hashcode, value):
    entry = encode_index_entry(*value)
    with self._gdbm_lock:
      self._gdbm[hashcode] = entry
      self._written = True

class KyotoIndex(HashCodeUtilsMixin, MultiOpenMixin):
  ''' Kyoto Cabinet index for a DataDir.
      Notably this uses a B+ tree for the index and thus one can
      traverse from one key forwards and backwards, which supports
      the coming Store synchronisation processes.
  '''

  INDEXNAME = 'kyoto'
  SUFFIX = 'kct'

  def __init__(self, kyotopath, hashclass, lock=None):
    MultiOpenMixin.__init__(self, lock=lock)
    self.hashclass = hashclass
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

  def hashcodes_from(self, start_hashcode=None, reverse=False):
    ''' Generator yielding the keys from the index in order starting with optional `start_hashcode`.
        `start_hashcode`: the first hashcode; if missing or None, iteration
                    starts with the first key in the index
        `reverse`: iterate backward if true, otherwise forward
    '''
    hashclass = self.hashclass
    cursor = self._kyoto.cursor()
    if reverse:
      if cursor.jump_back(start_hashcode):
        yield hashclass.from_hashbytes(cursor.get_key())
        while cursor.step_back():
          yield hashclass.from_hashbytes(cursor.get_key())
    else:
      if cursor.jump(start_hashcode):
        yield hashclass.from_hashbytes(cursor.get_key())
        while cursor.step():
          yield hashclass.from_hashbytes(cursor.get_key())
    cursor.disable()

INDEXCLASS_BY_NAME = {}

def register_index(indexname, indexclass):
  global INDEXCLASS_BY_NAME
  if indexname in INDEXCLASS_BY_NAME:
    raise ValueError(
            'cannot register index class %s: indexname %r already registered to %s'
            % (indexclass, indexname, INDEXCLASS_BY_NAME[indexname]))
  INDEXCLASS_BY_NAME[indexname] = indexclass

register_index('gdbm', GDBMIndex)
try:
    import kyotocabinet
except ImportError:
    pass
else:
    register_index('kyoto', KyotoIndex)

DEFAULT_INDEXCLASS = GDBMIndex

def DataDir_from_spec(spec, indexclass=None, hashclass=None, rollover=None):
  ''' Accept `spec` of the form:
        [indextype:[hashname:]]/indexdir[:/dirpath][:rollover=n]
      and return a DataDir.
  '''
  global INDEXCLASS_BY_NAME, DEFAULT_INDEXCLASS
  global HASHCLASS_BY_NAME, DEFAULT_HASHCLASS
  indexdirpath = None
  datadirpath = None
  with Pfx(spec):
    specpath = spec.split(os.pathsep)
    for partnum, specpart in enumerate(specpath, 1):
      with Pfx("%d:%r", partnum, specpart):
        if indexclass is None:
          if specpart in INDEXCLASS_BY_NAME:
            indexclass = INDEXCLASS_BY_NAME[specpart]
            continue
          indexclass = DEFAULT_INDEXCLASS
        if hashclass is None:
          if specpart in HASHCLASS_BY_NAME:
            hashclass = HASHCLASS_BY_NAME[specpart]
            continue
        if indexdirpath is None:
          indexdirpath = specpart
          continue
        if datadirpath is None:
          datadirpath = specpart
          continue
        raise ValueError("unexpected part")
  if indexclass is None:
    indexclass = DEFAULT_INDEXCLASS
  if hashclass is None:
    hashclass = DEFAULT_HASHCLASS
  return DataDir(indexdirpath, datadirpath, hashclass, indexclass, rollover=rollover)

if __name__ == '__main__':
  import cs.venti.datadir_tests
  cs.venti.datadir_tests.selftest(sys.argv)
