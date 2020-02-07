#!/usr/bin/python -tt
#
# Data stores based on local files.
# - Cameron Simpson <cs@cskk.id.au>
#

''' Data directories: the sharable filesystem directories
    storing the backing files for the `DataDir` and `PlatonicDir`
    Store classes.

    A `DataDir` uses `DataFile` formatted flat files to hold the block data,
    consisting of block records holding a small header and the block data,
    often compressed.
    New block data are appended to active datafiles
    up an an arbitrary size threshold,
    when a new datafile is commenced.
    Datafiles are named with UUIDs so that independent programmes
    might share the directory of datafiles without conflict;
    the append-only storage process means that they may monitor
    updates from other programmes simply by watching file sizes
    and scanning the new data.

    A `RawDataDir` behaves like an ordinary `DataDir`
    except that the data files contains the raw uncompressed data bytes.
    This notional use case is as a local cache of efficiently accessed data.
    The intent is that one might pull all the leaf data of a "file"
    into the store contiguously in order to obtain more efficient data access.

    A `PlatonicDir` uses an ordinary directory tree as the backing store,
    obviating the requirement to copy original data into a `DataDir`.
    Such a tree should generally just acquire new files;
    existing files are not expected to have their content modified.
    The typical examples include a media server's file tree
    or a large repository of scientific data.
    The `PlatonicDir` maintains a mapping of hashcodes
    to their block data location within the backing files.
'''

from binascii import hexlify
from collections import namedtuple
from collections.abc import Mapping
import errno
import os
from os import (
    SEEK_SET,
    SEEK_CUR,
    SEEK_END,
)
from os.path import (
    basename, exists as existspath, isdir as isdirpath, isfile as isfilepath,
    join as joinpath, relpath
)
import sqlite3
import stat
import sys
import time
from types import SimpleNamespace
from uuid import uuid4
from icontract import require
from cs.app.flag import DummyFlags, FlaggedMixin
from cs.cache import LRU_Cache
from cs.excutils import logexc
from cs.fileutils import (
    DEFAULT_READSIZE,
    ReadMixin,
    datafrom_fd,
    read_from,
    shortpath,
)
from cs.logutils import debug, info, warning, error, exception
from cs.pfx import Pfx
from cs.py.func import prop as property
from cs.queues import IterableQueue
from cs.resources import MultiOpenMixin, RunStateMixin
from cs.seq import imerge
from cs.serialise import get_bs, put_bs
from cs.threads import locked, bg as bg_thread
from cs.units import transcribe_bytes_geek
from cs.x import X
from . import MAX_FILE_SIZE, Lock, RLock
from .archive import Archive
from .block import Block
from .blockify import (
    DEFAULT_SCAN_SIZE, blocked_chunks_of, spliced_blocks, top_block_for
)
from .datafile import DataRecord, DATAFILE_DOT_EXT
from .dir import Dir, FileDirent
from .hash import HashCode, HashCodeUtilsMixin, MissingHashcodeError
from .index import choose as choose_indexclass
from .parsers import scanner_from_filename
from .util import buffer_from_pathname, createpath, openfd_read, openfd_append

DEFAULT_DATADIR_STATE_NAME = 'default'

RAWFILE_DOT_EXT = '.data'

# 1GiB rollover
DEFAULT_ROLLOVER = MAX_FILE_SIZE

# flush the index after this many updates in the index updater worker thread
INDEX_FLUSH_RATE = 16384

class DataFileState(SimpleNamespace):
  ''' General state information about a data file
      in use by a files based data dir
      (any subclass of `FilesdDir`).

      Attributes:
      * `datadir`: the `FilesDir` tracking this state.
      * `filenum`: the numeric index of this file.
      * `filename`: path relative to the `FilesDir`'s data directory.
      * `indexed_to`: the amount of data scanned and indexed so far.
  '''

  def __init__(
      self,
      datadir,
      filenum,
      filename,
      indexed_to=0,
      scanned_to=None,
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
    ''' Scan this datafile from the supplied `offset` (default `0`)
        yielding `(offset,flags,data, post_offset)`.

        We use the `DataDir`'s `.scanfrom` method because it knows the
        format of the file.
    '''
    yield from self.datadir.scanfrom(self.pathname, offset=offset, **kw)

class FilesDir(HashCodeUtilsMixin, MultiOpenMixin, RunStateMixin, FlaggedMixin,
               Mapping):
  ''' Base class indexing locally stored data in files.
      This class is hashclass specific;
      the utilising Store maintains one of these for each supported hashclass.

      There are two main subclasses of this at present:
      * `DataDir`: the data are kept in a subdirectory of UUID-named files,
        supporting easy merging and updating.
      * `PlatonicDataDir`: the data are present in a normal file tree,
        such as a preexisting media server directory or the like.
  '''

  STATE_FILENAME_FORMAT = 'index-{hashname}-state.sqlite'
  INDEX_FILENAME_BASE_FORMAT = 'index-{hashname}'
  DATA_ROLLOVER = DEFAULT_ROLLOVER

  def __init__(
      self,
      topdirpath,
      hashclass,
      *,
      indexclass=None,
      rollover=None,
      flags=None,
      flags_prefix=None,
  ):
    ''' Initialise the `DataDir` with `topdirpath`.

        Parameters:
        * `topdirpath`: a directory containing state information about the
          `DataFile`s; this contains the index-state.csv file and the
          associated index dbm-ish files.
        * `hashclass`: the hashclass used for indexing
        * `indexclass`: the `IndexClass` providing the index to chunks in the
          `DataFile`s. If not specified, a supported index class with an
          existing index file will be chosen, otherwise the most favoured
          indexclass available will be chosen.
        * `rollover`: data file roll over size; if a data file grows beyond
          this a new datafile is commenced for new blocks.
          Default: `self.DATA_ROLLOVER`.
        * `flags`: optional `Flags` object for control; if specified then
          `flags_prefix` is also required.
        * `flags_prefix`: prefix for control flag names.

        Note that `__init__` only saves the settings such as the `indexclass`
        and ensures that requisite directories exist.
        The monitor thread and runtime state are set up by the `startup` method
        and closed down by the `shutdown` method.
    '''
    assert isinstance(topdirpath, str)
    assert issubclass(hashclass, HashCode), "hashclass=%r" % (hashclass,)
    RunStateMixin.__init__(self)
    MultiOpenMixin.__init__(self)
    if flags is None:
      if flags_prefix is None:
        flags = DummyFlags()
        flags_prefix = 'DUMMY'
    else:
      if flags_prefix is None:
        raise ValueError("flags provided but no flags_prefix")
    FlaggedMixin.__init__(self, flags=flags, prefix=flags_prefix)
    if rollover is None:
      rollover = self.DATA_ROLLOVER
    elif rollover < 1024:
      raise ValueError(
          "rollover < 1024"
          " (a more normal size would be in megabytes or gigabytes): %r" %
          (rollover,)
      )
    self.rollover = rollover
    self.hashclass = hashclass
    self.hashname = hashclass.HASHNAME
    self.topdirpath = topdirpath
    self.statefilepath = joinpath(
        topdirpath, self.STATE_FILENAME_FORMAT.format(hashname=self.hashname)
    )
    if indexclass is None:
      indexclass = choose_indexclass(
          self.INDEX_FILENAME_BASE_FORMAT.format(hashname=self.hashname)
      )
    self.indexclass = indexclass
    self._filemap = None
    self._unindexed = None
    self._cache = None
    self._indexQ = None
    self._index_Thread = None
    self._monitor_Thread = None
    self._WDFstate = None
    self._lock = RLock()

  def __str__(self):
    return '%s(%s)' % (self.__class__.__name__, shortpath(self.topdirpath))

  def __repr__(self):
    return (
        '%s(topdirpath=%r,indexclass=%s)' %
        (self.__class__.__name__, self.topdirpath, self.indexclass)
    )

  def initdir(self):
    ''' Init a directory and its "data" subdirectory.
    '''
    topdirpath = self.topdirpath
    if not isdirpath(topdirpath):
      info("mkdir %r", topdirpath)
      with Pfx("mkdir(%r)", topdirpath):
        os.mkdir(topdirpath)
    datasubdirpath = joinpath(topdirpath, 'data')
    if not isdirpath(datasubdirpath):
      info("mkdir %r", datasubdirpath)
      with Pfx("mkdir(%r)", datasubdirpath):
        os.mkdir(datasubdirpath)

  def startup(self):
    ''' Start up the FilesDir: take locks, start worker threads etc.
    '''
    X("STARTUP %s ...", type(self).__name__)
    self.initdir()
    self._rfds = {}
    self._unindexed = {}
    self._filemap = SqliteFilemap(self, self.statefilepath)
    hashname = self.hashname
    self.index = self.indexclass(
        self.pathto(self.INDEX_FILENAME_BASE_FORMAT.format(hashname=hashname)),
        self.hashclass,
        self.index_entry_class.from_bytes,
    )
    self.index.open()
    self.runstate.start()
    # cache of open DataFiles
    self._cache = LRU_Cache(
        maxsize=4, on_remove=lambda k, datafile: datafile.close()
    )
    # Set up indexing thread.
    # Map individual hashcodes to locations before being persistently stored.
    # This lets us add data, stash the location in _unindexed and
    # drop the location onto the _indexQ for persistent storage in
    # the index asynchronously.
    self._indexQ = IterableQueue(64)
    self._index_Thread = bg_thread(
        self._index_updater,
        name="%s-index-thread" % (self,),
    )
    self._monitor_Thread = bg_thread(
        self._monitor_datafiles,
        name="%s-datafile-monitor" % (self,),
    )

  def shutdown(self):
    ''' Shut down the `FilesDir`: cancel the runstate, close the
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
    # close the write file descriptor, if any
    wfd = self.__dict__.get('_wfd')
    if wfd is not None:
      with Pfx("os.close(wfd:%d)", wfd):
        os.close(wfd)
      del self._wfd
    # close the read file descriptors
    for rfd in self._rfds.values():
      with Pfx("os.close(rfd:%d)", rfd):
        os.close(rfd)
    del self._rfds
    self.runstate.stop()

  def pathto(self, rpath):
    ''' Return the path to `rpath`, which is relative to the `topdirpath`.
    '''
    return joinpath(self.topdirpath, rpath)

  def datapathto(self, rpath):
    ''' Return the path to `rpath`, which is relative to the `datadirpath`.
    '''
    return self.pathto(joinpath('data', rpath))

  def __getattr__(self, attr):
    if attr == '_wfd':
      # no ._wfd: create a new write data file and return the new wfd
      with self._lock:
        wfd = self.__dict__.get('_wfd')
        if wfd is None:
          DFstate = self.new_datafile()
          wfd = self._wfd = openfd_append(DFstate.pathname)
          self._WDFstate = DFstate
      return wfd
    return super().__getattr__(attr)

  def new_datafile(self):
    ''' Create a new datafile.
        Return its `DataFileState`.
    '''
    while True:
      filename = str(uuid4()) + self.DATA_DOT_EXT
      pathname = self.datapathto(filename)
      if existspath(pathname):
        error("new datafile path already exists, retrying: %r", pathname)
        continue
      with Pfx(pathname):
        try:
          createpath(pathname)
        except OSError as e:
          if e.errno == errno.EEXIST:
            error("new datafile path already exists")
            continue
          raise
      break
    return self._filemap.add_path(filename)

  def add(self, data, hashclass):
    ''' Add the supplied data chunk to the current save DataFile,
        return the hashcode.
        Roll the internal state over to a new file if the current
        datafile has reached the rollover threshold.

        Subclasses must define the `data_record(data)` method
        and the `index_entry(filenum,offset)` method.
    '''
    bs = self.data_record(data)
    with self._lock:
      wfd = self._wfd
      filenum = self._WDFstate.filenum
      offset = os.lseek(wfd, 0, SEEK_END)
      n = os.write(wfd, bs)
      rollover = self.rollover
      if rollover is not None and offset + n >= rollover:
        # file now full, close it so as to start a new one on next write
        os.close(wfd)
        del self._wfd
        del self._WDFstate
    length = len(bs)
    if n != length:
      raise ValueError(
          "filenum %d: os.write(%d-bytes) wrote only %d bytes" %
          (filenum, length, n)
      )
    entry = self.index_entry(filenum, offset, length)
    post_offset = offset + length
    hashcode = hashclass.from_chunk(data)
    self._queue_index(hashcode, entry, post_offset)
    return hashcode

  def get_Archive(self, name=None, **kw):
    ''' Return the Archive named `name`.

        If `name` is omitted or `None`
        the Archive path is the `topdirpath`
        plus the extension `'.vt'`.
        Otherwise it is the `topdirpath` plus a dash plus the `name`
        plus the extension `'.vt'`.
        The `name` may not be empty or contain a dot or a dash.
    '''
    with Pfx("%s.get_Archive", self):
      if name is None or not name:
        archivepath = self.topdirpath + '.vt'
      else:
        if '.' in name or '/' in name:
          raise ValueError("invalid name: %r" % (name,))
        archivepath = self.topdirpath + '-' + name + '.vt'
      return Archive(archivepath, **kw)

  def _queue_index(self, hashcode, entry, post_offset):
    if not isinstance(entry, self.index_entry_class):
      raise RuntimeError(
          "expected %s but got %s %r" %
          (self.index_entry_class, type(entry), entry)
      )
    with self._lock:
      self._unindexed[hashcode] = entry
    self._indexQ.put((hashcode, entry, post_offset))

  def _queue_index_flush(self):
    self._indexQ.put(None)

  @logexc
  def _index_updater(self):
    ''' Thread body to collect hashcode index data from `.indexQ` and store it.
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
          raise RuntimeError(
              "expected %s but got %s %r" % (entry_class, type(entry), entry)
          )
        with self._lock:
          index[hashcode] = entry
          try:
            del unindexed[hashcode]
          except KeyError:
            # this can happen when the same key is indexed twice
            # entirely plausible if a new datafile is added to the datadir
            pass
        DFstate = filemap[entry.filenum]
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
          % (hashcode, h)
      )

  def __len__(self):
    return len(self.index)

  def hashcodes_from(self, start_hashcode=None, reverse=False):
    ''' Generator yielding the hashcodes from the database in order
        starting with optional `start_hashcode`.

        Parameters:
        * `start_hashcode`: the first hashcode; if missing or `None`,
          iteration starts with the first key in the index
        * `reverse`: iterate backwards if true, otherwise forwards.
    '''
    unindexed = set(self._unindexed)
    indexed = self.index.hashcodes_from(
        start_hashcode=start_hashcode, reverse=reverse
    )
    unseen_indexed = (h for h in indexed if h not in unindexed)
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
    filenum = entry.filenum
    try:
      try:
        rfd = self._rfds[filenum]
      except KeyError:
        # TODO: shove this sideways to self.open_datafile
        # which releases an existing datafile if too many are open
        DFstate = self._filemap[filenum]
        rfd = self._rfds[filenum] = openfd_read(DFstate.pathname)
      return entry.fetch_fd(rfd)
    except Exception as e:
      exception("%s[%s]:%s not available: %s", self, hashcode, entry, e)
      raise KeyError(str(hashcode))

class SqliteFilemap:
  ''' The file mapping of `filenum` to `DataFileState`.

      The implementation is an in-memory dict with an SQLite database
      as backing store. SQLite databases are portable across
      architectures and may have multiple users, so that `DataDir`s
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
    c.execute(
        r'''
        CREATE TABLE IF NOT EXISTS settings (
            `setting` TEXT,
            `value`   TEXT,
            CONSTRAINT `setting` UNIQUE (`setting`)
        );'''
    )
    c.execute(
        r'''
        CREATE TABLE IF NOT EXISTS filemap (
            `id`   INTEGER PRIMARY KEY,
            `path` TEXT,
            `indexed_to` INTEGER,
            CONSTRAINT `path` UNIQUE (`path`)
        );'''
    )
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
    ''' Return the active `DFstate` filenums.
    '''
    with self._lock:
      return list(self.n_to_DFstate.keys())

  def items(self):
    ''' Return the active `(filenum,DFstate)` pairs.
    '''
    with self._lock:
      return list(self.n_to_DFstate.items())

  def _map(self, path, filenum, indexed_to=0):
    ''' Add a `DataFileState` for `path` and `filenum` to the mapping.
    '''
    if path is None:
      error(
          "ignoring %s._map(path=%r,filenum=%r,indexed_to=%r)",
          type(self).__name__, path, filenum, indexed_to
      )
      return
    datadir = self.datadir
    if filenum in self.n_to_DFstate:
      warning("replacing n_to_DFstate[%s]", filenum)
    if path in self.path_to_DFstate:
      warning("replacing path_toDFstate[%r]", path)
    DFstate = DataFileState(datadir, filenum, path, indexed_to=indexed_to)
    self.n_to_DFstate[filenum] = DFstate
    self.path_to_DFstate[path] = DFstate

  def _load_map(self):
    with self._lock:
      c = self._execute(
          r'''
          SELECT id, path, indexed_to FROM filemap
      '''
      )
      for filenum, path, indexed_to in c.fetchall():
        self._map(path, filenum, indexed_to)
      c.close()

  @require(lambda new_path: new_path is not None)
  ##@require(lambda new_path: isfilepath(new_path))
  def add_path(self, new_path, indexed_to=0):
    ''' Insert a new path into the map.
        Return its `DataFileState`.
    '''
    info("new path %r", shortpath(new_path))
    with Pfx("add_path(%r,indexed_to=%d)", new_path, indexed_to):
      with self._lock:
        c = self._modify(
            r'''
            INSERT INTO filemap(`path`, `indexed_to`) VALUES (?, ?)
        ''', (new_path, 0),
            return_cursor=True
        )
        if c:
          filenum = c.lastrowid
          self._map(new_path, filenum, indexed_to=indexed_to)
          c.close()
        else:
          # TODO: look up the path=>(filenum,indexed_to) as fallback
          error("FAILED")
          return None
      return self.n_to_DFstate[filenum]

  def del_path(self, old_path):
    ''' Forget the information for `old_path`.

        In order to prevent reuse of an id we just set the record's
        `path` and `indexed_to` to `NULL`.
    '''
    DFstate = self.path_to_DFstate[old_path]
    with self._lock:
      self._modify(
          r'''
          UPDATE filemap SET path=NULL, indexed_to=NULL where id = ?
      ''', (DFstate.filenum,)
      )
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

  def set_indexed_to(self, filenum, new_indexed_to):
    ''' Update the `indexed_to` value for path `filenum`.
    '''
    DFstate = self.n_to_DFstate[filenum]
    with self._lock:
      self._modify(
          r'''
          UPDATE filemap SET indexed_to = ? WHERE id = ?
      ''', (new_indexed_to, filenum)
      )
    DFstate.indexed_to = new_indexed_to

class DataDirIndexEntry(namedtuple('DataDirIndexEntry', 'filenum offset')):
  ''' A block record for a `DataDir`.

      This has the following attributes:
      * `filenum`: the file number of the file containing the block
      * `data_offset`: the offset within the file of the data chunk
      * `data_length`: the length of the chunk
      * `flags`: information about the chunk

      The only defined flag at present is
      `DataDirIndexEntry.FLAG_COMPRESSED`, indicating that the raw
      data should be obtained by uncompressing the chunk using
      `zlib.uncompress`.
  '''

  @classmethod
  def from_bytes(cls, data: bytes):
    ''' Parse a binary index entry, return `(filenum,offset)`.
    '''
    filenum, offset = get_bs(data)
    file_offset, offset = get_bs(data, offset)
    if offset != len(data):
      raise ValueError(
          "unparsed data from index entry; full entry = %s; filenum=%d, file_offset=%d, unparsed=%r"
          % (hexlify(data), filenum, file_offset, data[offset:])
      )
    return cls(filenum, file_offset)

  def encode(self) -> bytes:
    ''' Encode (filenum, offset) to binary form for use as an index entry.
    '''
    return put_bs(self.filenum) + put_bs(self.offset)

  def fetch_fd(self, rfd):
    ''' Return the data associated with `entry` from `rfd`.
    '''
    return DataRecord.from_fd(rfd, self.offset).data

class DataDir(FilesDir):
  ''' Maintenance of a collection of `DataFile`s in a directory.

      A `DataDir` may be used as the Mapping for a `MappingStore`.

      NB: _not_ thread safe; callers must arrange that.

      The directory may be maintained by multiple instances of this
      class as they will not try to add data to the same `DataFile`.
      This is intended to address shared `Store`s such as a `Store` on
      a NAS presented via NFS, or a `Store` replicated by an external
      file-level service such as Dropbox or plain old rsync.
  '''

  DATA_DOT_EXT = DATAFILE_DOT_EXT
  DATA_ROLLOVER = DEFAULT_ROLLOVER

  index_entry_class = DataDirIndexEntry

  @staticmethod
  def data_record(data):
    ''' Return the record for data to save in the save file.
    '''
    return bytes(DataRecord(data))

  @staticmethod
  def index_entry(filenum, offset, _):
    ''' Construct an index entry from the file number and offset.
    '''
    return DataDirIndexEntry(filenum, offset)

  @staticmethod
  def scanfrom(filepath, offset=0):
    ''' Scan the specified `filepath` from `offset`, yielding data chunks.
    '''
    bfr = buffer_from_pathname(filepath, offset=offset)
    for record in DataRecord.parse_buffer(bfr):
      yield record

  def _monitor_datafiles(self):
    ''' Thread body to poll all the datafiles regularly for new data arrival.

        This is what supports shared use of the data area. Other clients
        may write to their own datafiles and this thread sees new files
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
          if (not filename.startswith('.')
              and filename.endswith(DATAFILE_DOT_EXT)
              and filename not in filemap):
            info("MONITOR: add new filename %r", filename)
            filemap.add_path(filename)
      # now scan known datafiles for new data
      for filenum in self._filemap.filenums():
        if self.cancelled or self.flag_scan_disable:
          break
        # don't monitor the current datafile: our own actions will update it
        WDFstate = self._WDFstate
        if WDFstate and filenum == WDFstate.filenum:
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
              indexQ.put(
                  (hashcode, DataDirIndexEntry(filenum, offset), post_offset)
              )
              DFstate.scanned_to = post_offset
              if self.cancelled:
                break
              offset = post_offset
            self.flush()
        self.flush()
      time.sleep(1)

class RawIndexEntry(namedtuple('RawIndexEntry', 'filenum offset length')):
  ''' A block record for a raw data file.
  '''

  @classmethod
  def from_bytes(cls, data: bytes):
    ''' Parse a binary index entry, return (filenum, offset).
    '''
    filenum, offset = get_bs(data)
    file_offset, offset = get_bs(data, offset)
    length, offset = get_bs(data, offset)
    if offset != len(data):
      raise ValueError(
          "unparsed data from index entry; full entry = %s" % (hexlify(data),)
      )
    return cls(filenum, file_offset, length)

  def encode(self) -> bytes:
    ''' Encode (filenum, offset) to binary form for use as an index entry.
    '''
    return put_bs(self.filenum) + put_bs(self.offset) + put_bs(self.length)

  def fetch_fd(self, rfd):
    ''' Fetch the bytes associated with this entry from `rfd`.
    '''
    bs = os.pread(rfd, self.length, self.offset)
    if len(bs) != self.length:
      raise ValueError(
          "incorrect pread(fd=%d,length=%d,offset=%d): got %d bytes" %
          (rfd, self.length, self.offset, len(bs))
      )
    return bs

class RawDataDir(FilesDir):
  ''' Maintenance of a collection of raw data files in a directory.

      This is intended as a fairly fast local data cache directory.
      Records are read directly from the files, which are not
      compressed or encapsulated.

      Hypothetical use case is the pull the leaf data of a large
      file into the store contiguously to effect efficient reads
      of that data later.

      A RawDataDir may be used as the Mapping for a MappingStore.

      NB: _not_ thread safe; callers must arrange that.
  '''

  DATA_DOT_EXT = RAWFILE_DOT_EXT
  DATA_ROLLOVER = DEFAULT_ROLLOVER

  index_entry_class = RawIndexEntry

  @staticmethod
  def index_entry(filenum, offset, length):
    ''' Construct an index entry from the file number and offset.
    '''
    return RawIndexEntry(filenum, offset, length)

  @staticmethod
  def data_record(data):
    ''' Return the record for data to save in the save file.
    '''
    return data

  def _monitor_datafiles(self):
    pass

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

class PlatonicDir(FilesDir):
  ''' Presentation of a block map based on a raw directory tree of
      files such as a preexisting media server.

      A `PlatonicDir` may be used as the Mapping for a `MappingStore`.

      NB: _not_ thread safe; callers must arrange that.

      A `PlatonicDir` is read-only. Data blocks are fetched directly
      from the files in the backing directory tree.
  '''

  # delays during scanning to limit the CPU and I/O impact of the
  # monitoring and updating
  DELAY_INTERSCAN = 1.0  # regular pause between directory scans
  DELAY_INTRASCAN = 0.1  # stalls during scan: per directory and after big files

  index_entry_class = RawIndexEntry

  def __init__(
      self,
      topdirpath,
      hashclass,
      *,
      exclude_dir=None,
      exclude_file=None,
      follow_symlinks=False,
      archive=None,
      meta_store=None,
      **kw
  ):
    ''' Initialise the `PlatonicDir` at `topdirpath`.

        Parameters:
        * `topdirpath`: a directory containing state information about the
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
          encountered during scans which are promoted to `HashCodeBlock`s
          are also stored here
        * `archive`: optional `Archive` ducktype instance with a
          .update(Dirent[,when]) method

        Other keyword arguments are passed to `FilesDir.__init__`.

        The directory and file paths tested are relative to the
        data directory path.
    '''
    if meta_store is None:
      raise ValueError("meta_store may not be None")
    super().__init__(topdirpath, hashclass, **kw)
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

  def _open_datafile(self, filenum):
    ''' Return the DataFile with index `filenum`.
    '''
    cache = self._cache
    DF = cache.get(filenum)
    if DF is None:
      with self._lock:
        # first, look again now that we have the _lock
        DF = cache.get(filenum)
        if DF is None:
          # still not in the cache, open the DataFile and put into the cache
          DFstate = self._filemap[filenum]
          DF = cache[filenum] = PlatonicFile(self.datapathto(DFstate.filename))
          DF.open()
    return DF

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
      if disabled:
        info("scan %r ENABLED", shortpath(datadirpath))
        disabled = False
      # scan for new datafiles
      with Pfx("%r", datadirpath):
        seen = set()
        info("scan tree...")
        for dirpath, dirnames, filenames in os.walk(datadirpath,
                                                    followlinks=True):
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
                debug(
                    "seen %r (dev=%s,ino=%s), skipping", subdirpath, ino[0],
                    ino[1]
                )
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
                if (DFstate is not None and D is not None
                    and filename not in D):
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
                        E.block, blockQ
                    )
                  scan_from = DFstate.scanned_to
                  scan_start = time.time()
                  for offset, flags, data, post_offset in DFstate.scanfrom(
                      offset=DFstate.scanned_to,
                      do_decompress=True,
                  ):
                    assert flags == 0
                    hashcode = self.hashclass.from_chunk(data)
                    indexQ.put(
                        (
                            hashcode,
                            RawIndexEntry(DFstate.filenum, offset,
                                          len(data)), post_offset
                        )
                    )
                    if meta_store is not None:
                      B = Block(data=data, hashcode=hashcode, added=True)
                      blockQ.put((offset, B))
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
                        "scanned to %d: %s", DFstate.scanned_to,
                        transcribe_bytes_geek(scanned)
                    )
                  else:
                    info(
                        "scanned to %d: %s at %s/s", DFstate.scanned_to,
                        transcribe_bytes_geek(scanned),
                        transcribe_bytes_geek(scan_rate)
                    )
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
