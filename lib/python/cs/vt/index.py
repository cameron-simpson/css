#!/usr/bin/python
#
# INdex classes.
# - Cameron Simpson <cs@cskk.id.au>
#

''' An index is a mapping of hashcodes => bytes-records.
    This module supports several backends and a mechanism for choosing one.
'''

from contextlib import contextmanager
from os.path import exists as pathexists
from cs.logutils import warning, info
from cs.pfx import Pfx
from cs.resources import MultiOpenMixin
from . import Lock
from .hash import HashCodeUtilsMixin

_CLASSES = []
_BY_NAME = {}

def class_names():
  ''' Return the index class names.
  '''
  return _BY_NAME.keys()

def class_by_name(indexname):
  ''' Return an index class from its name.
  '''
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
    if pathexists(indexpath):
      return indexclass
  for indexname, indexclass in indexclasses:
    if not indexclass.is_supported():
      continue
    return indexclass
  raise ValueError(
      "no supported index classes available: tried %r"
      % (indexclasses,))

class _Index(HashCodeUtilsMixin, MultiOpenMixin):

  def __init__(self, basepath, hashclass, decode, lock=None):
    ''' Initialise an _Index instance.

        Parameters:
        * `basepath`: the base path to the index; the index itself
          is at `basepath`.SUFFIX
        * `decode`: function to decode a binary index record into the
          return type instance
        * `lock`: optional mutex, passed to MultiOpenMixin.__init__.
    '''
    MultiOpenMixin.__init__(self, lock=lock)
    self.basepath = basepath
    self.hashclass = hashclass
    self.decode = decode

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

class LMDBIndex(_Index):
  ''' LMDB index for a DataDir.
  '''

  NAME = 'lmdb'
  SUFFIX = 'lmdb'
  MAP_SIZE = 1024 * 1024 * 1024

  def __init__(self, lmdbpathbase, hashclass, decode, lock=None):
    _Index.__init__(self, lmdbpathbase, hashclass, decode, lock=lock)
    self._lmdb = None
    self._resize_needed = False
    # Locking around transaction control logic.
    self._txn_lock = Lock()
    # Lock preventing activity which cannot occur while a transaction is
    # current. This is primarily for database reopens, as when the
    # LMDB map_size is raised.
    self._txn_idle = Lock()     # available if no transactions are in progress
    self._txn_blocked = Lock()  # available if new transactions may commence
    self._txn_count = 0

  @classmethod
  def is_supported(cls):
    ''' Test whether this index class is supported by the Python environment.
    '''
    try:
      import lmdb
    except ImportError:
      return False
    return True

  def startup(self):
    ''' Start up the index.
    '''
    self.map_size = 10240   # self.MAP_SIZE
    self._open_lmdb()

  def shutdown(self):
    ''' Shut down the index.
    '''
    with self._txn_idle:
      self.flush()
      self._lmdb.close()
      self._lmdb = None

  def _open_lmdb(self):
    import lmdb
    self._lmdb = lmdb.Environment(
        self.path,
        subdir=True, readonly=False,
        metasync=False, sync=False,
        writemap=True, map_async=True,
        map_size=self.map_size,
    )

  def _embiggen_lmdb(self, new_map_size=None):
    if new_map_size is None:
      new_map_size = self.map_size * 2
    self.map_size = new_map_size
    info("change LMDB map_size to %d", self.map_size)
    # reopen the database
    self._lmdb.sync()
    self._lmdb.close()
    self._open_lmdb()

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

  def __iter__(self):
    mkhash = self.hashclass.from_hashbytes
    with self._txn() as txn:
      cursor = txn.cursor()
      for hashcode in cursor.iternext(keys=True, values=False):
        yield mkhash(hashcode)

  def keys(self, hashclass=None):
    if hashclass is not None:
      if hashclass is not self.hashclass:
        raise RuntimeError(
            "%s.keys: hashclass:%s != self.hashclass:%s"
            % (type(self), hashclass, self.hashclass))
    return iter(self)

  def items(self):
    ''' Yield `(hashcode,record)` from index.
    '''
    mkhash = self.hashclass.from_hashbytes
    with self._txn() as txn:
      cursor = txn.cursor()
      for hashcode, record in cursor.iternext(keys=True, values=True):
        yield mkhash(hashcode), self.decode(record)

  def _get(self, hashcode):
    with self._txn() as txn:
      return txn.get(hashcode)

  def __contains__(self, hashcode):
    return self._get(hashcode) is not None

  def __getitem__(self, hashcode):
    record = self._get(hashcode)
    if record is None:
      raise KeyError(hashcode)
    return self.decode(record)

  def get(self, hashcode, default=None):
    ''' Get and decode the record for `hashcode`.
        Return None for missing `hashcode`.
    '''
    entry = self._get(hashcode)
    if entry is None:
      return default
    return self.decode(entry)

  def __setitem__(self, hashcode, entry):
    import lmdb
    record = entry.encode()
    while True:
      try:
        with self._txn(write=True) as txn:
          txn.put(hashcode, record, overwrite=True)
          txn.commit()
      except lmdb.MapFullError as e:
        info("%s", e)
        self._resize_needed = True
      else:
        return

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
    ''' Test whether this index class is supported by the Python environment.
    '''
    try:
      import dbm.gnu
    except ImportError:
      return False
    return True

  def startup(self):
    ''' Start the index: open dbm, allocate lock.
    '''
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
      del self._gdbm_lock

  def flush(self):
    ''' Flush the index: sync the gdbm.
    '''
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
    ''' Get and decode the record for `hashcode`.
        Return None for missing `hashcode`.
    '''
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
    ''' Test whether this index class is supported by the Python environment.
    '''
    try:
      import kyotocabinet
    except ImportError:
      return False
    return True

  def startup(self):
    ''' Open the index.
    '''
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

  def __contains__(self, hashcode):
    return self._kyoto.check(hashcode) >= 0

  def get(self, hashcode):
    ''' Get and decode the record for `hashcode`.
        Return None for missing `hashcode`.
    '''
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
    ''' Generator yielding the keys from the index
        in order starting with optional `start_hashcode`.

        Parameters:
        * `start_hashcode`: the starting hashcode; if missing or None,
          iteration starts with the first key in the index
        * `reverse`: iterate backward if true, otherwise forward
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
        % (indexclass, indexname, _BY_NAME[indexname]))
  _BY_NAME[indexname] = indexclass
  if priority:
    _CLASSES.insert(0, (indexclass.NAME, indexclass))
  else:
    _CLASSES.append((indexclass.NAME, indexclass))

for klass in LMDBIndex, KyotoIndex, GDBMIndex:
  if klass.is_supported():
    register(klass)
