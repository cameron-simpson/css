#!/usr/bin/env python3 -tt
#
# Data stores based on local files.
# - Cameron Simpson <cs@cskk.id.au>
#
# pylint: disable=too-many-lines
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

    A `PlatonicDir` uses an ordinary directory tree as the backing store,
    obviating the requirement to copy original data into a `DataDir`.
    Such a tree should generally just acquire new files;
    existing files are not expected to have their content modified.
    The typical examples include a media server's file tree
    or a large repository of scientific data.
    The `PlatonicDir` maintains a mapping of hashcodes
    to their block data location within the backing files.
'''

from collections import defaultdict, namedtuple
from collections.abc import MutableMapping
from contextlib import contextmanager
from dataclasses import dataclass
import errno
from functools import cached_property, partial
from getopt import GetoptError
import os
from os import (
    pread,
    SEEK_SET,
    SEEK_CUR,
    SEEK_END,
)
from os.path import (
    basename, exists as existspath, isfile as isfilepath, join as joinpath,
    realpath, relpath
)
import sqlite3
import stat
import sys
from time import time, sleep
from types import SimpleNamespace
from typing import Optional
from uuid import uuid4
from zlib import decompress

from icontract import require
from typeguard import typechecked

from cs.app.flag import FlaggedMixin
from cs.binary import BinaryMultiValue, BSUInt
from cs.cache import LRU_Cache
from cs.cmdutils import BaseCommand
from cs.context import nullcontext, stackattrs
from cs.fileutils import (
    DEFAULT_READSIZE,
    ReadMixin,
    datafrom_fd,
    read_from,
    shortpath,
)
from cs.fs import HasFSPath, needdir
from cs.lex import s
from cs.logutils import debug, info, warning, error, exception
from cs.obj import SingletonMixin
from cs.pfx import Pfx, pfx_call, pfx_method
from cs.progress import progressbar
from cs.py.func import prop as property  # pylint: disable=redefined-builtin
from cs.queues import IterableQueue
from cs.resources import MultiOpenMixin, RunState, RunStateMixin, uses_runstate
from cs.threads import locked, bg as bg_thread, joinif
from cs.units import transcribe_bytes_geek, BINARY_BYTES_SCALE
from cs.upd import with_upd_proxy, UpdProxy, uses_upd

from . import MAX_FILE_SIZE, Lock, RLock, Store, run_modes
from .archive import Archive
from .block import HashCodeBlock
from .blockify import (
    DEFAULT_SCAN_SIZE, blocked_chunks_of, spliced_blocks, top_block_for
)
from .datafile import DataFile, DATAFILE_DOT_EXT
from .dir import Dir, FileDirent
from .hash import HashCode, HashCodeUtilsMixin, MissingHashcodeError
from .index import choose as choose_indexclass
from .parsers import scanner_from_filename
from .util import createpath, openfd_read

pfx_listdir = partial(pfx_call, os.listdir)

##_sleep = sleep
##
##def sleep(t):
##  print("sleep", t, "...")
##  return _sleep(t)

DEFAULT_DATADIR_STATE_NAME = 'default'

##RAWFILE_DOT_EXT = '.data'

# 1GiB rollover
DEFAULT_ROLLOVER = MAX_FILE_SIZE

# flush the index after this many updates in the index updater worker thread
INDEX_FLUSH_RATE = 16384

def main(argv=None):
  ''' DataDir command line mode. '''
  return DataDirCommand(argv=argv).run()

class FileDataIndexEntry(BinaryMultiValue('FileDataIndexEntry', {
    'filenum': BSUInt,
    'data_offset': BSUInt,
    'data_length': BSUInt,
    'flags': BSUInt,
})):
  ''' An index entry describing a data chunk in a `DataDir`.

      This has the following attributes:
      * `filenum`: the file number of the file containing the block
      * `data_offset`: the offset within the file of the data chunk
      * `data_length`: the length of the chunk
      * `flags`: information about the chunk

      These enable direct access to the raw data component.

      The following flags are defined:
      * `FLAG_COMPRESSED`: the raw data should be obtained
        by uncompressing the chunk using `zlib.uncompress`.
      * `INDIRECT_COMPLETE`: the `IndirectBlock` with this hashcode
        is known to have all its data blocks in the Store
  '''

  FLAG_COMPRESSED = 0x01
  INDIRECT_COMPLETE = 0x02

  @property
  def is_compressed(self):
    ''' Whether the chunk data are compressed.
    '''
    return self.flags & self.FLAG_COMPRESSED

  def fetch_fd(self, rfd):
    ''' Fetch the decompressed data from an open binary file.
    '''
    bs = pread(rfd, self.data_length, self.data_offset)
    if len(bs) != self.data_length:
      raise RuntimeError(
          "%s.fetch_fd: pread(fd=%s) returned %d bytes, expected %d" %
          (self, rfd, len(bs), self.data_length)
      )
    if self.is_compressed:
      bs = decompress(bs)
    return bs

class DataFileState(SimpleNamespace, HasFSPath):
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
  def fspath(self):
    ''' Return the full pathname of this data file.
    '''
    return self.datadir.datapathto(self.filename)

  @cached_property
  def datafile(self):
    ''' The `DataFile` associated with this `DataFileState`.
    '''
    df = DataFile(self.fspath)
    df.open()
    return df

  def __del__(self):
    self.datafile.close()

  def stat_size(self, follow_symlinks=False):
    ''' Stat the datafile, return its size.
    '''
    path = self.fspath
    if follow_symlinks:
      S = os.stat(path)
    else:
      S = os.lstat(path)
    if not stat.S_ISREG(S.st_mode):
      return None
    return S.st_size

  def scanfrom(self, offset=0):
    ''' Scan this datafile from the supplied `offset` (default `0`)
        yielding `(offset,flags,data, post_offset)`.

        We use the `DataDir`'s `.scanfrom` method because it knows the
        format of the file.
    '''
    yield from self.datafile.scanfrom(offset=offset, with_offsets=True)

class FilesDir(SingletonMixin, HasFSPath, HashCodeUtilsMixin, MultiOpenMixin,
               RunStateMixin, FlaggedMixin, MutableMapping):
  ''' Base class indexing locally stored data in files for a specific hashclass.

      There are two main subclasses of this at present:
      * `DataDir`: the data are kept in a subdirectory of UUID-named files,
        supporting easy merging and updating.
      * `PlatonicDataDir`: the data are present in a normal file tree,
        such as a preexisting media server directory or the like.
  '''

  STATE_FILENAME_FORMAT = 'index-{hashname}-state.sqlite'
  INDEX_FILENAME_BASE_FORMAT = 'index-{hashname}'
  DATA_ROLLOVER = DEFAULT_ROLLOVER

  _FD_Singleton_Key_Tuple = namedtuple(
      'FilesDir_FD_Singleton_Key_Tuple',
      'cls realdirpath hashclass indexclass rollover flags_id'
  )

  @classmethod
  def _resolve(cls, *, hashclass, indexclass, rollover, flags, flags_prefix):
    ''' Resolve the `__init__()` arguments,
        shared by `__init__` and `_singleton_key`.
    '''
    if indexclass is None:
      indexclass = choose_indexclass(
          cls.INDEX_FILENAME_BASE_FORMAT.format(hashname=hashclass.hashname)
      )
    if rollover is None:
      rollover = cls.DATA_ROLLOVER
    elif rollover < 1024:
      raise ValueError(
          "rollover < 1024"
          " (a more normal size would be in megabytes or gigabytes): %r" %
          (rollover,)
      )
    if flags is None:
      if flags_prefix is None:
        flags = defaultdict(bool)
        flags_prefix = 'DUMMY'
    else:
      if flags_prefix is None:
        raise ValueError("flags provided but no flags_prefix")
    return SimpleNamespace(
        hashclass=hashclass,
        indexclass=indexclass,
        rollover=rollover,
        flags=flags,
        flags_prefix=flags_prefix
    )

  @classmethod
  def _singleton_key(
      cls,
      fspath,
      *,
      hashclass,
      indexclass=None,
      rollover=None,
      flags=None,
      flags_prefix=None,
      **_,
  ):
    resolved = cls._resolve(
        hashclass=hashclass,
        indexclass=indexclass,
        rollover=rollover,
        flags=flags,
        flags_prefix=flags_prefix
    )
    return cls._FD_Singleton_Key_Tuple(
        cls=cls,
        realdirpath=realpath(fspath),
        hashclass=resolved.hashclass,
        indexclass=resolved.indexclass,
        rollover=resolved.rollover,
        flags_id=id(resolved.flags)
    )

  @uses_runstate
  @require(lambda hashclass: issubclass(hashclass, HashCode))
  def __init__(
      self,
      topdirpath,
      *,
      hashclass,
      indexclass=None,
      rollover=None,
      flags=None,
      flags_prefix=None,
      runstate: RunState,
  ):
    ''' Initialise the `DataDir` at `topdirpath`.

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
    if hasattr(self, '_filemap'):
      return
    resolved = self._resolve(
        hashclass=hashclass,
        indexclass=indexclass,
        rollover=rollover,
        flags=flags,
        flags_prefix=flags_prefix
    )
    HasFSPath.__init__(self, topdirpath)
    RunStateMixin.__init__(self, runstate=runstate)
    MultiOpenMixin.__init__(self)
    FlaggedMixin.__init__(
        self, flags=resolved.flags, prefix=resolved.flags_prefix
    )
    self.indexclass = resolved.indexclass
    self.rollover = resolved.rollover
    self.hashclass = hashclass
    self.hashname = hashclass.hashname
    self.statefilepath = self.pathto(
        self.STATE_FILENAME_FORMAT.format(hashname=self.hashname)
    )
    self.index = None
    self._filemap = None
    self._cache = None
    self._monitor_Thread = None
    # the current output datafile
    self._WDFstate = None
    self._wf = None
    self._lock = RLock()
    # a lock to surround record modifications
    # used by the modify_entry(hashcode) method
    self._modify_lock = Lock()

  def __repr__(self):
    return (
        '%s(topdirpath=%r,indexclass=%s)' %
        (self.__class__.__name__, self.shortpath, self.indexclass)
    )

  @property
  def datapath(self):
    ''' The pathname of the data subdirectory. '''
    return self.pathto('data')

  def datapathto(self, rpath):
    ''' Return the path to `rpath`, which is relative to the `datadirpath`.
    '''
    return joinpath(self.datapath, rpath)

  def initdir(self):
    ''' Init a directory and its "data" subdirectory.
    '''
    needdir(self.fspath, log=info)
    needdir(self.datapath, log=info)

  @contextmanager
  @uses_upd
  @uses_runstate
  def startup_shutdown(self, *, upd, runstate: RunState):
    ''' Start up and shut down the `FilesDir`: take locks, start worker threads etc.
    '''
    with super().startup_shutdown():
      self.initdir()
      hashname = self.hashname
      # cache of open DataFiles
      cache = LRU_Cache(
          max_size=4, on_remove=lambda k, datafile: datafile.close()
      )
      with self.indexclass(self.pathto(
          self.INDEX_FILENAME_BASE_FORMAT.format(hashname=hashname))) as index:
        with stackattrs(
            self,
            _rfds={},
            _filemap=SqliteFilemap(self, self.statefilepath),
            index=index,
            _cache=cache,
        ):
          with runstate:
            # Set up data queue.
            # The .add() method adds the data to self._unindexed, puts the
            # data onto the data queue, and returns.
            # The data queue worker saves the data to backing files and
            # updates the indices.
            with upd.run_task(str(self) + " monitor ") as monitor_proxy:
              with stackattrs(self, _monitor_proxy=monitor_proxy):
                _monitor_Thread = bg_thread(
                    self._monitor_datafiles,
                    name=f'{self}._monitor_datafiles',
                    thread_states=False,
                )
                with stackattrs(self, _monitor_Thread=_monitor_Thread):
                  try:
                    yield
                  finally:
                    self.runstate.cancel()
                    joinif(_monitor_Thread)
                    with self._lock:
                      self.flush()
                      self.WDFclose()
                      # update state to substrate
                      self._filemap.close()
                      # close the read file descriptors
                      for rfd in self._rfds.values():
                        pfx_call(os.close, rfd)

  @property
  def WDFstate(self) -> DataFileState:
    with self._lock:
      WDFstate = self._WDFstate
      if WDFstate is None:
        # create a new data file
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
        WDFstate = self._WDFstate = self._filemap.add_path(filename)
      return WDFstate

  def WDFclose(self):
    ''' Close the current writable `DataFileState` if open.
   '''
    with self._lock:
      WDFstate = self._WDFstate
      if WDFstate is not None:
        ##WDFstate.close()
        self._WDFstate = None

  def __len__(self):
    return len(self.index)

  def __iter__(self):
    return iter(self.hashcodes_from())

  def __contains__(self, h):
    return h in self.index

  def __getitem__(self, hashcode):
    ''' Return the decompressed data associated with the supplied `hashcode`.
    '''
    index = self.index
    try:
      with self._lock:
        entry_bs = index[hashcode]
    except KeyError as e:
      raise KeyError(f'{self}[{hashcode}]: hash not in index') from e
    entry = FileDataIndexEntry.from_bytes(entry_bs)
    filenum = entry.filenum
    with self._lock:
      try:
        try:
          rfd = self._rfds[filenum]
        except KeyError:
          # TODO: shove this sideways to self.open_datafile
          # which releases an existing datafile if too many are open
          DFstate = self._filemap[filenum]
          rfd = self._rfds[filenum] = openfd_read(DFstate.fspath)
        return entry.fetch_fd(rfd)
      except Exception as e:
        exception(f'{self}[{hashcode}]:{entry} not available: {e}')
        raise KeyError(str(hashcode)) from e

  def add(self, data):
    ''' Add `data` to the cache, queue data for indexing, return hashcode.
    '''
    hashcode = self.hashclass.from_chunk(data)
    self[hashcode] = data
    return hashcode

  def __setitem__(self, h, data):
    ''' Store the bytes `data` against key hashcode `h`.
    '''
    assert isinstance(h, self.hashclass)
    assert h == self.hashclass.from_data(data)
    with self._lock:
      index_entry = self.append(data)
      self.index[h] = bytes(index_entry)
      if index_entry.data_offset + index_entry.data_length >= self.rollover:
        # file is full, make a new one next time
        self.WDFclose()

  def __delitem__(self, h):
    raise RuntimeError('we do notexpect to delete from a DataDir')

  def get_index_entry(self, hashcode):
    ''' Return the index entry for `hashcode`, or `None` if there
        is no index or the index has no entry for `hashcode`.
    '''
    entry_bs = self.index.get(hashcode)
    if entry_bs is None:
      return None
    entry = FileDataIndexEntry.from_bytes(entry_bs)
    return entry

  @contextmanager
  def modify_index_entry(self, hashcode):
    ''' Context manager to obtain and yield the `FileDataIndexEntry` for `hashcode`
        and resave it on return.

        Example:

            with index.modify_entry(hashcode) as entry:
                entry.flags |= entry.INDIRECT_COMPLETE
    '''
    with self._modify_lock:
      try:
        entry_bs = self.index[hashcode]
      except KeyError:
        yield None
      else:
        entry = FileDataIndexEntry.from_bytes(entry_bs)
        yield entry
        new_entry_bs = bytes(entry)
        if new_entry_bs != entry_bs:
          self.index[hashcode] = new_entry_bs

  def get_Archive(self, name=None, **kw):
    ''' Return the Archive named `name`.

        If `name` is omitted or `None`
        the Archive path is the `fspath`
        plus the extension `'.vt'`.
        Otherwise it is the `fspath` plus a dash plus the `name`
        plus the extension `'.vt'`.
        The `name` may not be empty or contain a dot or a dash.
    '''
    with Pfx("%s.get_Archive", self):
      if name is None or not name:
        archivepath = self.fspath + '.vt'
      else:
        if '.' in name or '/' in name:
          raise ValueError("invalid name: %r" % (name,))
        archivepath = self.fspath + '-' + name + '.vt'
      return Archive(archivepath, **kw)

  @locked
  def flush(self):
    ''' Flush all the components.
    '''
    self._cache.flush()
    self.index.flush()

  def hashcodes_from(self, *, start_hashcode=None):
    ''' Generator yielding the hashcodes from the database in order
        starting with optional `start_hashcode`.

        Parameters:
        * `start_hashcode`: the first hashcode; if missing or `None`,
          iteration starts with the first key in the index
    '''
    return map(
        self.hashclass, self.index.sorted_keys(start_hashcode=start_hashcode)
    )

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

  def __str__(self):
    return "%s(%r)" % (type(self).__name__, self.path)

  def close(self):
    ''' Close the database.
    '''
    self.conn.close()
    del self.conn

  def _execute(self, sql, *a):
    return self.conn.execute(sql, *a)

  @pfx_method(use_str=True)
  def _modify(self, sql, *a, return_cursor=False, quiet=False):
    sql = sql.strip()
    conn = self.conn
    try:
      c = self._execute(sql, *a)
    except (
        sqlite3.OperationalError,
        sqlite3.IntegrityError,
    ) as e:
      quiet or error("%s: %s [SQL=%r %r]", type(e).__name__, e, sql, a)
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
      c = self._execute('SELECT id, path, indexed_to FROM filemap')
      for filenum, path, indexed_to in c.fetchall():
        self._map(path, filenum, indexed_to)
      c.close()

  @pfx_method
  @require(lambda new_path: new_path is not None)
  ##@require(lambda new_path: isfilepath(new_path))
  @typechecked
  def add_path(self, new_path: str, indexed_to=0) -> DataFileState:
    ''' Insert a new path into the map.
        Return its `DataFileState`.
    '''
    info("new path %r", shortpath(new_path))
    with self._lock:
      c = self._modify(
          'INSERT INTO filemap(`path`, `indexed_to`) VALUES (?, ?)',
          (new_path, 0),
          return_cursor=True,
          quiet=True,
      )
      if c:
        filenum = c.lastrowid
        self._map(new_path, filenum, indexed_to=indexed_to)
        c.close()
        DFstate = self.n_to_DFstate[filenum]
      else:
        # already mapped
        DFstate = self.path_to_DFstate[new_path]
    return DFstate

  def del_path(self, old_path):
    ''' Forget the information for `old_path`.

        In order to prevent reuse of an id we just set the record's
        `path` and `indexed_to` to `NULL`.
    '''
    DFstate = self.path_to_DFstate[old_path]
    with self._lock:
      self._modify(
          'UPDATE filemap SET path=NULL, indexed_to=NULL where id = ?',
          (DFstate.filenum,)
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
          'UPDATE filemap SET indexed_to = ? WHERE id = ?',
          (new_indexed_to, filenum)
      )
    DFstate.indexed_to = new_indexed_to

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

  def append(self, data) -> FileDataIndexEntry:
    ''' Append `data` to the current output file.
    '''
    with self._lock:
      WDFstate = self.WDFstate
      with WDFstate.datafile as df:
        DR, file_offset, length = df.append(data)
      index_entry = FileDataIndexEntry(
          filenum=WDFstate.filenum,
          data_offset=file_offset + DR.data_offset,
          data_length=DR.raw_data_length,
          flags=DR.flags,
      )
    return index_entry

  def datafilenames(self):
    ''' Return a list of the datafile basenames. '''
    try:
      listing = pfx_listdir(self.datapath)
    except OSError as e:
      if e.errno == errno.ENOENT:
        warning("listing failed: %s", e)
        return []
      raise
    return [
        filename for filename in listing if
        (not filename.startswith(',') and filename.endswith(DATAFILE_DOT_EXT))
    ]

  def _monitor_datafiles(self):
    ''' Thread body to poll all the datafiles regularly for new data arrival.

        This is what supports shared use of the data area. Other clients
        may write to their own datafiles and this thread sees new files
        and new data in existing files and scans them, adding the index
        information to the local state.
    '''
    index = self.index
    filemap = self._filemap
    if filemap is None:
      return
    datadirpath = self.datapath
    while not self.cancelled and not self.closed:
      if self.flag_scan_disable:
        sleep(0.1)
        continue
      # scan for new datafiles
      for filename in self.datafilenames():
        if filename not in filemap:
          info("%s: add new filename %r", filename)
          filemap.add_path(filename)
      # now scan known datafiles for new data
      for filenum in filemap.filenums():
        if self.cancelled or self.flag_scan_disable:
          break
        # don't monitor the current datafile: our own actions will update it
        WDFstate = self._WDFstate
        if WDFstate is not None and filenum == WDFstate.filenum:
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
            warning(
                "%s: new_size:%d > DFstate.scanned_to:%d", DFstate, new_size,
                DFstate.scanned_to
            )
            offset = DFstate.scanned_to
            hashclass = self.hashclass
            scanner = DFstate.scanfrom(offset=offset)
            if run_modes.show_progress:
              scanner = progressbar(
                  scanner,
                  "%s: scan %s" %
                  (self, relpath(DFstate.filename, datadirpath)),
                  position=offset,
                  total=new_size,
                  itemlenfunc=(
                      lambda pre_dr_post: pre_dr_post[2] - pre_dr_post[0]
                  ),
                  units_scale=BINARY_BYTES_SCALE,
                  update_frequency=64,
              )
            for pre_offset, DR, post_offset in scanner:
              hashcode = hashclass.from_chunk(DR.data)
              entry = FileDataIndexEntry(
                  filenum=filenum,
                  data_offset=pre_offset + DR.data_offset,
                  data_length=DR.raw_data_length,
                  flags=DR.flags,
              )
              entry_bs = bytes(entry)
              with self._lock:
                index[hashcode] = entry_bs
              DFstate.scanned_to = post_offset
              if self.cancelled:
                break
            # update the persistent filemap state
            self._filemap.set_indexed_to(DFstate.filenum, DFstate.scanned_to)
            self.flush()
        self.flush()
      if not self.cancelled:
        sleep(0.1)

class PlatonicFile(MultiOpenMixin, HasFSPath, ReadMixin):
  ''' A PlatonicFile is a normal file whose content is used as the
      reference for block data.
  '''

  def __init__(self, fspath):
    MultiOpenMixin.__init__(self)
    HasFSPath.__init__(self, fspath)
    self._fd = None
    # dummy value since all I/O goes through datafrom, which uses pread
    self._seek_offset = 0

  @contextmanager
  def startup_shutdown(self):
    ''' Startup: open the file for read.
    '''
    with super().startup_shutdown():
      with stackattrs(
          self,
          _fd=pfx_call(os.open, self.fspath, os.O_RDONLY),
      ):
        try:
          yield
        finally:
          os.close(self._fd)

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

      A `PlatonicDir` may be used as the `Mapping` for a `MappingStore`.

      NB: _not_ thread safe; callers must arrange that.

      A `PlatonicDir` is read-only. Data blocks are fetched directly
      from the files in the backing directory tree.
  '''

  # delays during scanning to limit the CPU and I/O impact of the
  # monitoring and updating
  DELAY_INTERSCAN = 1.0  # regular pause between directory scans
  DELAY_INTRASCAN = 0.1  # stalls during scan: per directory and after big files

  def __init__(
      self,
      topdirpath,
      *,
      hashclass,
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
          representing the ideal directory,
          which is maintained as changes to the source directory tree
          are observed.
          Also, unhashed data blocks encountered during scans
          which are promoted to `HashCodeBlock`s are stored here.
        * `archive`: optional `Archive` ducktype instance with a
          `.update(Dirent[,when])` method

        Other keyword arguments are passed to `FilesDir.__init__`.

        The directory and file paths tested are relative to the
        data directory path.
    '''
    if meta_store is None:
      meta_store = Store.default()
    super().__init__(topdirpath, hashclass=hashclass, **kw)
    if exclude_dir is None:
      exclude_dir = self._default_exclude_path
    if exclude_file is None:
      exclude_file = self._default_exclude_path
    self.exclude_dir = exclude_dir
    self.exclude_file = exclude_file
    self.follow_symlinks = follow_symlinks
    self.meta_store = meta_store
    if archive is None:
      # use the default archive
      archive = self.get_Archive(missing_ok=True)
    elif archive is not None:
      if isinstance(archive, str):
        archive = Archive(archive)
    self.archive = archive
    archive = self.archive
    D = archive.last.dirent
    if D is None:
      info("%r: no archive entries, create empty topdir Dir", archive)
      with meta_store:
        D = Dir('.')
        archive.update(D)
    self.topdir = D

  @contextmanager
  def startup_shutdown(self):
    with super().startup_shutdown():
      with self.meta_store:
        try:
          yield
        finally:
          self.sync_meta()

  def sync_meta(self):
    ''' Update the Archive state.
    '''
    # update the archive, using meta_store as the default block store
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

  @pfx_method(use_str=True)
  @with_upd_proxy
  def _monitor_datafiles(self, *, upd_proxy: UpdProxy):
    ''' Thread body to poll the ideal tree for new or changed files.
    '''
    datadirpath = self.datapath
    disabled = False
    while not self.cancelled:
      sleep(self.DELAY_INTERSCAN)
      if self.flag_scan_disable:
        if not disabled:
          info("scan %r DISABLED", shortpath(datadirpath))
          disabled = True
        continue
      if disabled:
        info("scan %r ENABLED", shortpath(datadirpath))
        disabled = False
      self._scan_datatree(upd_proxy=upd_proxy)

  def _scan_datatree(self, *, upd_proxy: UpdProxy):
    topdir = self.topdir
    # scan for new datafiles
    seen = set()
    info("scan %s ... ", self.datapath)
    with upd_proxy.extend_prefix("walk "):
      updated = False
      for dirpath, dirnames, filenames in os.walk(self.datapath,
                                                  followlinks=True):
        if self.cancelled:
          break
        dirnames[:] = sorted(dirnames)
        filenames = sorted(filenames)
        sleep(self.DELAY_INTRASCAN)
        if self.cancelled or self.flag_scan_disable:
          break
        rdirpath = relpath(dirpath, self.datapath)
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
          with self.meta_store:
            D = topdir.makedirs(rdirpath, force=True)
            # prune removed names
            names = list(D.keys())
            for name in names:
              if name not in dirnames and name not in filenames:
                info("del %r", name)
                del D[name]
          if filenames:
            with (upd_proxy.extend_prefix(f'{rdirpath}/ ')
                  if filenames else nullcontext()):
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
                  upd_proxy.text = f'scan {filename!r}'
                  try:
                    updated |= self._scan_datafile(
                        D, filename, rfilepath, upd_proxy=upd_proxy
                    )
                  except Exception as e:
                    warning(
                        "exception scanning %s: %s",
                        shortpath(joinpath(dirpath, filename)), e
                    )
        # update the archive after updating from a directory
        if updated:
          self.sync_meta()
          updated = False
    self.flush()

  def _scan_datafile(self, D, filename, rfilepath, *, upd_proxy: UpdProxy):
    ''' Scan the data file at `data/{rfilepath}`, record as `D[filename]`.
        Return a Boolean indicating whether `D` was updated.
    '''
    updated = False
    index = self.index
    filemap = self._filemap
    # look up this file in our file state index
    DFstate = filemap.get(rfilepath)
    if (DFstate is not None and D is not None and filename not in D):
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
      return
    if new_size is None:
      # skip non files
      debug("SKIP non-file")
      return updated
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
      blockQ = IterableQueue()
      current_block = E.block
      assert len(current_block) == DFstate.scanned_to, (
          "DFstate.scanned_to:%s != len(E.block):%s" %
          (DFstate.scanned_to, len(current_block))
      )
      # splice the newly scanned data into the existing data
      top_block_result = self.meta_store._defer(
          lambda B, Q: top_block_for(spliced_blocks(B, Q)), current_block,
          blockQ
      )
      scan_from = DFstate.scanned_to
      scan_start = time()
      scanner = DFstate.scanfrom(offset=DFstate.scanned_to)
      if 1:
        scanner = progressbar(
            scanner,
            "scan " + rfilepath[:max(16, upd_proxy.width - 60)],
            position=DFstate.scanned_to,
            total=new_size,
            itemlenfunc=lambda t3: t3[2] - t3[0],
            update_frequency=256,
            proxy=upd_proxy,
            units_scale=BINARY_BYTES_SCALE,
            report_print=True,
        )
      for pre_offset, data, post_offset in scanner:
        hashcode = self.hashclass.from_chunk(data)
        entry = FileDataIndexEntry(
            filenum=DFstate.filenum,
            data_offset=pre_offset,
            data_length=len(data),
            flags=0,
        )
        entry_bs = bytes(entry)
        with self._lock:
          index[hashcode] = entry_bs
        B = HashCodeBlock(data=data, hashcode=hashcode, added=True)
        blockQ.put((pre_offset, B))
        DFstate.scanned_to = post_offset
        if self.cancelled or self.flag_scan_disable:
          break
      # now collect the top block of the spliced data
      blockQ.close()
      try:
        top_block = top_block_result()
      except MissingHashcodeError as e:
        error("missing data, forcing rescan: %s", e)
        DFstate.scanned_to = 0
      else:
        E.block = top_block
        D.changed = True
        updated = True
      elapsed = time() - scan_start
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
            transcribe_bytes_geek(scanned), transcribe_bytes_geek(scan_rate)
        )
      # stall after a file scan, briefly, to limit impact
      if elapsed > 0 and not self.cancelled:
        sleep(min(elapsed, self.DELAY_INTRASCAN))
    return updated

  @staticmethod
  def scanfrom(filepath, offset=0):
    ''' Scan the specified `filepath` from `offset`,
        yielding data `(pre_offset, data, post_offset)`.
    '''
    scanner = scanner_from_filename(filepath)
    with open(filepath, 'rb') as fp:
      fp.seek(offset)
      for data in blocked_chunks_of(read_from(fp, DEFAULT_SCAN_SIZE),
                                    scanner=scanner):
        post_offset = offset + len(data)
        yield offset, data, post_offset
        offset = post_offset

class DataDirCommand(BaseCommand):
  ''' Command line implementation for `DataDir`s.
  '''

  GETOPT_SPEC = 'd:'

  USAGE_FORMAT = '''Usage: {cmd} [-d datadir] subcommand [...]
  -d datadir    Specify the filesystem path of the DataDir.
                Default from the default Store, which must be a DataDirStore.
  '''

  SUBCOMMAND_ARGV_DEFAULT = ['info']

  @dataclass
  class Options(BaseCommand.Options):
    ''' Special class for `self.options` with various properties.
    '''
    datadirpath: Optional[str] = None
    datadir: Optional[DataDir] = None
    store_spec: Optional[str] = None

  def apply_opt(self, opt, val):
    ''' Apply the command line option `opt` with value `val`.
    '''
    options = self.options
    if opt == '-d':
      options.datadirpath = val
    else:
      raise GetoptError(f'unhandled option: {opt!r}={val!r}')

  @contextmanager
  def run_context(self):
    options = self.options
    datadirpath = options.datadirpath
    if datadirpath is None:
      from .store import DataDirStore  # pylint: disable=import-outside-toplevel
      S = pfx_call(Store.promote, options.store_spec, options.config)
      if not isinstance(S, DataDirStore):
        raise GetoptError("default Store is not a DataDirStore: %s" % (s(S),))
      datadir = S._datadir
      datadirpath = datadir.fspath
    else:
      datadir = DataDir(datadirpath)
    with super().run_context():
      with stackattrs(
          options,
          datadir=datadir,
          datadirpath=datadirpath,
      ):
        yield

  def cmd_info(self, argv):
    ''' Usage: {cmd}
          Print information about the DataDir.
    '''
    if argv:
      raise GetoptError(f'extra arguments: {argv!r}')
    options = self.options
    datadir = options.datadir
    verbose = options.verbose
    print(datadir)
    print("  hashclass: ", datadir.hashclass.__name__)
    print("  indexclass:", datadir.indexclass.__name__)
    if verbose:
      print("  datafiles:")
    total_data = 0
    datafilenames = datadir.datafilenames()
    for filename in sorted(datafilenames):
      with Pfx(filename):
        datapath = datadir.datapathto(filename)
        S = os.stat(datapath)
        if verbose:
          print("   ", filename, transcribe_bytes_geek(S.st_size))
        total_data += S.st_size
    if verbose:
      print("   ", transcribe_bytes_geek(total_data))
    else:
      print(
          "  datafiles:",
          len(datafilenames),
          "files, ",
          transcribe_bytes_geek(total_data),
          "total bytes",
      )

  def cmd_init(self, argv):
    ''' Usage: {cmd}
          Initialise the DataDir.
    '''
    if argv:
      raise GetoptError(f'extra arguments: {argv!r}')
    self.options.datadir.initdir()

  def cmd_test(self, argv):
    ''' Usage: {cmd} [selftest-args...]
          Run the DataDir unit tests.
    '''
    from .datadir_tests import selftest  # pylint: disable=import-outside-toplevel
    selftest([self.options.cmd] + argv)

if __name__ == '__main__':
  sys.exit(main(sys.argv))
