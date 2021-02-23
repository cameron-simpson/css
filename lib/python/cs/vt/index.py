#!/usr/bin/python
#
# Binary index classes.
# - Cameron Simpson <cs@cskk.id.au>
#

''' An index is a mapping of hashcodes => FileDataIndexEntry.
    This module supports several backends and a mechanism for choosing one.
'''

from abc import ABC, abstractmethod
from collections import namedtuple
from contextlib import contextmanager
from os import pread
from os.path import exists as pathexists
from zlib import decompress
from cs.binary import BinaryMultiValue, BSUInt
from cs.logutils import warning, info
from cs.pfx import Pfx
from cs.resources import MultiOpenMixin
from . import Lock
from .hash import HashCodeUtilsMixin

_CLASSES = []
_BY_NAME = {}

def class_names():
  ''' Return an iterable of the index class names.
  '''
  return _BY_NAME.keys()

def class_by_name(indexname):
  ''' Return an index class from its name.
  '''
  return _BY_NAME[indexname]

def choose(basepath, preferred_indexclass=None):
  ''' Choose an indexclass from a `basepath` with optional preferred indexclass.
      This prefers an existing index if present.
  '''
  global _CLASSES  # pylint: disable=global-statement
  global _BY_NAME  # pylint: disable=global-statement
  if preferred_indexclass is not None:
    if isinstance(preferred_indexclass, str):
      indexname = preferred_indexclass
      try:
        preferred_indexclass = _BY_NAME[indexname]
      except KeyError:
        warning("ignoring unknown indexclass name %r", indexname)
        preferred_indexclass = None
  indexclasses = list(_CLASSES)
  if preferred_indexclass:
    indexclasses.insert((preferred_indexclass.NAME, preferred_indexclass))
  # look for a preexisting index
  for indexname, indexclass in indexclasses:
    if not indexclass.is_supported():
      continue
    indexpath = indexclass.pathof(basepath)
    if pathexists(indexpath):
      return indexclass
  # otherwise choose the first supported index
  for indexname, indexclass in indexclasses:
    if not indexclass.is_supported():
      continue
    return indexclass
  raise ValueError(
      "no supported index classes available: tried %r" % (indexclasses,)
  )

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

      The only defined flag at present is `FLAG_COMPRESSED`,
      indicating that the raw data should be obtained
      by uncompressing the chunk using `zlib.uncompress`.
  '''

  FLAG_COMPRESSED = 0x01

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

class BinaryIndex(MultiOpenMixin, ABC):
  ''' The base class for indices mapping `bytes`->`bytes`.
  '''

  # make a TypeError if used, subclasses provide their own
  SUFFIX = None

  def __init__(self, basepath):
    ''' Initialise an `BinaryIndex` instance.

        Parameters:
        * `basepath`: the base path to the index; the index itself
          is at `basepath`.SUFFIX
    '''
    MultiOpenMixin.__init__(self)
    self.basepath = basepath

  @classmethod
  def pathof(cls, basepath):
    ''' Construct the path to the index file.
    '''
    return '.'.join((basepath, cls.SUFFIX))

  @property
  def path(self):
    ''' The path to the index file.
    '''
    return self.pathof(self.basepath)

  def __iter__(self):
    return self.keys()

  def get(self, key, default=None):
    ''' Get the `FileDataIndexEntry` for `key`.
        Return `default` for a missing `key` (default `None`).
    '''
    try:
      return self[key]
    except KeyError:
      return default

class LMDBIndex(BinaryIndex):
  ''' LMDB index for a DataDir.
  '''

  NAME = 'lmdb'
  SUFFIX = 'lmdb'
  MAP_SIZE = 1024 * 1024 * 1024

  def __init__(self, lmdbpathbase):
    super().__init__(lmdbpathbase)
    self._lmdb = None
    self._resize_needed = False
    # Locking around transaction control logic.
    self._txn_lock = Lock()
    # Lock preventing activity which cannot occur while a transaction is
    # current. This is primarily for database reopens, as when the
    # LMDB map_size is raised.
    self._txn_idle = Lock()  # available if no transactions are in progress
    self._txn_blocked = Lock()  # available if new transactions may commence
    self._txn_count = 0
    self.map_size = None

  def __str__(self):
    return "%s(%r)" % (type(self).__name__, self.basepath)

  def __len__(self):
    with self._txn_lock:
      db = self._lmdb
      return None if db is None else db.stat()['entries']

  @classmethod
  def is_supported(cls):
    ''' Test whether this index class is supported by the Python environment.
    '''
    try:
      # pylint: disable=import-error,unused-import,import-outside-toplevel
      import lmdb
    except ImportError:
      return False
    return True

  def startup(self):
    ''' Start up the index.
    '''
    self.map_size = 10240  # self.MAP_SIZE
    self._open_lmdb()

  def shutdown(self):
    ''' Shut down the index.
    '''
    with self._txn_idle:
      self.flush()
      self._lmdb.close()
      self._lmdb = None

  def _open_lmdb(self):
    # pylint: disable=import-error,import-outside-toplevel
    import lmdb
    db = self._lmdb = lmdb.Environment(
        self.path,
        subdir=True,
        readonly=False,
        metasync=False,
        sync=False,
        writemap=True,
        map_async=True,
        map_size=self.map_size,
    )
    return db

  def _reopen_lmdb(self):
    with self._txn_lock:
      self._lmdb.sync()
      self._lmdb.close()
      return self._open_lmdb()

  def _embiggen_lmdb(self, new_map_size=None):
    if new_map_size is None:
      new_map_size = self.map_size * 2
    self.map_size = new_map_size
    info("change LMDB map_size to %d", self.map_size)
    # reopen the database
    return self._reopen_lmdb()

  @contextmanager
  def _txn(self, write=False):
    ''' Context manager wrapper for an LMDB transaction which tracks active transactions.
    '''
    with self._txn_blocked:
      with self._txn_lock:
        resize_needed = self._resize_needed
        if resize_needed:
          self._resize_needed = False
      if resize_needed:
        # wait for existing transactions to finish
        with self._txn_idle:
          self._embiggen_lmdb()
      with self._txn_lock:
        count = self._txn_count
        count += 1
        self._txn_count = count
        if count == 1:
          # mark transactions as in progress
          self._txn_idle.acquire()
    try:
      yield self._lmdb.begin(write=write)
    finally:
      # logic mutex
      with self._txn_lock:
        count = self._txn_count
        count -= 1
        self._txn_count = count
        if count == 0:
          # mark all transactions as complete
          self._txn_idle.release()

  def flush(self):
    ''' Flush outstanding data to the index.
    '''
    # no force=True param?
    self._lmdb.sync()

  def keys(self):
    with self._txn() as txn:
      cursor = txn.cursor()
      yield from cursor.iternext(keys=True, values=False)

  def items(self):
    ''' Yield `(key,record)` from index.
    '''
    with self._txn() as txn:
      cursor = txn.cursor()
      for binary_key, binary_entry in cursor.iternext(keys=True, values=True):
        yield binary_key, binary_entry

  def _get(self, key):
    with self._txn() as txn:
      return txn.get(key)

  def __contains__(self, key):
    return self._get(key) is not None

  def __getitem__(self, key):
    ''' Get the `FileDataIndexEntry` for `key`.
        Raise `KeyError` for a missing key.
    '''
    binary_entry = self._get(key)
    if binary_entry is None:
      raise KeyError(key)
    return binary_entry

  def __setitem__(self, key: bytes, binary_entry: bytes):
    # pylint: disable=import-error,import-outside-toplevel
    import lmdb
    while True:
      try:
        with self._txn(write=True) as txn:
          txn.put(key, binary_entry, overwrite=True)
          txn.commit()
      except lmdb.MapFullError as e:
        info("%s", e)
        self._resize_needed = True
      else:
        return

class GDBMIndex(BinaryIndex):
  ''' GDBM index for a DataDir.
  '''

  NAME = 'gdbm'
  SUFFIX = 'gdbm'

  def __init__(self, gdbmpathbase):
    super().__init__(gdbmpathbase)
    self._gdbm = None
    self._gdbm_lock = None
    self._written = False

  @classmethod
  def is_supported(cls):
    ''' Test whether this index class is supported by the Python environment.
    '''
    try:
      # pylint: disable=import-error,unused-import,import-outside-toplevel
      import dbm.gnu
    except ImportError:
      return False
    return True

  def startup(self):
    ''' Start the index: open dbm, allocate lock.
    '''
    # pylint: disable=import-error,import-outside-toplevel
    import dbm.gnu
    with Pfx(self.path):
      self._gdbm = dbm.gnu.open(self.path, 'cf')
    self._gdbm_lock = Lock()
    self._written = False

  def shutdown(self):
    ''' Shutdown the index.
    '''
    self.flush()
    with self._gdbm_lock:
      self._gdbm.close()
      self._gdbm = None
      self._gdbm_lock = None

  def flush(self):
    ''' Flush the index: sync the gdbm.
    '''
    if self._written:
      with self._gdbm_lock:
        if self._written:
          self._gdbm.sync()
          self._written = False

  def keys(self):
    with self._gdbm_lock:
      key = self._gdbm.firstkey()
    while key is not None:
      yield key
      self.flush()
      with self._gdbm_lock:
        key = self._gdbm.nextkey(key)

  def __contains__(self, key):
    with self._gdbm_lock:
      return key in self._gdbm

  def __getitem__(self, key):
    with self._gdbm_lock:
      binary_entry = self._gdbm[key]
    return binary_entry

  def __setitem__(self, key, entry):
    binary_entry = bytes(entry)
    with self._gdbm_lock:
      self._gdbm[key] = binary_entry
      self._written = True

class NDBMIndex(BinaryIndex):
  ''' NDBM index for a DataDir.
  '''

  NAME = 'ndbm'
  SUFFIX = 'ndbm'

  def __init__(self, nmdbpathbase):
    super().__init__(nmdbpathbase)
    self._ndbm = None
    self._ndbm_lock = None
    self._written = False

  @classmethod
  def is_supported(cls):
    ''' Test whether this index class is supported by the Python environment.
    '''
    try:
      # pylint: disable=import-error,unused-import,import-outside-toplevel
      import dbm.ndbm
    except ImportError:
      return False
    return True

  def startup(self):
    ''' Start the index: open dbm, allocate lock.
    '''
    # pylint: disable=import-error,import-outside-toplevel
    import dbm.ndbm
    with Pfx(self.path):
      self._ndbm = dbm.ndbm.open(self.path, 'c')
    self._ndbm_lock = Lock()
    self._written = False

  def shutdown(self):
    ''' Shutdown the index.
    '''
    self.flush()
    with self._ndbm_lock:
      self._ndbm.close()
      self._ndbm = None
      self._ndbm_lock = None

  def flush(self):
    ''' Flush the index: sync the ndbm.
    '''
    # no fast mode, no sync

  def keys(self):
    return iter(self._ndbm.keys())

  def __contains__(self, key):
    with self._ndbm_lock:
      return key in self._ndbm

  def __getitem__(self, key):
    with self._ndbm_lock:
      return self._ndbm[key]

  def __setitem__(self, key, entry):
    binary_entry = bytes(entry)
    with self._ndbm_lock:
      self._ndbm[key] = binary_entry
      self._written = True

class KyotoIndex(BinaryIndex):
  ''' Kyoto Cabinet index.
      Notably this uses a B+ tree for the index and thus one can
      traverse from one key forwards and backwards, which supports
      the coming Store synchronisation processes.
  '''

  NAME = 'kyoto'
  SUFFIX = 'kct'

  def __init__(self, nmdbpathbase):
    super().__init__(nmdbpathbase)
    self._kyoto = None

  @classmethod
  def is_supported(cls):
    ''' Test whether this index class is supported by the Python environment.
    '''
    # pylint: disable=import-error,unused-import,import-outside-toplevel
    try:
      import kyotocabinet
    except ImportError:
      return False
    return True

  def startup(self):
    ''' Open the index.
    '''
    # pylint: disable=import-error,import-outside-toplevel
    from kyotocabinet import DB
    self._kyoto = DB()
    self._kyoto.open(self.path, DB.OWRITER | DB.OCREATE)

  def shutdown(self):
    ''' Close the index.
    '''
    self._kyoto.close()
    self._kyoto = None

  def flush(self):
    ''' Flush pending updates to the index.
    '''
    try:
      self._kyoto.synchronize(hard=False)
    except TypeError:
      self._kyoto.synchronize()

  def __len__(self):
    return self._kyoto.count()

  def __contains__(self, key):
    return self._kyoto.check(key) >= 0

  def __getitem__(self, key):
    binary_entry = self._kyoto.get(key)
    if binary_entry is None:
      raise KeyError(key)
    return binary_entry

  def __setitem__(self, key, binary_entry):
    self._kyoto[key] = binary_entry

  def keys(self, *, start_key=None, reverse=False):
    ''' Generator yielding the keys from the index
        in order starting with optional `start_key`.

        Parameters:
        * `start_key`: the starting key; if missing or None,
          iteration starts with the first key in the index
        * `reverse`: iterate backward if true, otherwise forward
    '''
    cursor = self._kyoto.cursor()
    if reverse:
      if cursor.jump_back(start_key):
        yield cursor.get_key()
        while cursor.step_back():
          yield cursor.get_key()
    else:
      if cursor.jump(start_key):
        yield cursor.get_key()
        while cursor.step():
          yield cursor.get_key()
    cursor.disable()

  __iter__ = keys

def register(indexclass, indexname=None, priority=False):
  ''' Register a new `indexclass`, making it known.

      Parameters:
      * `indexclass`: the index class
      * `indexname`: the index class name, default from indexclass.NAME
      * `priority`: if true, prepend the class to the _CLASSES list otherwise append
  '''
  if indexname is None:
    indexname = indexclass.NAME
  if indexname in _BY_NAME:
    raise ValueError(
        'cannot register index class %s: indexname %r already registered to %s'
        % (indexclass, indexname, _BY_NAME[indexname])
    )
  _BY_NAME[indexname] = indexclass
  if priority:
    _CLASSES.insert(0, (indexclass.NAME, indexclass))
  else:
    _CLASSES.append((indexclass.NAME, indexclass))

for klass in LMDBIndex, KyotoIndex, GDBMIndex, NDBMIndex:
  if klass.is_supported():
    register(klass)

if not _CLASSES:
  raise RuntimeError(
      __name__ + ": no index classes available:"
      " none of LMDBIndex, KyotoIndex, GDBMIndex, NDBMIndex is available"
  )
