#!/usr/bin/python -tt
#
# Data stores based on local files.
# - Cameron Simpson <cs@cskk.id.au>
#

''' DataDir: the sharable directory storing DataFiles used by DataDirStores.
'''

from binascii import hexlify
from collections import namedtuple
from collections.abc import Mapping
import errno
import os
from os import SEEK_SET, SEEK_CUR, SEEK_END
from os.path import (
    basename,
    isdir as isdirpath,
    isfile as isfilepath,
    join as joinpath,
    relpath)
import sqlite3
import stat
import sys
import time
from types import SimpleNamespace
from uuid import uuid4
from cs.app.flag import DummyFlags, FlaggedMixin
from cs.cache import LRU_Cache
from cs.excutils import logexc
from cs.fileutils import (
    DEFAULT_READSIZE,
    ReadMixin,
    datafrom_fd,
    makelockfile,
    read_from,
    shortpath,
    TimeoutError)
from cs.logutils import debug, info, warning, error, exception
from cs.pfx import Pfx, PfxThread as Thread
from cs.py.func import prop as property
from cs.queues import IterableQueue
from cs.resources import MultiOpenMixin, RunStateMixin
from cs.seq import imerge
from cs.serialise import get_bs, put_bs
from cs.threads import locked
from cs.units import transcribe_bytes_geek
from . import MAX_FILE_SIZE, Lock, RLock
from .archive import Archive
from .block import Block
from .blockify import (
    DEFAULT_SCAN_SIZE,
    blocked_chunks_of,
    spliced_blocks,
    top_block_for
)
from .datafile import DataFileReader, DataFileWriter, DATAFILE_DOT_EXT
from .dir import Dir, FileDirent
from .hash import HashCode, HashCodeUtilsMixin, MissingHashcodeError
from .index import choose as choose_indexclass
from .parsers import scanner_from_filename

DEFAULT_DATADIR_STATE_NAME = 'default'

# 1GiB rollover
DEFAULT_ROLLOVER = MAX_FILE_SIZE

# flush the index after this many updates in the index updater worker thread
INDEX_FLUSH_RATE = 16384

def init_datadir(basedir):
  ''' Init a directory and its "data" subdirectory.
  '''
  if not isdirpath(basedir):
    info("mkdir %r", basedir)
    with Pfx("mkdir(%r)", basedir):
      os.mkdir(basedir)
  # create the data subdir if missing
  datadirpath = joinpath(basedir, 'data')
  if not isdirpath(datadirpath):
    info("mkdir %r", datadirpath)
    with Pfx("mkdir(%r)", datadirpath):
      os.mkdir(datadirpath)

class DataFileState(SimpleNamespace):
  ''' General state information about a data file in use by a files based data dir.

      Attributes:
      * `datadir`: the _FilesDir tracking this state.
      * `filenum`: the numeric index of this file.
      * `filename`: path relative to the _FilesDir's data directory.
      * `indexed_to`: the maximum amount of data scanned and indexed
        so far.
  '''

  def __init__(
      self,
      datadir, filenum, filename,
      indexed_to=0, scanned_to=None,
  ) -> None:
    if scanned_to is None:
      scanned_to = indexed_to
    self.datadir = datadir
    self.filenum = filenum
    self.filename = filename
    self.indexed_to = indexed_to
    self.scanned_to = scanned_to

  def __str__(self):
    return "%s(%d:%r)" % (type(self).__name__, self.filenum, self.filename)

  @property
  def pathname(self):
    ''' Return the full pathname of this data file.
    '''
    return self.datadir.datapathto(self.filename)

  def stat_size(self, follow_symlinks=False):
    ''' Stat the datafile, return its size.
    '''
    path = self.pathname
    if follow_symlinks:
      S = os.stat(path)
    else:
      S = os.lstat(path)
    if not stat.S_ISREG(S.st_mode):
      return None
    return S.st_size

  def scanfrom(self, offset=0, **kw):
    ''' Scan this datafile from the supplied `offset` (default 0)
        yielding (offset, flags, data, post_offset).

        We use the DataDir's .scanfrom method because it knows the
        format of the file.
    '''
    yield from self.datadir.scanfrom(self.pathname, offset=offset, **kw)

class _FilesDir(HashCodeUtilsMixin, MultiOpenMixin, RunStateMixin, FlaggedMixin, Mapping):
  ''' Base class indexing locally stored data in files.
      This class is hashclass specific;
      the utilising Store keeps one of these for each supported hashclass.

      There are two main subclasses of this at present:

      DataDir, where the data are kept in a subdirectory of UUID-named
      files, supporting easy merging and updating.

      PlatonicDataDir, where the data are present in a normal file tree,
      such as a preexisting media server directory or the like.
  '''

  STATE_FILENAME_FORMAT = 'index-{hashname}-state.sqlite'
  INDEX_FILENAME_BASE_FORMAT = 'index-{hashname}'

  def __init__(
      self,
      statedirpath,
      hashclass,
      *,
      indexclass=None,
      flags=None, flags_prefix=None,
  ):
    ''' Initialise the DataDir with `statedirpath` and `datadirpath`.

        Parameters:
        * `statedirpath`: a directory containing state information about the
          DataFiles; this is the index-state.csv file and the associated
          index dbm-ish files.
        * `hashclass`: the hashclass used for indexing
        * `indexclass`: the IndexClass providing the index to chunks in the
          DataFiles. If not specified, a supported index class with an
          existing index file will be chosen, otherwise the most favoured
          indexclass available will be chosen.
        * `flags`: optional Flags object for control; if specified then
          `flags_prefix` is also required
        * `flags_prefix`: prefix for control flag names

        Note that __init__ only saves the settings such as the `indexclass`
        and ensures that requisite directories exist.
        The monitor thread and runtime state are setup by the `startup` method
        and closed down by the `shutdown` method.
    '''
    assert isinstance(statedirpath, str)
    assert issubclass(hashclass, HashCode), "hashclass=%r" % (hashclass,)
    RunStateMixin.__init__(self)
    MultiOpenMixin.__init__(self, lock=RLock())
    if flags is None:
      if flags_prefix is None:
        flags = DummyFlags()
        flags_prefix = 'DUMMY'
    else:
      if flags_prefix is None:
        raise ValueError("flags provided but no flags_prefix")
    FlaggedMixin.__init__(self, flags=flags, prefix=flags_prefix)
    self.hashclass = hashclass
    self.hashname = hashclass.HASHNAME
    self.statedirpath = statedirpath
    self.statefilepath = joinpath(
        statedirpath,
        self.STATE_FILENAME_FORMAT.format(hashname=self.hashname))
    if indexclass is None:
      indexclass = choose_indexclass(
          self.INDEX_FILENAME_BASE_FORMAT.format(hashname=self.hashname))
    self.indexclass = indexclass
    self._filemap = None
    self._unindexed = None
    self._cache = None
    self._indexQ = None
    self._index_Thread = None
    self._monitor_Thread = None

  def __str__(self):
    return '%s(%s)' % (self.__class__.__name__, shortpath(self.statedirpath))

  def __repr__(self):
    return ( '%s(statedirpath=%r,indexclass=%s)'
             % (self.__class__.__name__,
                self.statedirpath,
                self.indexclass)
           )

  def startup(self):
    ''' Start up the _FilesDir: take locks, start worker threads etc.
    '''
    self._unindexed = {}
    self._filemap = SqliteFilemap(self, self.statefilepath)
    hashname = self.hashname
    self.index = self.indexclass(
        self.pathto(self.INDEX_FILENAME_BASE_FORMAT.format(hashname=hashname)),
        self.hashclass,
        self.index_entry_class.from_bytes,
        lock=self._lock)
    self.index.open()
    self.runstate.start()
    # cache of open DataFiles
    self._cache = LRU_Cache(
        maxsize=4,
        on_remove=lambda k, datafile: datafile.close()
    )
    # Set up indexing thread.
    # Map individual hashcodes to locations before being persistently stored.
    # This lets us add data, stash the location in _unindexed and
    # drop the location onto the _indexQ for persistent storage in
    # the index asynchronously.
    self._indexQ = IterableQueue(64)
    T = self._index_Thread = Thread(
        name="%s-index-thread" % (self,),
        target=self._index_updater)
    T.start()
    T = self._monitor_Thread = Thread(
        name="%s-datafile-monitor" % (self,),
        target=self._monitor_datafiles)
    T.start()

  def shutdown(self):
    ''' Shut down the _FilesDir: cancel the runstate, close the
        queues, join the worker threads.
    '''
    self.runstate.cancel()
    self.flush()
    # shut down the monitor Thread
    mon_thread = self._monitor_Thread
    if mon_thread is not None:
      mon_thread.join()
      self._monitor_Thread = None
    # drain index update queue
    Q = self._indexQ
    if Q is not None:
      Q.close()
      self._indexQ = None
    index_thread = self._index_Thread
    if index_thread is not None:
      index_thread.join()
      self._index_Thread = None
    if self._unindexed:
      error("UNINDEXED BLOCKS: %r", self._unindexed)
    # update state to substrate
    self._cache = None
    self._filemap.close()
    self._filemap = None
    self.index.close()
    self.runstate.stop()

  def pathto(self, rpath):
    ''' Return the path to `rpath`, which is relative to the statedirpath.
    '''
    return joinpath(self.statedirpath, rpath)

  def datapathto(self, rpath):
    ''' Return the path to `rpath`, which is relative to the datadirpath.
    '''
    return self.pathto(joinpath('data', rpath))

  def get_Archive(self, name=None, **kw):
    ''' Return the Archive named `name`.

        If `name` is omitted or `None`
        the Archive path is the `statedirpath`
        plus the extension `'.vt'`.
        Otherwise it is the `statedirpath` plus a dash plus the `name`
        plus the extension `'.vt'`.
        The `name` may not be empty or contain a dot.
    '''
    with Pfx("%s.get_Archive", self):
      if name is None or not name:
        archivepath = self.statedirpath + '.vt'
      else:
        if '.' in name:
          raise ValueError("invalid name: %r" % (name,))
        archivepath = self.statedirpath + '-' + name + '.vt'
      return Archive(archivepath, **kw)

  def _queue_index(self, hashcode, entry, post_offset):
    if not isinstance(entry, self.index_entry_class):
      raise RuntimeError("expected %s but got %s %r" % (self.index_entry_class, type(entry), entry))
    with self._lock:
      self._unindexed[hashcode] = entry
    self._indexQ.put( (hashcode, entry, post_offset) )

  def _queue_index_flush(self):
    self._indexQ.put(None)

  @logexc
  def _index_updater(self):
    ''' Thread body to collect hashcode index data from .indexQ and store it.
    '''
    with Pfx("%s._index_updater", self):
      index = self.index
      entry_class = self.index_entry_class
      unindexed = self._unindexed
      filemap = self._filemap
      old_DFstate = None
      indexQ = self._indexQ
      for item in indexQ:
        # dummy item to sync state
        if item is None:
          if old_DFstate is not None:
            filemap.set_indexed_to(old_DFstate.filenum, old_DFstate.indexed_to)
            old_DFstate = None
          continue
        hashcode, entry, post_offset = item
        if not isinstance(entry, entry_class):
          raise RuntimeError("expected %s but got %s %r" % (entry_class, type(entry), entry))
        with self._lock:
          index[hashcode] = entry
          try:
            del unindexed[hashcode]
          except KeyError:
            # this can happen when the same key is indexed twice
            # entirely plausible if a new datafile is added to the datadir
            pass
        DFstate = filemap[entry.n]
        if DFstate is not old_DFstate:
          if old_DFstate is not None:
            filemap.set_indexed_to(old_DFstate.filenum, old_DFstate.indexed_to)
          old_DFstate = DFstate
        DFstate.indexed_to = post_offset
      if old_DFstate is not None:
        filemap.set_indexed_to(old_DFstate.filenum, old_DFstate.indexed_to)

  @locked
  def flush(self):
    ''' Flush all the components.
    '''
    self._queue_index_flush()
    self._cache.flush()
    self.index.flush()

  def __setitem__(self, hashcode, data):
    h = self.add(data, type(hashcode))
    if hashcode != h:
      raise ValueError(
          'supplied hashcode %s does not match data, data added under %s instead'
          % (hashcode, h))

  def __len__(self):
    return len(self.index)

  def hashcodes_from(self, start_hashcode=None, reverse=False):
    ''' Generator yielding the hashcodes from the database in order
        starting with optional `start_hashcode`.

        Parameters:
        * `start_hashcode`: the first hashcode; if missing or None,
          iteration starts with the first key in the index
        * `reverse`: iterate backwards if true, otherwise forwards.
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
    unindexed = self._unindexed
    try:
      entry = unindexed[hashcode]
    except KeyError:
      index = self.index
      try:
        with self._lock:
          entry = index[hashcode]
      except KeyError:
        raise KeyError("%s[%s]: hash not in index" % (self, hashcode))
    try:
      return self.fetch(entry)
    except Exception as e:
      exception("%s[%s]:%s not available: %s", self, hashcode, entry, e)
      raise KeyError(str(hashcode))

  # TODO: memoised BlockMap on demand function?
  def get_blockmap(self, B):
    ''' Return a persistent BlockMap for the supplied Block.
    '''
    raise RuntimeError(
        "%s.get_blockmap: return singleton persistent BlockMap here for Block %s"
        % (self, B))

class SqliteFilemap:
  ''' The file mapping of `n` to `DataFileState`.

      The implementation is an in-memory dict with an SQLite database
      as backing store. SQLite databases are portable across
      architectures and may have multiple users, so that DataDirs
      may be shared.
  '''

  def __init__(self, datadir, path):
    self._lock = Lock()
    self.datadir = datadir
    self.path = path
    with Pfx("connect(%r,...)", path):
      self.conn = sqlite3.connect(path, check_same_thread=False)
    self.settings = {}
    self.n_to_DFstate = {}
    self.path_to_DFstate = {}
    c = self.conn.cursor()
    c.execute(r'''
        CREATE TABLE IF NOT EXISTS settings (
            `setting` TEXT,
            `value`   TEXT,
            CONSTRAINT `setting` UNIQUE (`setting`)
        );''')
    c.execute(r'''
        CREATE TABLE IF NOT EXISTS filemap (
            `id`   INTEGER PRIMARY KEY,
            `path` TEXT,
            `indexed_to` INTEGER,
            CONSTRAINT `path` UNIQUE (`path`)
        );''')
    c.connection.commit()
    c.close()
    self._load_map()

  def close(self):
    ''' Close the database.
    '''
    self.conn.close()
    del self.conn

  def _execute(self, sql, *a):
    return self.conn.execute(sql, *a)

  def _modify(self, sql, *a, return_cursor=False):
    sql = sql.strip()
    conn = self.conn
    try:
      c = self._execute(sql, *a)
    except (
        sqlite3.OperationalError,
        sqlite3.IntegrityError,
    ) as e:
      error("%s: %s [SQL=%r %r]", type(e).__name__, e, sql, a)
      conn.rollback()
    else:
      conn.commit()
      if return_cursor:
        return c
      c.close()
    return None

  def filenums(self):
    ''' Return the active DFstate filenums.
    '''
    with self._lock:
      return list(self.n_to_DFstate.keys())

  def items(self):
    ''' Return the active (n, DFstate) pairs.
    '''
    with self._lock:
      return list(self.n_to_DFstate.items())

  def _map(self, path, n, indexed_to=0):
    ''' Add a DataFileState for `path` and `n` to the mapping.
    '''
    datadir = self.datadir
    if n in self.n_to_DFstate:
      warning("replacing n_to_DFstate[%s]", n)
    if path in self.path_to_DFstate:
      warning("replacing path_toDFstate[%r]", path)
    DFstate = DataFileState(
        datadir, n, path, indexed_to=indexed_to)
    self.n_to_DFstate[n] = DFstate
    self.path_to_DFstate[path] = DFstate

  def _load_map(self):
    with self._lock:
      c = self._execute(r'''
          SELECT id, path, indexed_to FROM filemap
      ''')
      for n, path, indexed_to in c.fetchall():
        self._map(path, n, indexed_to)
      c.close()

  def add_path(self, new_path, indexed_to=0):
    ''' Insert a new path into the map.
        Return its DataFileState.
    '''
    info("new path %r", shortpath(new_path))
    with Pfx("add_path(%r,indexed_to=%d)", new_path, indexed_to):
      with self._lock:
        c = self._modify(r'''
            INSERT INTO filemap(`path`, `indexed_to`) VALUES (?, ?)
        ''', (new_path, 0), return_cursor=True)
        if c:
          n = c.lastrowid
          self._map(new_path, n, indexed_to=indexed_to)
          c.close()
        else:
          # TODO: look up the path=>(n,indexed_to) as fallback
          error("FAILED")
          return None
      return self.n_to_DFstate[n]

  def del_path(self, old_path):
    ''' Forget the information for `old_path`.

        In order to prevent reuse of an id we just set the record's
        `path` and `indexed_to` to NULL.
    '''
    DFstate = self.path_to_DFstate[old_path]
    with self._lock:
      self._modify(r'''
          UPDATE filemap SET path=NULL, indexed_to=NULL where id = ?
      ''', (DFstate.filenum,))
    del self.n_to_DFstate[DFstate.filenum]
    del self.path_to_DFstate[old_path]

  def __getitem__(self, k):
    if isinstance(k, int):
      return self.n_to_DFstate[k]
    return self.path_to_DFstate[k]

  def get(self, k):
    ''' Return the entry with key `k` or `None`.
    '''
    try:
      return self[k]
    except KeyError:
      return None

  def __contains__(self, k):
    try:
      self[k]
    except KeyError:
      return False
    return True

  def set_indexed_to(self, n, new_indexed_to):
    ''' Update the `indexed_to` value for path `n`.
    '''
    DFstate = self.n_to_DFstate[n]
    with self._lock:
      self._modify(r'''
          UPDATE filemap SET indexed_to = ? WHERE id = ?
      ''', (new_indexed_to, n))
    DFstate.indexed_to = new_indexed_to

class DataDirIndexEntry(namedtuple('DataDirIndexEntry', 'n offset')):
  ''' A block record for a DataDir.
  '''

  @classmethod
  def from_bytes(cls, data: bytes):
    ''' Parse a binary index entry, return (n, offset).
    '''
    n, offset = get_bs(data)
    file_offset, offset = get_bs(data, offset)
    if offset != len(data):
      raise ValueError(
          "unparsed data from index entry; full entry = %s; n=%d, file_offset=%d, unparsed=%r"
          % (hexlify(data), n, file_offset, data[offset:]))
    return cls(n, file_offset)

  def encode(self) -> bytes:
    ''' Encode (n, offset) to binary form for use as an index entry.
    '''
    return put_bs(self.n) + put_bs(self.offset)

class DataDir(_FilesDir):
  ''' Maintenance of a collection of DataFiles in a directory.

      A DataDir may be used as the Mapping for a MappingStore.

      NB: _not_ thread safe; callers must arrange that.

      The directory may be maintained by multiple instances of this
      class as they will not try to add data to the same DataFile.
      This is intended to address shared Stores such as a Store on
      a NAS presented via NFS, or a Store replicated by an external
      file-level service such as Dropbox or plain old rsync.
  '''

  index_entry_class = DataDirIndexEntry

  def __init__(
      self,
      statedirpath,
      hashclass,
      *,
      rollover=None,
      **kw
  ):
    ''' Initialise the DataDir with `statedirpath`.

        Parameters:
        * `statedirpath`: a directory containing state information about the
          DataFiles; this is the index-state.csv file and the associated
          index dbm-ish files.
        * `indexclass`: the IndexClass providing the index to chunks in the
          DataFiles. If not specified, a supported index class with an
          existing index file will be chosen, otherwise the most favoured
          indexclass available will be chosen.
        * `rollover`: data file roll over size; if a data file grows beyond
          this a new datafile is commenced for new blocks.  Default:
          `DEFAULT_ROLLOVER`.
    '''
    super().__init__(statedirpath, hashclass, **kw)
    if rollover is None:
      rollover = DEFAULT_ROLLOVER
    elif rollover < 1024:
      raise ValueError(
          "rollover < 1024"
          " (a more normal size would be in megabytes or gigabytes): %r"
          % (rollover,))
    self.rollover = rollover
    self._write_n = None
    self._write_DF = None
    self._write_lockpath = None

  def shutdown(self):
    self._close_write_datafile()
    super().shutdown()

  def _open_datafile(self, n):
    ''' Return the DataFileReader with index `n`.
    '''
    cache = self._cache
    DF = cache.get(n)
    if DF is None:
      with self._lock:
        # first, look again now that we have the _lock
        DF = cache.get(n)
        if DF is None:
          # still not in the cache, open the DataFile and put into the cache
          DFstate = self._filemap[n]
          DF = cache[n] = DataFileReader(self.datapathto(DFstate.filename))
          DF.open()
    return DF

  def fetch(self, entry):
    ''' Return the data chunk stored in DataFile `n` at `offset`.
    '''
    DF = self._open_datafile(entry.n)
    return DF.fetch(entry.offset)

  def _get_write_datafile(self):
    ''' Obtain a fresh writable datafile, closing the current one if open.
        Returns (n, WDF).
    '''
    self._close_write_datafile()
    rollover = self.rollover
    WDF = None
    for n, DFstate in self._filemap.items():
      if DFstate.indexed_to < rollover:
        try:
          lockpath = makelockfile(DFstate.pathname, timeout=0)
        except TimeoutError:
          # lock taken, proceed to another file
          continue
        WDF = DataFileWriter(DFstate.pathname)
        break
    if WDF is None:
      # no suitable existing file, make a new one
      while True:
        filename = str(uuid4()) + DATAFILE_DOT_EXT
        pathname = self.datapathto(filename)
        if os.path.exists(pathname):
          error("new datafile path already exists, retrying: %r", pathname)
          continue
        break
      lockpath = makelockfile(pathname, timeout=0)
      WDF = DataFileWriter(pathname, do_create=True)
      DFstate = self._filemap.add_path(filename)
      n = DFstate.filenum
    WDF.open()
    self._write_n = n
    self._write_DF = WDF
    self._write_lockpath = lockpath
    return n, WDF

  def _close_write_datafile(self):
    ''' Close the current writable datafile if open.
    '''
    WDF = self._write_DF
    if WDF is not None:
      WDF.close()
      try:
        os.remove(self._write_lockpath)
      except OSError as e:
        if e.errno == errno.ENOENT:
          warning("remove(%r): %s", self._write_lockpath, e)
        else:
          error("remove(%r): %s", self._write_lockpath, e)
      self._write_n = None
      self._write_DF = None
      self._write_lockpath = None

  def add(self, data, hashclass):
    ''' Add the supplied data chunk to the current save DataFile,
        return the hashcode.
        Roll the internal state over to a new file if the current
        datafile has reached the rollover threshold.
    '''
    # obtain the write DataFile
    with self._lock:
      WDF = self._write_DF
      if WDF is None:
        n, WDF = self._get_write_datafile()
      else:
        n = self._write_n
    # save the data chunk
    with WDF:
      offset, post_offset = WDF.add(data)
    # queue the index update
    hashcode = self.hashclass.from_chunk(data)
    self._queue_index(hashcode, DataDirIndexEntry(n, offset), post_offset)
    # see if the write DataFile is now full; if so, close it
    rollover = self.rollover
    with self._lock:
      if rollover is not None and post_offset >= rollover:
        self._close_write_datafile()
    return hashcode

  @staticmethod
  def scanfrom(filepath, offset=0, **kw):
    ''' Scan the specified `filepath` from `offset`, yielding data chunks.
    '''
    with DataFileReader(filepath) as DF:
      yield from DF.scanfrom(offset, **kw)

  def _monitor_datafiles(self):
    ''' Thread body to poll all the datafiles regularly for new data arrival.

        This is what supports shared use of the data area. Other clients
        may write to their onw datafiles and this thread sees new files
        and new data in existing files and scans them, adding the index
        information to the local state.
    '''
    filemap = self._filemap
    indexQ = self._indexQ
    datadirpath = self.pathto('data')
    while not self.cancelled:
      if self.flag_scan_disable:
        time.sleep(1)
        continue
      # scan for new datafiles
      with Pfx("listdir(%r)", datadirpath):
        try:
          listing = list(os.listdir(datadirpath))
        except OSError as e:
          if e.errno == errno.ENOENT:
            error("listing failed: %s", e)
            time.sleep(2)
            continue
          raise
        for filename in listing:
          if (
              not filename.startswith('.')
              and filename.endswith(DATAFILE_DOT_EXT)
              and filename not in filemap
          ):
            info("MONITOR: add new filename %r", filename)
            filemap.add_path(filename)
      # now scan known datafiles for new data
      for filenum in self._filemap.filenums():
        if self.cancelled or self.flag_scan_disable:
          break
        # don't monitor the current datafile: our own actions will update it
        n = self._write_n
        if n is not None and filenum == n:
          continue
        try:
          DFstate = filemap[filenum]
        except KeyError:
          warning("missing entry %d in filemap", filenum)
          continue
        with Pfx(DFstate.filename):
          try:
            new_size = DFstate.stat_size()
          except OSError as e:
            warning("stat: %s", e)
            continue
          else:
            if new_size is None:
              info("skip nonfile")
              continue
          if new_size > DFstate.scanned_to:
            offset = DFstate.scanned_to
            for record, post_offset in DFstate.scanfrom(offset=offset):
              hashcode = self.hashclass.from_chunk(record.data)
              indexQ.put( (hashcode, DataDirIndexEntry(filenum, offset), post_offset) )
              DFstate.scanned_to = post_offset
              if self.cancelled:
                break
              offset = post_offset
            self.flush()
        self.flush()
      time.sleep(1)

class PlatonicDirIndexEntry(namedtuple('PlatonicDirIndexEntry', 'n offset length')):
  ''' A block record for a PlatonicDir.
  '''

  @classmethod
  def from_bytes(cls, data: bytes):
    ''' Parse a binary index entry, return (n, offset).
    '''
    n, offset = get_bs(data)
    file_offset, offset = get_bs(data, offset)
    length, offset = get_bs(data, offset)
    if offset != len(data):
      raise ValueError("unparsed data from index entry; full entry = %s" % (hexlify(data),))
    return cls(n, file_offset, length)

  def encode(self) -> bytes:
    ''' Encode (n, offset) to binary form for use as an index entry.
    '''
    return put_bs(self.n) + put_bs(self.offset) + put_bs(self.length)

class PlatonicFile(MultiOpenMixin, ReadMixin):
  ''' A PlatonicFile is a normal file whose content is used as the
      reference for block data.
  '''

  def __init__(self, path):
    MultiOpenMixin.__init__(self)
    self.path = path
    self._fd = None
    # dummy value since all I/O goes through datafrom, which uses pread
    self._seek_offset = 0

  def __str__(self):
    return "PlatonicFile(%s)" % (shortpath(self.path,))

  def startup(self):
    ''' Startup: open the file for read.
    '''
    self._fd = os.open(self.path, os.O_RDONLY)

  def shutdown(self):
    ''' Shutdown: close the file.
    '''
    os.close(self._fd)
    self._fd = None

  def tell(self):
    ''' Return the notional file offset.
    '''
    return self._seek_offset

  def seek(self, pos, how=SEEK_SET):
    ''' Adjust the notional file offset.
    '''
    if how == SEEK_SET:
      pass
    elif how == SEEK_CUR:
      pos += self._seek_offset
    elif how == SEEK_END:
      pos += os.fstat(self._fd).st_size
    else:
      raise ValueError("unsupported seek how value: 0x%02x" % (how,))
    if pos < 0:
      raise ValueError("seek out of range")
    self._seek_offset = pos

  def datafrom(self, offset, readsize=None):
    ''' Return an iterable of data from this file from `offset`.
    '''
    if readsize is None:
      readsize = DEFAULT_READSIZE
    return datafrom_fd(self._fd, offset, readsize)

  def fetch(self, offset, length):
    ''' Fetch the data from this file at `offset`.
    '''
    data = self.read(length, offset=offset, longread=True)
    if len(data) != length:
      raise RuntimeError(
          "%r: asked for %d bytes from offset %d, but got %d"
          % (self.path, length, offset, len(data)))
    return data

class PlatonicDir(_FilesDir):
  ''' Presentation of a block map based on a raw directory tree of
      files such as a preexisting media server.

      A PlatonicDir may be used as the Mapping for a MappingStore.

      NB: _not_ thread safe; callers must arrange that.

      A PlatonicDir is read-only. Data blocks are fetched directly
      from the files in the backing directory tree.
  '''

  # delays during scanning to limit the CPU and I/O impact of the
  # monitoring and updating
  DELAY_INTERSCAN = 1.0     # regular pause between directory scans
  DELAY_INTRASCAN = 0.1     # stalls during scan: per directory and after big files

  index_entry_class = PlatonicDirIndexEntry

  def __init__(
      self,
      statedirpath, hashclass,
      *,
      exclude_dir=None, exclude_file=None,
      follow_symlinks=False, archive=None, meta_store=None,
      **kw
  ):
    ''' Initialise the PlatonicDir with `statedirpath` and `datadirpath`.

        Parameters:
        * `statedirpath`: a directory containing state information about the
          DataFiles; this is the index-state.sqlite file and the associated
          index dbm-ish files.
        * `hashclass`: the hash class used to index chunk contents.
        * `exclude_dir`: optional function to test a directory path for
          exclusion from monitoring; default is to exclude directories
          whose basename commences with a dot.
        * `exclude_file`: optional function to test a file path for
          exclusion from monitoring; default is to exclude directories
          whose basename commences with a dot.
        * `follow_symlinks`: follow symbolic links, default False.
        * `meta_store`: an optional Store used to maintain a Dir
          representing the ideal directory; unhashed data blocks
          encountered during scans which are promoted to HashCodeBlocks
          are also stored here
        * `archive`: optional Archive ducktype instance with a
          .update(Dirent[,when]) method

        Other keyword arguments are passed to _FilesDir.__init__.

        The directory and file paths tested are relative to the
        data directory path.
    '''
    if meta_store is None:
      raise ValueError("meta_store may not be None")
    super().__init__(statedirpath, hashclass, **kw)
    if exclude_dir is None:
      exclude_dir = self._default_exclude_path
    if exclude_file is None:
      exclude_file = self._default_exclude_path
    self.exclude_dir = exclude_dir
    self.exclude_file = exclude_file
    self.follow_symlinks = follow_symlinks
    self.meta_store = meta_store
    if meta_store is not None and archive is None:
      # use the default archive
      archive = self.get_Archive(missing_ok=True)
    elif archive is not None:
      if isinstance(archive, str):
        archive = Archive(archive)
    self.archive = archive
    self.topdir = None

  def startup(self):
    if self.meta_store is not None:
      self.meta_store.open()
      archive = self.archive
      D = archive.last.dirent
      if D is None:
        info("%r: no archive entries, create empty topdir Dir", archive)
        D = Dir('.')
        archive.update(D)
      self.topdir = D
    super().startup()

  def shutdown(self):
    if self.meta_store is not None:
      self.sync_meta()
      self.meta_store.close()
    super().shutdown()

  def sync_meta(self):
    ''' Update the Archive state.
    '''
    # update the topdir state before any save
    if self.meta_store is not None:
      with self.meta_store:
        self.archive.update(self.topdir)
        ##dump_Dirent(self.topdir, recurse=True)

  @staticmethod
  def _default_exclude_path(path):
    ''' Default function to exclude a path from the file tree traversal.
    '''
    base = basename(path)
    return not base or base.startswith('.')

  def _open_datafile(self, n):
    ''' Return the DataFile with index `n`.
    '''
    cache = self._cache
    DF = cache.get(n)
    if DF is None:
      with self._lock:
        # first, look again now that we have the _lock
        DF = cache.get(n)
        if DF is None:
          # still not in the cache, open the DataFile and put into the cache
          DFstate = self._filemap[n]
          DF = cache[n] = PlatonicFile(self.datapathto(DFstate.filename))
          DF.open()
    return DF

  def fetch(self, entry):
    ''' Return the data chunk stored in DataFile `n` at `offset`.
    '''
    DF = self._open_datafile(entry.n)
    return DF.fetch(entry.offset, entry.length)

  @logexc
  def _monitor_datafiles(self):
    ''' Thread body to poll the ideal tree for new or changed files.
    '''
    meta_store = self.meta_store
    filemap = self._filemap
    indexQ = self._indexQ
    datadirpath = self.pathto('data')
    if meta_store is not None:
      topdir = self.topdir
    else:
      warning("%s: no meta_store!", self)
    updated = False
    disabled = False
    while not self.cancelled:
      time.sleep(self.DELAY_INTERSCAN)
      if self.flag_scan_disable:
        if not disabled:
          info("scan %r DISABLED", shortpath(datadirpath))
          disabled = True
        continue
      else:
        if disabled:
          info("scan %r ENABLED", shortpath(datadirpath))
          disabled = False
      # scan for new datafiles
      with Pfx("%r", datadirpath):
        seen = set()
        info("scan tree...")
        for dirpath, dirnames, filenames in os.walk(datadirpath, followlinks=True):
          time.sleep(self.DELAY_INTRASCAN)
          if self.cancelled or self.flag_scan_disable:
            break
          rdirpath = relpath(dirpath, datadirpath)
          with Pfx(rdirpath):
            # this will be the subdirectories into which to recurse
            pruned_dirnames = []
            for dname in dirnames:
              if self.exclude_dir(joinpath(rdirpath, dname)):
                # unwanted
                continue
              subdirpath = joinpath(dirpath, dname)
              try:
                S = os.stat(subdirpath)
              except OSError as e:
                # inaccessable
                warning("stat(%r): %s, skipping", subdirpath, e)
                continue
              ino = S.st_dev, S.st_ino
              if ino in seen:
                # we have seen this subdir before, probably via a symlink
                # TODO: preserve symlinks? attach alter ego directly as a Dir?
                debug("seen %r (dev=%s,ino=%s), skipping", subdirpath, ino[0], ino[1])
                continue
              seen.add(ino)
              pruned_dirnames.append(dname)
            dirnames[:] = pruned_dirnames
            if meta_store is None:
              warning("no meta_store")
              D = None
            else:
              with meta_store:
                D = topdir.makedirs(rdirpath, force=True)
                # prune removed names
                names = list(D.keys())
                for name in names:
                  if name not in dirnames and name not in filenames:
                    info("del %r", name)
                    del D[name]
            for filename in filenames:
              with Pfx(filename):
                if self.cancelled or self.flag_scan_disable:
                  break
                rfilepath = joinpath(rdirpath, filename)
                if self.exclude_file(rfilepath):
                  continue
                filepath = joinpath(dirpath, filename)
                if not isfilepath(filepath):
                  continue
                # look up this file in our file state index
                DFstate = filemap.get(rfilepath)
                if (
                    DFstate is not None
                    and D is not None
                    and filename not in D
                ):
                  # in filemap, but not in dir: start again
                  warning("in filemap but not in Dir, rescanning")
                  filemap.del_path(rfilepath)
                  DFstate = None
                if DFstate is None:
                  DFstate = filemap.add_path(rfilepath)
                try:
                  new_size = DFstate.stat_size(self.follow_symlinks)
                except OSError as e:
                  if e.errno == errno.ENOENT:
                    warning("forgetting missing file")
                    self._del_datafilestate(DFstate)
                  else:
                    warning("stat: %s", e)
                  continue
                if new_size is None:
                  # skip non files
                  debug("SKIP non-file")
                  continue
                if meta_store:
                  try:
                    E = D[filename]
                  except KeyError:
                    E = FileDirent(filename)
                    D[filename] = E
                  else:
                    if not E.isfile:
                      info("new FileDirent replacing previous nonfile: %s", E)
                      E = FileDirent(filename)
                      D[filename] = E
                if new_size > DFstate.scanned_to:
                  if DFstate.scanned_to > 0:
                    info("scan from %d", DFstate.scanned_to)
                  if meta_store is not None:
                    blockQ = IterableQueue()
                    R = meta_store._defer(
                        lambda B, Q: top_block_for(spliced_blocks(B, Q)),
                        E.block, blockQ)
                  scan_from = DFstate.scanned_to
                  scan_start = time.time()
                  for offset, flags, data, post_offset in DFstate.scanfrom(
                      offset=DFstate.scanned_to, do_decompress=True,
                  ):
                    assert flags == 0
                    hashcode = self.hashclass.from_chunk(data)
                    indexQ.put( (
                        hashcode,
                        PlatonicDirIndexEntry(DFstate.filenum, offset, len(data)),
                        post_offset
                    ) )
                    if meta_store is not None:
                      B = Block(data=data, hashcode=hashcode, added=True)
                      blockQ.put( (offset, B) )
                    DFstate.scanned_to = post_offset
                    if self.cancelled or self.flag_scan_disable:
                      break
                  if meta_store is not None:
                    blockQ.close()
                    try:
                      top_block = R()
                    except MissingHashcodeError as e:
                      error("missing data, forcing rescan: %s", e)
                      DFstate.scanned_to = 0
                    else:
                      E.block = top_block
                      D.changed = True
                      updated = True
                  elapsed = time.time() - scan_start
                  scanned = DFstate.scanned_to - scan_from
                  if elapsed > 0:
                    scan_rate = scanned / elapsed
                  else:
                    scan_rate = None
                  if scan_rate is None:
                    info(
                        "scanned to %d: %s",
                        DFstate.scanned_to,
                        transcribe_bytes_geek(scanned))
                  else:
                    info(
                        "scanned to %d: %s at %s/s",
                        DFstate.scanned_to,
                        transcribe_bytes_geek(scanned),
                        transcribe_bytes_geek(scan_rate))
                  # stall after a file scan, briefly, to limit impact
                  if elapsed > 0:
                    time.sleep(min(elapsed, self.DELAY_INTRASCAN))
            # update the archive after updating from a directory
            if updated and meta_store is not None:
              self.sync_meta()
              updated = False
      self.flush()

  @staticmethod
  def scanfrom(filepath, offset=0, do_decompress=False):
    ''' Scan the specified `filepath` from `offset`, yielding data chunks.
    '''
    assert do_decompress is True
    scanner = scanner_from_filename(filepath)
    with open(filepath, 'rb') as fp:
      fp.seek(offset)
      for data in blocked_chunks_of(read_from(fp, DEFAULT_SCAN_SIZE), scanner):
        post_offset = offset + len(data)
        yield offset, 0, data, post_offset
        offset = post_offset

if __name__ == '__main__':
  from .datadir_tests import selftest
  selftest(sys.argv)
