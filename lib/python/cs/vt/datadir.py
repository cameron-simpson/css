#!/usr/bin/python -tt
#
# Data stores based on local files.
#
# DataDir: the sharable directory storing DataFiles used by DataDirStores.
# - Cameron Simpson <cs@cskk.id.au>
#

from binascii import hexlify
from collections import namedtuple
from collections.abc import Mapping
import csv
import errno
import os
from os.path import basename, join as joinpath, samefile, exists as existspath, isdir as isdirpath, relpath, isabs as isabspath
import stat
import sys
from threading import RLock
import time
from types import SimpleNamespace
from uuid import uuid4
from cs.app.flag import DummyFlags, FlaggedMixin
from cs.cache import LRU_Cache
from cs.csvutils import csv_reader
from cs.excutils import logexc
from cs.fileutils import makelockfile, shortpath, longpath, read_from
from cs.logutils import debug, info, warning, error, exception
from cs.pfx import Pfx, XP, PfxThread as Thread
from cs.py.func import prop
from cs.queues import IterableQueue
from cs.resources import MultiOpenMixin, RunStateMixin
from cs.seq import imerge
from cs.serialise import get_bs, put_bs
from cs.threads import locked
from cs.units import transcribe_bytes_geek
from . import MAX_FILE_SIZE
from .archive import Archive
from .block import Block
from .blockify import top_block_for, blocked_chunks_of, spliced_blocks, DEFAULT_SCAN_SIZE
from .datafile import DataFile, scan_datafile, DATAFILE_DOT_EXT
from .dir import Dir, FileDirent
from .hash import DEFAULT_HASHCLASS, HASHCLASS_BY_NAME, HashCodeUtilsMixin
from .index import choose as choose_indexclass, class_by_name as indexclass_by_name
from .parsers import scanner_from_filename
from .paths import decode_Dirent_text

# 1GiB rollover
DEFAULT_ROLLOVER = MAX_FILE_SIZE

# flush the index after this many updates in the index updater worker thread
INDEX_FLUSH_RATE = 16384

def DataDir_from_spec(spec, indexclass=None, hashclass=None, rollover=None):
  ''' Accept `spec` of the form:
        [indextype:[hashname:]]/indexdir[:/dirpath][:rollover=n]
      and return a DataDir.
  '''
  global HASHCLASS_BY_NAME, DEFAULT_HASHCLASS
  indexdirpath = None
  datadirpath = None
  with Pfx(spec):
    specpath = spec.split(os.pathsep)
    for partnum, specpart in enumerate(specpath, 1):
      with Pfx("%d:%r", partnum, specpart):
        if indexclass is None:
          try:
            indexclass = indexclass_by_name(specpart)
          except KeyError:
            pass
          else:
            continue
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
  if hashclass is None:
    hashclass = DEFAULT_HASHCLASS
  return DataDir(indexdirpath, datadirpath, hashclass, indexclass=indexclass, rollover=rollover)

class FileState(SimpleNamespace):
  ''' General state information about a data file in use by a files based data dir.
      Attributes:
      `datadir`: the _FilesDir tracking this state
      `filename`: out path relative to the _FilesDir's data directory
      `indexed_to`: the maximum amount of data scanned and indexed so far
  '''

  def __init__(self, datadir, filenum, filename, indexed_to=0, scanned_to=None) -> None:
    if scanned_to is None:
      scanned_to = indexed_to
    self.datadir = datadir
    self.filenum = filenum
    self.filename = filename
    self.indexed_to = indexed_to
    self.scanned_to = scanned_to

  @classmethod
  def from_csvrow(cls, datadir, filenum, filename, indexed_to, *etc):
    if etc:
      raise ValueError("%s.from_csvrow: extra arguments after indexed_to: %r" % (cls, etc))
    return cls(
        datadir,
        filenum,
        filename,
        indexed_to=indexed_to)

  def csvrow(self):
    ''' Return a list of CSV row values to follow `n` and `filename`.
    '''
    return [ self.indexed_to ]

  @property
  def pathname(self):
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

  def scan(self, offset=0, **kw):
    ''' Scan this datafile from the supplied `offset` (default 0) yielding (offset, flags, data, post_offset).
        We use the DataDir's .scan method because it knows the format of the file.
    '''
    yield from self.datadir.scan(self.pathname, offset=offset, **kw)

class _FilesDir(HashCodeUtilsMixin, MultiOpenMixin, RunStateMixin, FlaggedMixin, Mapping):
  ''' Base class for locally stored data in files.

      There are two main subclasses of this at present:

      DataDir, where the data are kept in a subdirectory of UUID-named
      files, supporting easy merging and updating.

      PlatonicDataDir, where the data are present in a normal file tree,
      such as a preexisting media server directory or the like.
  '''

  STATE_FILENAME_FORMAT = 'index-{hashname}-state.csv'
  INDEX_FILENAME_BASE_FORMAT = 'index-{hashname}'

  def __init__(self,
      statedirpath, datadirpath, hashclass, indexclass=None,
      create_statedir=None, create_datadir=None,
      flags=None, flag_prefix=None):
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
            in the DataFiles. If not specified, a supported index
            class with an existing index file will be chosen, otherwise
            the most favoured indexclass available will be chosen.
        `hashclass`: the hash class used to index chunk contents.
        `create_statedir`: os.mkdir the state directory if missing
        `create_datadir`: os.mkdir the data directory if missing
        `flags`: optional Flags object for control; if specified
            then `flag_prefix` is also required
        `flag_prefix`: prefix for control flag names
    '''
    MultiOpenMixin.__init__(self, lock=RLock())
    RunStateMixin.__init__(self)
    if flags is None:
      if flag_prefix is None:
        flags = DummyFlags()
        flag_prefix = 'DUMMY'
    else:
      if flag_prefix is None:
        raise ValueError("flags provided but no flag_prefix")
    FlaggedMixin.__init__(self, flags=flags, prefix=flag_prefix)
    self.statedirpath = statedirpath
    if hashclass is None:
      hashclass = DEFAULT_HASHCLASS
    self.hashclass = hashclass
    if indexclass is None:
      indexclass = self._indexclass()
    self.indexclass = indexclass
    if create_statedir is None:
      create_statedir = False
    if not isdirpath(statedirpath):
      if create_statedir:
        with Pfx("mkdir(%r)", statedirpath):
          os.mkdir(statedirpath)
      else:
        raise ValueError("missing statedirpath directory: %r" % (statedirpath,))
    self._filemap = {}
    self._extra_state = {}
    self._load_state()
    if datadirpath is not None:
      self.datadirpath = datadirpath
    else:
      datadirpath = self.datadirpath
    # the "default" data dir may be created if the statedir exists
    if create_datadir is None:
      create_datadir = existspath(statedirpath) and not existspath(datadirpath)
    if not isdirpath(datadirpath):
      if create_datadir:
        with Pfx("mkdir(%r)", datadirpath):
          os.mkdir(datadirpath)
      else:
        raise ValueError("missing datadirpath directory: %r" % (datadirpath,))

  def __str__(self):
    return '%s(%s)' % (self.__class__.__name__, shortpath(self.statedirpath))

  def __repr__(self):
    return ( '%s(statedirpath=%r,datadirpath=%r,hashclass=%s,indexclass=%s)'
             % (self.__class__.__name__,
                self.statedirpath,
                self.datadirpath,
                self.hashclass.HASHNAME,
                self.indexclass)
           )

  def _indexclass(self, preferred_indexclass=None):
    return choose_indexclass(self.indexbase, preferred_indexclass=preferred_indexclass)

  def spec(self):
    ''' Return a datadir_spec for this DataDirMapping.
    '''
    return ':'.join( (self.indexclass.NAME,
                      self.hashclass.HASHNAME,
                      str(self.statedirpath),
                      str(self.datadirpath)) )

  def startup(self):
    self.runstate.start()
    # cache of open DataFiles
    self._cache = LRU_Cache(maxsize=4,
                            on_remove=lambda k, datafile: datafile.close())
    # obtain lock
    self.lockpath = makelockfile(self.statefilepath)
    # open dbm index
    self.index = self.indexclass(self.indexbasepath, self.hashclass, self.index_entry_class.from_bytes, lock=self._lock)
    self.index.open()
    # set up indexing thread
    # map individual hashcodes to locations before being persistently stored
    # This lets us add data, stash the location in _unindexed and
    # drop the location onto the _indexQ for persistent storage in
    # the index asynchronously.
    self._unindexed = {}
    self._indexQ = IterableQueue(64)
    T = self._index_Thread = Thread(name="%s-index-thread" % (self,),
                                    target=self._index_updater)
    T.start()
    T = self._monitor_Thread = Thread(name="%s-datafile-monitor" % (self,),
                                      target=self._monitor_datafiles)
    T.start()

  def shutdown(self):
    self.runstate.cancel()
    # shut down the monitor Thread
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
    try:
      os.remove(self.lockpath)
    except OSError as e:
      error("cannot remove lock file: %s", e)
    del self.lockpath

  def localpathto(self, rpath):
    ''' Return the path to `rpath`, which is relative to the statedirpath.
    '''
    return joinpath(self.statedirpath, rpath)

  def datapathto(self, rpath):
    ''' Return the path to `rpath`, which is relative to the datadirpath.
    '''
    return joinpath(self.datadirpath, rpath)

  def state_localpath(self, hashclass):
    return self.STATE_FILENAME_FORMAT.format(hashname=hashclass.HASHNAME)

  @property
  def statefilepath(self):
    return self.localpathto(self.state_localpath(self.hashclass))

  @property
  def indexbase(self):
    ''' Basename of the index.
    '''
    return self.INDEX_FILENAME_BASE_FORMAT.format(hashname=self.hashclass.HASHNAME)

  @property
  def indexbasepath(self):
    ''' Pathname of the index.
    '''
    return self.localpathto(self.indexbase)

  def _load_state(self):
    ''' Read STATE_FILENAME.
    '''
    statefilepath = self.statefilepath
    ##if existspath(statefilepath):
    ##  os.system('sed "s/^/IN  /" %r' % (statefilepath,))
    with Pfx('_load_state(%r)', shortpath(statefilepath)):
      if existspath(statefilepath):
        with open(statefilepath, 'r') as fp:
          for lineno, row in enumerate(csv_reader(fp), 1):
            with Pfx("%d", lineno):
              col1 = row[0]
              with Pfx(col1):
                try:
                  filenum = int(col1)
                except ValueError:
                  _, col2 = row
                  with Pfx("%s=%r", col1, col2):
                    self.set_state(col1, col2)
                else:
                  # filenum, filename, indexed_to
                  _, filename, indexed_to, *etc = row
                  try:
                    indexed_to = int(indexed_to)
                  except ValueError as e:
                    error("discrading record: invalid indexed_to (column 3), expected int: %s: %r",
                          e, indexed_to)
                    continue
                  filestate = FileState.from_csvrow(self, filenum, filename, indexed_to, *etc)
                  filestate.filenum = filenum
                  self._add_datafilestate(filestate)

  def _save_state(self):
    ''' Rewrite STATE_FILENAME.
    '''
    # update the topdir state before any save
    statefilepath = self.statefilepath
    X("SAVE STATE ==> %r", statefilepath)
    with Pfx("_save_state(%r)", statefilepath):
      with self._lock:
        with open(statefilepath, 'w') as fp:
          csvw = csv.writer(fp)
          csvw.writerow( ('datadir', shortpath(self.datadirpath)) )
          if self.current_save_filenum is not None:
            csvw.writerow( ('current', self.current_save_filenum) )
          extras = self._extra_state
          for k in sorted(extras.keys()):
            csvw.writerow( (k, extras[k]) )
          filemap = self._filemap
          for n in sorted(filter(lambda n: isinstance(n, int), filemap.keys())):
            F = filemap[n]
            if F is not None:
              csvw.writerow( [n, F.filename] + F.csvrow() )
      ##os.system('sed "s/^/OUT /" %r' % (statefilepath,))
    X("SAVE DONE")

  def set_state(self, key, value):
    if not key.islower():
      raise ValueError("invalid state key, short be lower case: %r" % (key,))
    if value is None:
      if key in self._extra_state:
        del self._extra_state[key]
    else:
      self._extra_state[key] = value

  @prop
  def datadirpath(self):
    path = longpath(self._extra_state.get('datadir', 'data'))
    if not isabspath(path):
      path = joinpath(self.statedirpath, path)
    return path

  @datadirpath.setter
  def datadirpath(self, newpath):
    if isabspath(newpath) and newpath.startswith(self.statedirpath + '/'):
      newpath = relpath(newpath, self.statedirpath)
    self.set_state('datadir', shortpath(newpath))

  @prop
  def current_save_filenum(self):
    n = self._extra_state.get('current')
    if n is not None:
      n = int(n)
    return n

  @current_save_filenum.setter
  def current_save_filenum(self, new_filenum):
    self.set_state('current', new_filenum)

  def _add_datafile(self, filename):
    ''' Add the specified data file named `filename` to the filemap, returning the filenum.
        `filename`: the filename relative to the data directory
    '''
    F = FileState(self, None, filename, indexed_to=0)
    return self._add_datafilestate(F)

  def _add_datafilestate(self, F):
    ''' Add the supplied data file state `F` to the filemap, returning the filenum.
    '''
    ##info("%s._add_datafilestate(F=%s)", self, F)
    filenum = F.filenum
    filemap = self._filemap
    filename = F.filename
    if filename in filemap:
      raise KeyError('FileState:%s: already in filemap: %r' % (F, filename,))
    with self._lock:
      if filenum is None:
        # TODO: keep the max floating around and make this O(1)
        filenum = max([0] + list(k for k in filemap if isinstance(k, int))) + 1
        F.filenum = filenum
      elif filenum in filemap:
        raise KeyError('filenum %d already in filemap: %s' % (filenum, filemap[filenum]))
      filemap[filenum] = F
      filemap[filename] = F
    return filenum

  def _del_datafilestate(self, F):
    ''' Delete references to the specified FileState, leaving a None placeholder behind.
    '''
    filename = F.filename
    filenum = F.filenum
    filemap = self.filemap
    with self._lock:
      assert filemap[filename] is F
      filemap[filename] = None
      assert filemap[filenum] is F
      filemap[filenum] = None

  @locked
  def _get_current_save_datafile(self):
    ''' Return the number and DataFile of the current datafile,
        opening one if necessary.
    '''
    n = self.current_save_filenum
    if n is None:
      n = self._new_datafile()
      self.current_save_filenum = n
    D = self._open_datafile(n)
    return n, D

  def _queue_index(self, hashcode, entry, post_offset):
    if not isinstance(entry, self.index_entry_class):
      raise RuntimeError("expected %s but got %s %r" % (entry_class, type(entry), entry))
    with self._lock:
      self._unindexed[hashcode] = entry
    self._indexQ.put( (hashcode, entry, post_offset) )

  @logexc
  def _index_updater(self):
    ''' Thread body to collect hashcode index data from .indexQ and store it.
    '''
    with Pfx("%s._index_updater", self):
      index = self.index
      entry_class = self.index_entry_class
      flush_rate = INDEX_FLUSH_RATE
      unindexed = self._unindexed
      filemap = self._filemap
      oldF = None
      nsaves = 0
      need_sync = False
      indexQ = self._indexQ
      for hashcode, entry, post_offset in indexQ:
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
        nsaves += 1
        if nsaves >= flush_rate:
          need_sync = True
        F = filemap[entry.n]
        if post_offset <= F.indexed_to:
          error("%r: indexed_to already %s but post_offset=%s",
              F.filename, F.indexed_to, post_offset)
        F.indexed_to = max(F.indexed_to, post_offset)
        if F is not oldF:
          XP("switch to %r: %r", F.filename, F.pathname)
          if oldF is not None:
            XP("previous: %r indexed_to=%s", oldF.filename, oldF.indexed_to)
          oldF = F
          need_sync = True
        if need_sync and indexQ.empty():
          X("INDEX UPDATER SYNC...")
          index.flush()
          self._save_state()
          need_sync = False
          nsaves = 0
          X("INDEX UPDATER SYNC DONE")
      index.flush()
      self._save_state()

  @locked
  def flush(self):
    self._cache.flush()
    self.index.flush()
    self._save_state()

  def __setitem__(self, hashcode, data):
    h = self.add(data)
    if hashcode != h:
      raise ValueError('hashcode %s does not match data, data added under %s instead'
                       % (hashcode, h))

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
      entry = unindexed[hashcode]
    except KeyError:
      index = self.index
      try:
        with self._lock:
          entry = index[hashcode]
      except KeyError:
        ##info("%s[%s]: hash not in index", self, hashcode)
        raise
    try:
      return self.fetch(entry)
    except Exception as e:
      exception("%s[%s]:%s not available: %s", self, hashcode, entry, e)
      raise KeyError(str(hashcode))

class DataDirIndexEntry(namedtuple('DataDirIndexEntry', 'n offset')):
  ''' A block record for a DataDir.
  '''

  @classmethod
  def from_bytes(cls, data:bytes):
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

  def __init__(self,
      statedirpath, datadirpath, hashclass, *,
      rollover=None,
      **kw):
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
            in the DataFiles. If not specified, a supported index
            class with an existing index file will be chosen, otherwise
            the most favoured indexclass available will be chosen.
        `rollover`: data file roll over size; if a data file grows
            beyond this a new datafile is commenced for new blocks.
            Default: DEFAULT_ROLLOVER
        `create_statedir`: os.mkdir the state directory if missing
        `create_datadir`: os.mkdir the data directory if missing
    '''
    super().__init__(statedirpath, datadirpath, hashclass, **kw)
    if rollover is None:
      rollover = DEFAULT_ROLLOVER
    elif rollover < 1024:
      raise ValueError("rollover < 1024 (a more normal size would be in megabytes or gigabytes): %r" % (rollover,))
    self.rollover = rollover

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

  def _open_datafile(self, n):
    ''' Return the DataFile with index `n`.
    '''
    cache = self._cache
    D = cache.get(n)
    if D is None:
      with self._lock:
        # first, look again now that we have the _lock
        D = cache.get(n)
        if D is None:
          # still not in the cache, open the DataFile and put into the cache
          F = self._filemap[n]
          readwrite = (n == self.current_save_filenum)
          D = cache[n] = DataFile(self.datapathto(F.filename), readwrite=readwrite)
          D.open()
    return D

  def fetch(self, entry):
    ''' Return the data chunk stored in DataFile `n` at `offset`.
    '''
    D = self._open_datafile(entry.n)
    return D.fetch(entry.offset)

  def _monitor_datafiles(self):
    ''' Thread body to poll all the datafiles regularly for new data arrival.
        This is what supports shared use of the data area. Other clients
        may write to their onw datafiles and this thread sees new files
        and new data in existing files and scans it, adding the index
        information to the local state.
    '''
    filemap = self._filemap
    indexQ = self._indexQ
    while not self.cancelled:
      if self.flag_scan_disable:
        time.sleep(1)
        continue
      # scan for new datafiles
      need_save = False
      with Pfx("listdir(%r)", self.datadirpath):
        try:
          listing = list(os.listdir(self.datadirpath))
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
            self._add_datafile(filename)
            need_save = True
      if need_save:
        self._save_state()
      # now scan known datafiles for new data
      for filenum in filter(lambda n: isinstance(n, int), filemap.keys()):
        if self.cancelled or self.flag_scan_disable:
          break
        # don't monitor the current datafile: our own actions will update it
        n = self.current_save_filenum
        if n is not None and filenum == n:
          # ignore the current save file
          continue
        try:
          F = filemap[filenum]
        except KeyError:
          warning("missing entry %d in filemap", filenum)
          continue
        with Pfx(F.filename):
          try:
            new_size = F.stat_size()
          except OSError as e:
            warning("stat: %s", e)
            continue
          if new_size > F.scanned_to:
            need_save = False
            for offset, flags, data, post_offset in F.scan(offset=F.scanned_to):
              hashcode = self.hashclass.from_chunk(data)
              indexQ.put( (hashcode, DataDirIndexEntry(filenum, offset), post_offset) )
              F.scanned_to = post_offset
              need_save = True
              if self.cancelled:
                break
            # update state after completion of a scan
            if need_save:
              self._save_state()
              need_save = False
      time.sleep(1)

  def add(self, data):
    ''' Add the supplied data chunk to the current DataFile, return the hashcode.
        Roll the internal state over to a new file if the current
        datafile has reached the rollover threshold.
    '''
    # save the data in the current datafile, record the file number and offset
    with self._lock:
      n, D = self._get_current_save_datafile()
      with D:
        offset, post_offset = D.add(data)
    hashcode = self.hashclass.from_chunk(data)
    self._queue_index(hashcode, DataDirIndexEntry(n, offset), post_offset)
    rollover = self.rollover
    with self._lock:
      if rollover is not None and post_offset >= rollover:
        self.current_save_filenum = None
    return hashcode

  @staticmethod
  def scan(filepath, offset=0):
    ''' Scan the specified `filepath` from `offset`, yielding data chunks.
    '''
    return scan_datafile(filepath, offset)

class PlatonicDirIndexEntry(namedtuple('PlatonicDirIndexEntry', 'n offset length')):
  ''' A block record for a PlatonicDir.
  '''

  @classmethod
  def from_bytes(cls, data:bytes):
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

class PlatonicFile(MultiOpenMixin):

  def __init__(self, path):
    MultiOpenMixin.__init__(self)
    self.path = path

  def __str__(self):
    return "PlatonicFile(%s)" % (shortpath(self.path,))

  def startup(self):
    self._fp = open(self.path, 'rb')

  def shutdown(self):
    self._fp.close()
    del self._fp

  def fetch(self, offset, length):
    fp = self._fp
    with self._lock:
      fp.seek(offset)
      data = fp.read(length)
    if len(data) != length:
      raise RuntimeError(
          "%r: asked for %d bytes from offset %d, but got %d"
          % (self.path, length, offset, len(data)))
    return data

class PlatonicDir(_FilesDir):
  ''' Presentation of a block map based on a raw directory tree of files such a preexisting media server.
      A PlatonicDir may be used as the Mapping for a MappingStore.
      NB: _not_ thread safe; callers must arrange that.
      A PlatonicDir is read-only. Data blocks are fetched directly
      from the files in the backing directory tree.
  '''

  index_entry_class = PlatonicDirIndexEntry

  def __init__(self,
      statedirpath, datadirpath, hashclass,
      create_datadir=False,
      exclude_dir=None, exclude_file=None,
      follow_symlinks=False, archive=None, meta_store=None,
      **kw):
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
        `exclude_dir`: optional function to test a directory path for
          exclusion from monitoring; default is to exclude directories
          whose basename commences with a dot.
        `exclude_file`: optional function to test a file path for
          exclusion from monitoring; default is to exclude directories
          whose basename commences with a dot.
        `follow_symlinks`: follow symbolic links, default False.
        `meta_store`: an optional Store used to maintain a Dir
          representing the ideal directory
        `archive`: optional Archive ducktype with a .save(Dirent[,when]) method
        Other keyword arguments are passed to _FilesDir.__init__.
        The directory and file paths tested are relative to the
        data directory path.
    '''
    super().__init__(statedirpath, datadirpath, hashclass, create_datadir=False, **kw)
    if exclude_dir is None:
      exclude_dir = self._default_exclude_path
    if exclude_file is None:
      exclude_file = self._default_exclude_path
    self.exclude_dir = exclude_dir
    self.exclude_file = exclude_file
    self.follow_symlinks = follow_symlinks
    self.meta_store = meta_store
    if meta_store is not None and archive is None:
      archive = joinpath(statedirpath, 'data.vt')
    if archive is not None:
      if isinstance(archive, str):
        archive = Archive(archive)
    self.archive = archive

  def startup(self):
    if self.meta_store is not None:
      self.meta_store.open()
      archive = self.archive
      when, D = archive.last
      if D is None:
        info("%r: no entries in %s, create empty topdir Dir", self.datadirpath, archive)
        D = Dir('.')
        archive.save(D)
      self.topdir = D
    super().startup()

  def shutdown(self):
    super().shutdown()
    if self.meta_store is not None:
      self.meta_store.close()

  def _save_state(self):
    ''' Rewrite STATE_FILENAME.
    '''
    # update the topdir state before any save
    if self.meta_store is not None:
      with self.meta_store:
        self.archive.save(self.topdir)
    return _FilesDir._save_state(self)

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
    D = cache.get(n)
    if D is None:
      with self._lock:
        # first, look again now that we have the _lock
        D = cache.get(n)
        if D is None:
          # still not in the cache, open the DataFile and put into the cache
          F = self._filemap[n]
          D = cache[n] = PlatonicFile(self.datapathto(F.filename))
          D.open()
    return D

  def fetch(self, entry):
    ''' Return the data chunk stored in DataFile `n` at `offset`.
    '''
    D = self._open_datafile(entry.n)
    return D.fetch(entry.offset, entry.length)

  @logexc
  def _monitor_datafiles(self):
    ''' Thread body to poll the ideal tree for new or changed files.
    '''
    meta_store = self.meta_store
    filemap = self._filemap
    indexQ = self._indexQ
    if meta_store is not None:
      topdir = self.topdir
    while not self.cancelled:
      if self.flag_scan_disable:
        time.sleep(1)
        continue
      # scan for new datafiles
      need_save = False
      datadirpath = self.datadirpath
      with Pfx("walk(%r)", datadirpath):
        seen = set()
        X("NEW WALK %r", datadirpath)
        for dirpath, dirnames, filenames in os.walk(datadirpath, followlinks=True):
          X("WALK: dirpath=%r", dirpath)
          if self.cancelled or self.flag_scan_disable:
            break
          rdirpath = relpath(dirpath, datadirpath)
          with Pfx(rdirpath):
            pruned_dirnames = []
            for dname in dirnames:
              if self.exclude_dir(joinpath(rdirpath, dname)):
                continue
              subdirpath = joinpath(dirpath, dname)
              try:
                S = os.stat(subdirpath)
              except OSError as e:
                warning("stat(%r): %s, skipping", subdirpath, e)
                continue
              ino = S.st_dev, S.st_ino
              if ino in seen:
                warning("seen %r (dev=%s,ino=%s), skipping", subdirpath, ino[0], ino[1])
                continue
              seen.add(ino)
              pruned_dirnames.append(dname)
            dirnames[:] = pruned_dirnames
            if meta_store is not None:
              with meta_store:
                D = topdir.makedirs(rdirpath, force=True)
                # prune removed names
                names = list(D.keys())
                for name in names:
                  if name not in dirnames and name not in filenames:
                    info("del %r", name)
                    del D[name]
            for filename in filenames:
              X("WALK: dirpath=%r, filename=%r", dirpath, filename)
              with Pfx(filename):
                if self.cancelled or self.flag_scan_disable:
                  break
                rfilepath = joinpath(rdirpath, filename)
                with Pfx(filename):
                  if self.exclude_file(rfilepath):
                    continue
                  try:
                    F = filemap[rfilepath]
                  except KeyError:
                    X("F: add datafile %r", rfilepath)
                    filenum = self._add_datafile(rfilepath)
                    F = filemap[filenum]
                    need_save = True
                  else:
                    filenum = F.filenum
                  try:
                    new_size = F.stat_size(self.follow_symlinks)
                  except OSError as e:
                    if e.errno == errno.ENOENT:
                      warning("forgetting missing file")
                      self._del_datafilestate(F)
                      need_save = True
                    else:
                      warning("stat: %s", e)
                    continue
                  if new_size is None:
                    # skip non files
                    debug("SKIP non-file")
                    continue
                  if meta_store is not None:
                    try:
                      E = D[filename]
                    except KeyError:
                      info("new FileDirent")
                      E = FileDirent(filename)
                      D[filename] = E
                    else:
                      if not E.isfile:
                        info("new FileDirent replacing previous nonfile")
                        E = D[E] = FileDirent(filename)
                  if new_size > F.scanned_to:
                    info("scan from %d", F.scanned_to)
                    if meta_store is not None:
                      blockQ = IterableQueue()
                      R = meta_store.bg(
                          lambda B, Q: top_block_for(spliced_blocks(B, Q)),
                          E.block, blockQ)
                    scan_from = F.scanned_to
                    scan_start = time.time()
                    for offset, flags, data, post_offset in F.scan(offset=F.scanned_to):
                      hashcode = self.hashclass.from_chunk(data)
                      indexQ.put( (hashcode, PlatonicDirIndexEntry(filenum, offset, len(data)), post_offset) )
                      if meta_store is not None:
                        B = Block(data=data, hashcode=hashcode, added=True)
                        blockQ.put( (offset, B) )
                      F.scanned_to = post_offset
                      need_save = True
                      if self.cancelled or self.flag_scan_disable:
                        break
                    elapsed = time.time() - scan_start
                    scanned = F.scanned_to - scan_from
                    if elapsed > 0:
                      scan_rate = scanned / elapsed
                    else:
                      scan_rate = None
                    if scan_rate is None:
                      info("scanned to %d: %s", F.scanned_to, transcribe_bytes_geek(scanned))
                    else:
                      info("scanned to %d: %s at %s/s", F.scanned_to, transcribe_bytes_geek(scanned), transcribe_bytes_geek(scan_rate))
                    if meta_store is not None:
                      blockQ.close()
                      top_block = R()
                      E.block = top_block
                      D.changed = True
                      need_save = True
                    # update state after completion of a scan
                    if need_save:
                      need_save = False
                      self._save_state()
      if need_save:
        need_save = False
        self._save_state()
      X("SLEEP 1 AFTER WALK")
      time.sleep(11)

  @staticmethod
  def scan(filepath, offset=0):
    ''' Scan the specified `filepath` from `offset`, yielding data chunks.
    '''
    global DEFAULT_SCAN_SIZE
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
