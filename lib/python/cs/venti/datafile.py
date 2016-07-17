#!/usr/bin/python -tt
#
# The basic data store for venti blocks.
#       - Cameron Simpson <cs@zip.com.au>
#

import sys
from collections import namedtuple
from collections.abc import Mapping
import os
from os import SEEK_SET, SEEK_END
from os.path import join as joinpath, samefile, exists as existspath
import errno
import csv
from subprocess import Popen, PIPE
from threading import Lock, RLock, Thread
from types import SimpleNamespace
from uuid import uuid4
from zlib import compress, decompress
from cs.cache import LRU_Cache
from cs.csvutils import csv_reader, csv_writerow
from cs.excutils import LogExceptions
from cs.fileutils import makelockfile, shortpath, longpath
from cs.logutils import D, X, XP, debug, warning, error, exception, Pfx
from cs.obj import O
from cs.queues import IterableQueue
from cs.resources import MultiOpenMixin
from cs.seq import imerge
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

def read_chunk(fp, do_decompress=False):
  ''' Read a data chunk from a file at its current offset. Return (flags, chunk, post_offset).
      If do_decompress is true and flags&F_COMPRESSED, strip that
      flag and decompress the data before return.
      Raises EOFError on premature end of file.
  '''
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
  ''' Write a data chunk to a file at the current position, return the starting and ending offsets.
      If not no_compress, try to compress the chunk.
      Note: does _not_ call .flush().
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
  return offset, fp.tell()

class DataFile(MultiOpenMixin):
  ''' A cs.venti data file, storing data chunks in compressed form.
      This is the usual file based persistence layer of a local venti Store.

      A DataFile is a MultiOpenMixin and supports:
        .flush()        Flush any pending output to the file.
        .fetch(offset)  Fetch the data chunk from `offset`.
        .add(data)      Store data chunk, return (offset, offset2) indicating its location.
        .scan([do_decompress=],[offset=0])
                        Scan the data file and yield (offset, flags, zdata, offset2) tuples.
                        This can take place during other activity.
  '''

  def __init__(self, pathname, do_create=False, readwrite=False, lock=None):
    MultiOpenMixin.__init__(self, lock=lock)
    self.pathname = pathname
    self.readwrite = readwrite
    if do_create and not readwrite:
      raise ValueError("do_create=true requires readwrite=true")
    self.appending = False
    if do_create:
      fd = os.open(pathname, os.O_CREAT|os.O_EXCL|os.O_RDWR)
      os.close(fd)

  def __str__(self):
    return "DataFile(%s)" % (self.pathname,)

  def startup(self):
    self.fp = open(self.pathname, ( "a+b" if self.readwrite else "r+b" ))

  def shutdown(self):
    self.flush()
    self.fp.close()
    self.fp = None

  def flush(self):
    with self._lock:
      if self.appending:
        self.fp.flush()

  def scan(self, do_decompress=False, offset=None):
    ''' Scan the data file and yield (offset, flags, zdata, offset2) tuples.
        `offset` is the start of the chunk and `offset2` is the
        offset at the end of the chunk.
        If `do_decompress` is true, decompress the data and strip
        that flag value.
        This can be used in parallel with other activity, though
        it may impact performance.
    '''
    if offset is None:
      offset = fp.tell()
    with self:
      fp = self.fp
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
          offset2 = fp.tell()
        yield offset, flags, data, offset2

  def fetch(self, offset):
    ''' Fetch data bytes from the supplied offset.
    '''
    fp = self.fp
    with self._lock:
      if self.appending:
        fp.flush()
        self.appending = False
      fp.seek(offset, SEEK_SET)
      flags, data, offset2 = read_chunk(fp, do_decompress=True)
    if flags:
      raise ValueError("unhandled flags: 0x%02x" % (flags,))
    return data

  def add(self, data, no_compress=False):
    ''' Append a chunk of data to the file, return the store start and end offsets.
    '''
    if not self.readwrite:
      raise RuntimeError("%s: not readwrite" % (self,))
    fp = self.fp
    with self._lock:
      if not self.appending:
        self.appending = True
        fp.seek(0, SEEK_END)
      return write_chunk(fp, data, no_compress=no_compress)

class _DataDirFile(SimpleNamespace):
  def __hash__(self):
    return id(self)

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
        `hashclass`: the hash class used to index chunk contents.
        `indexclass`: the IndexClass providing the index to chunks
          in the DataFiles.
        `rollover`: data file roll over size; if a data file grows
            beyond this a new datafile is commenced for new blocks.
    '''
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
    self._load_state()
    MultiOpenMixin.__init__(self)

  def __repr__(self):
    return ( '%s(statedirpath=%r,datadirpath=%r,hashclass=%s,indexclass=%s,rollover=%d)'
             % (self.__class__.__name__,
                self.statedirpath,
                self.datadirpath,
                self.hashclass.HASHNAME,
                self.indexclass.
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
    # mapping of file numerals to DataFile pathnames
    self._n = None
    # cache of open DataFiles
    self._cache = LRU_Cache(maxsize=4,
                            on_remove=lambda k, datafile: datafile.close())
    self.lockpath = makelockfile(self.statefilepath)
    self.index = self.indexclass(self.indexpath, self.hashclass)
    self.index.open()
    # set up indexing thread
    # map individual hashcodes to locations before being persistently stored
    self._unindexed = {}
    self._indexQ = IterableQueue()
    T = self._index_Thread = Thread(name="%s-index-thread" % (self,),
                                    target=self._index_updater)
    T.start()
    for filenum, F in self._filemap.items():
      self._update_datafile(F)

  def shutdown(self):
    self.flush()
    del self._cache
    del self._filemap
    # drain index update queue
    self._indexQ.close()
    self._index_Thread.join()
    if self._unindexed:
      error("UNINDEXED BLOCKS: %r", self._unindexed)
    self.index.close()
    # release lockfile
    os.remove(self.lockpath)
    del self.lockpath

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
    self._unindexed[hashcode] = n, offset
    self._indexQ.put( (hashcode, n, offset) )

  def _index_updater(self):
    ''' Thread body to collect hashcode index data and store it.
    '''
    with Pfx("_index_updater"):
      index = self.index
      unindexed = self._unindexed
      for hashcode, n, offset in self._indexQ:
        index[hashcode] = n, offset
        del unindexed[hashcode]

  def _load_state(self):
    ''' Read STATE_FILENAME.
    '''
    statefilepath = self.statefilepath
    if existspath(statefilepath):
      os.system('sed "s/^/IN  /" %r' % (statefilepath,))
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
                  F = _DataDirFile(filenum=filenum, filename=filename, size=size)
                  if filenum in filemap:
                    raise KeyError('already in filemap: %r' % (filenum,))
                  if filename in filemap:
                    raise KeyError('already in filemap: %r' % (filename,))
                  filemap[filenum] = F
                  filemap[filename] = F
    # presume data in state dir if not specified
    if self.datadirpath is None:
      self.datadirpath = self.statedirpath

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
        for F in set(filemap.values()):
          csvw.writerow( (F.filenum, F.filename, F.size) )
    os.system('sed "s/^/OUT /" %r' % (statefilepath,))

  def _add_datafile(self, filename):
    ''' Add the datafile with basename `filename` to the filemap.
    '''
    filemap = self._filemap
    if filename in filemap:
      raise KeyError('already in filemap: %r' % (filename,))
    filenum = max([0] + list(filemap.keys())) + 1
    F = _DataDirFile(filenum=filenum, filename=filename, size=0)
    filemap[filenum] = F
    return F

  def _new_datafile(self):
    ''' Create a new datafile and return its record.
    '''
    filename = str(uuid4()) + '.vtd'
    DataFile(self.datapathto(filename), readwrite=True, do_create=True)
    F = self._add_datafile(filename)
    return F

  def _update_datafile(self, F):
    ''' Update the datafile record `F`.
    '''
    datafilepath = self.datapathto(F.filename)
    S = os.stat(datafilepath)
    if S.st_size < F.size:
      warning("%s: shorter than expected (%d < %d)",
              datafilepath, S.st_size, F.size)
    elif S.st_size > F.size:
      F.size = self._scan_datafile_from(self[F.filenum], F.size)

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
    self._save_state()
    self._cache.flush()
    self.index.flush()

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

  def _scan_datafile_from(self, F, start):
    pathname = self.datapathto(F.filename)
    with Pfx("_scan_datafile_from(%r)", pathname):
      S = os.stat(pathname)
      if S.st_size > start:
        hashclass = self.hashclass
        with DataDir(self.datapathto(F.filename)) as D:
          offset2 = start
          for offset, flags, data, offset2 in D.scan(offset=start, do_decompress=True):
            hashcode = self.hashclass.from_bytes(data)
            self._queue_index(hashcode, n, offset)
          if offset2 > F.size:
            F.size = offset2

  def updates(self, from_start=False, save_update=False):
    ''' Scan all the datafiles and yield new data as:
          filenum, filename, flags, data, offset, offset2
        being the file index, the filename, flags and data of the
        chunk, offset of the start of the chunk, offset2 the offset
        after the chunk.
    '''
    raise RuntimeError("REALLY? UPDATES?")
    for filenum, filename, size in self.datafiles:
      with Pfx("updates[%d:%r]", filenum, filename):
        S = os.stat(filename)
        endsize = S.st_size
        if from_start:
          size = 0
        if endsize < size:
          warning("st_size(%d) < size", endsize, size)
        else:
          offset = size
          with open(filename, "rb") as fp:
            while size < endsize:
              flags, data, offset2 = read_chunk(fp, offset, do_decompress=True)
              yield filenum, filename, flags, data, offset, offset2
              offset = offset2
          if save_update:
            self.datafiles.update_size(filenum, offset)

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

  def shutdown(self):
    self._gdbm.close()
    self._gdbm = None

  def flush(self):
    self._gdbm.sync()

  def __iter__(self):
    mkhash = self.hashclass.from_hashbytes
    hashcode = self._gdbm.firstkey()
    while hashcode is not None:
      yield mkhash(hashcode)
      hashcode = self._gdbm.nextkey(hashcode)

  __contains__ = lambda self, hashcode: hashcode in self._gdbm
  __getitem__  = lambda self, hashcode: decode_index_entry(self._gdbm[hashcode])
  get          = lambda self, hashcode, default=None: \
                    decode_index_entry(self._gdbm.get(hashcode, default))

  def __setitem__(self, hashcode, value):
    self._gdbm[hashcode] = encode_index_entry(*value)

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
        `reverse`: iterate backwards if true, otherwise forwards
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
  import cs.venti.datafile_tests
  cs.venti.datafile_tests.selftest(sys.argv)
