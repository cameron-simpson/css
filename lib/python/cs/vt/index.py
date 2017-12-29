#!/usr/bin/python
#
# An index is a mapping of hashcodes => bytes-records. This module supports
# several backends and a mechanism for choosing one.
# - Cameron Simpson <cs@cskk.id.au>
#

from contextlib import contextmanager
from os.path import exists as existspath
from threading import Lock
from cs.logutils import warning, info
from cs.pfx import Pfx
from cs.resources import MultiOpenMixin
from .hash import HashCodeUtilsMixin

_CLASSES = []
_BY_NAME = {}

def class_names():
  return _BY_NAME.keys()

def class_by_name(indexname):
  return _BY_NAME[indexname]

def choose(basepath, preferred_indexclass=None):
  ''' Choose an indexclass from a `basepath` with optional preferred indexclass.
  '''
  global _CLASSES
  global _BY_NAME
  if preferred_indexclass is not None:
    if isinstance(preferred_indexclass, str):
      indexname = preferred_indexclass
      try:
        preferred_indexclass = _BY_NAME[indexname]
      except KeyError:
        warning("ignoring unknown indexclass name %r", indexname)
        preferred_indexclass = None
  indexclasses = list(_CLASSES)
  if preferred_indexclass is not None and preferred_indexclass.is_supported():
    indexclasses.insert( (preferred_indexclass.NAME, preferred_indexclass) )
  for indexname, indexclass in indexclasses:
    if not indexclass.is_supported():
      continue
    indexpath = indexclass.pathof(basepath)
    if existspath(indexpath):
      return indexclass
  for indexname, indexclass in indexclasses:
    if not indexclass.is_supported():
      continue
    return indexclass
  raise ValueError("no supported index classes available")

class _Index(HashCodeUtilsMixin, MultiOpenMixin):

  def __init__(self, basepath, hashclass, decode, lock=None):
    ''' Initialise an _Index instance.
        `basepath`: the base path to the index; the index itself
          is at `basepath`.SUFFIX
        `decode`: function to decode a binary index record into the
          return type instance
        `lock`: optional mutex, passed to MultiOpenMixin.__init__.
    '''
    MultiOpenMixin.__init__(self, lock=lock)
    self.basepath = basepath
    self.hashclass = hashclass
    self.decode = decode

  @classmethod
  def pathof(cls, basepath):
    return '.'.join((basepath, cls.SUFFIX))

  @property
  def path(self):
    return self.pathof(self.basepath)

class LMDBIndex(_Index):
  ''' LMDB index for a DataDir.
  '''

  NAME = 'lmdb'
  SUFFIX = 'lmdb'
  MAP_SIZE = 1024 * 1024 * 1024

  def __init__(self, lmdbpathbase, hashclass, decode, lock=None):
    _Index.__init__(self, lmdbpathbase, hashclass, decode, lock=lock)
    self._lmdb = None
    # Locking around transaction control logic.
    self._txn_lock = Lock()
    # Lock preventing activity which cannot occur while a transaction is
    # current. This is primarily for database reopens, as when the
    # LMDB map_size is raised.
    self._txn_idle = Lock()
    self._txn_count = 0

  @classmethod
  def is_supported(cls):
    try:
      import lmdb
    except ImportError:
      return False
    return True

  def startup(self):
    self.map_size = 10240   ## self.MAP_SIZE
    self._open_lmdb()

  def _open_lmdb(self):
    import lmdb
    self._lmdb = lmdb.Environment(
        self.path,
        subdir=True, readonly=False,
        metasync=False, sync=False,
        writemap=True, map_async=True,
        map_size = self.map_size,
    )
    info("%s: %r", self, self._lmdb.info())

  def _embiggen_lmdb(self, new_map_size=None):
    if new_map_size is None:
      new_map_size= self.map_size * 2
    self.map_size = new_map_size
    info("change LMDB map_size to %d", self.map_size)
    with self._txn_idle:
      with self._txn_lock:
        self._lmdb.sync()
        self._lmdb.close()
        self._open_lmdb()

  @contextmanager
  def _txn(self, write=False):
    ''' Context manager wrapper for an LMDB transaction which tracks active transactions.
    '''
    with self._txn_lock:
      count = self._txn_count
      count += 1
      self._txn_count = count
    if count == 1:
      self._txn_idle.acquire()
    yield self._lmdb.begin(write=write)
    with self._txn_lock:
      count = self._txn_count
      count -= 1
      self._txn_count = count
    if count == 0:
      self._txn_idle.release()

  def shutdown(self):
    self.flush()
    self._lmdb.close()

  def flush(self):
    # no force=True param?
    self._lmdb.sync()

  def __iter__(self):
    mkhash = self.hashclass.from_hashbytes
    with self._txn() as txn:
      cursor = txn.cursor()
      for hashcode in cursor.iternext(keys=True, values=False):
        yield mkhash(hashcode)

  def _get(self, hashcode):
    with self._txn() as txn:
      return txn.get(hashcode)

  def __contains__(self, hashcode):
    return self._get(hashcode) is not None

  def __getitem__(self, hashcode):
    entry = self._get(hashcode)
    if entry is None:
      raise KeyError(hashcode)
    return self.decode(entry)

  def get(self, hashcode, default=None):
    entry = self._get(hashcode)
    if entry is None:
      return default
    return self.decode(entry)

  def __setitem__(self, hashcode, value):
    import lmdb
    entry = value.encode()
    while True:
      try:
        with self._txn(write=True) as txn:
          txn.put(hashcode, entry, overwrite=True)
      except lmdb.MapFullError as e:
        info("%s", e)
      else:
        return
      self._embiggen_lmdb()

class GDBMIndex(_Index):
  ''' GDBM index for a DataDir.
  '''

  NAME = 'gdbm'
  SUFFIX = 'gdbm'

  def __init__(self, lmdbpathbase, hashclass, decode, lock=None):
    _Index.__init__(self, lmdbpathbase, hashclass, decode, lock=lock)
    self._gdbm = None

  @classmethod
  def is_supported(cls):
    try:
      import dbm.gnu
    except ImportError:
      return False
    return True

  def startup(self):
    import dbm.gnu
    with Pfx(self.path):
      self._gdbm = dbm.gnu.open(self.path, 'cf')
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
    with self._gdbm_lock:
      hashcode = self._gdbm.firstkey()
    while hashcode is not None:
      yield mkhash(hashcode)
      self.flush()
      with self._gdbm_lock:
        hashcode = self._gdbm.nextkey(hashcode)

  def __contains__(self, hashcode):
    with self._gdbm_lock:
      return hashcode in self._gdbm

  def __getitem__(self, hashcode):
    with self._gdbm_lock:
      entry = self._gdbm[hashcode]
    return self.decode(entry)

  def get(self, hashcode, default=None):
    with self._gdbm_lock:
      entry = self._gdbm.get(hashcode, None)
    if entry is None:
      return default
    return self.decode(entry)

  def __setitem__(self, hashcode, value):
    entry = value.encode()
    with self._gdbm_lock:
      self._gdbm[hashcode] = entry
      self._written = True

class KyotoIndex(_Index):
  ''' Kyoto Cabinet index.
      Notably this uses a B+ tree for the index and thus one can
      traverse from one key forwards and backwards, which supports
      the coming Store synchronisation processes.
  '''

  NAME = 'kyoto'
  SUFFIX = 'kct'

  def __init__(self, lmdbpathbase, hashclass, decode, lock=None):
    _Index.__init__(self, lmdbpathbase, hashclass, decode, lock=lock)
    self._kyoto = None

  @classmethod
  def is_supported(cls):
    try:
      import kyotocabinet
    except ImportError:
      return False
    return True

  def startup(self):
    from kyotocabinet import DB
    self._kyoto = DB()
    self._kyoto.open(self.path, DB.OWRITER | DB.OCREATE)

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
    return self.decode(record)

  def __getitem__(self, hashcode):
    entry = self.get(hashcode)
    if entry is None:
      raise IndexError(str(hashcode))
    return entry

  def __setitem__(self, hashcode, value):
    self._kyoto[hashcode] = value.encode()

  def hashcodes_from(self, start_hashcode=None, reverse=False):
    ''' Generator yielding the keys from the index in order starting with optional `start_hashcode`.
        `start_hashcode`: the starting hashcode; if missing or None,
          iteration starts with the first key in the index
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

def register(indexclass, indexname=None, priority=False):
  ''' Register a new `indexclass`, making it known.
      `indexclass`: the index class
      `indexname`: the index class name, default from indexclass.NAME
      `priority`: if true, prepend the class to the _CLASSES list otherwise append
  '''
  global _CLASSES
  global _BY_NAME
  if indexname is None:
    indexname = indexclass.NAME
  if indexname in _BY_NAME:
    raise ValueError(
            'cannot register index class %s: indexname %r already registered to %s'
            % (indexclass, indexname, _BY_NAME[indexname]))
  _BY_NAME[indexname] = indexclass
  if priority:
    _CLASSES.insert(0, (indexclass.NAME, indexclass))
  else:
    _CLASSES.append((indexclass.NAME, indexclass))

for indexclass in LMDBIndex, KyotoIndex, GDBMIndex:
  if indexclass.is_supported():
    register(indexclass)
