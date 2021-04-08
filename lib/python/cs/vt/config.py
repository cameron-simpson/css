#!/usr/bin/python
#
# Configuration file and services.
#   - Cameron Simpson <cs@cskk.id.au> 2017-12-25
#

'''
Store definition configuration file.
'''

from configparser import ConfigParser
from io import StringIO
import os
from os.path import (
    abspath,
    basename,
    realpath,
    splitext,
    isabs as isabspath,
    isfile as isfilepath,
    join as joinpath,
    exists as pathexists,
)
from icontract import require
from cs.fileutils import shortpath, longpath
from cs.lex import get_ini_clausename, get_ini_clause_entryname
from cs.logutils import debug, warning, error
from cs.obj import SingletonMixin, singleton
from cs.pfx import Pfx, XP, pfx_method
from cs.result import OnDemandResult
from . import Lock, DEFAULT_BASEDIR, DEFAULT_CONFIG_MAP
from .archive import Archive, FilePathArchive
from .backingfile import VTDStore
from .cache import FileCacheStore, MemoryCacheStore
from .compose import (
    parse_store_specs,
    get_archive_path,
)
from .convert import (
    expand_path,
    get_integer,
    scaled_value,
    truthy_word,
)
from .dir import Dir
from .store import PlatonicStore, ProxyStore, DataDirStore
from .socket import TCPClientStore, UNIXSocketClientStore
from .transcribe import parse

def Store(spec, config, runstate=None, hashclass=None):
  ''' Factory to construct Stores from string specifications.
  '''
  return config.Store_from_spec(spec, runstate=runstate, hashclass=hashclass)

class Config(SingletonMixin):
  ''' A configuration specification.

      This can be driven by any mapping of mappings: {clause_name => {param => value}}.
      It is modelled on a .ini file, with named clauses each containing named values.

      Parameters:
      * `config_map`: either a mapping of mappings: `{clause_name: {param: value}}`
        or the filename of a file in `.ini` format
      * `environ`: optional environment map for `$varname` substitution.
        Default: `os.environ`
  '''

  @staticmethod
  @require(
      lambda config_map: config_map is None or
      isinstance(config_map, (str, dict))
  )
  @require(
      lambda default_config:
      (default_config is None or isinstance(default_config, dict))
  )
  def _singleton_key(config_map=None, environ=None, default_config=None):
    if config_map is None:
      config_map = DEFAULT_CONFIG_MAP
    return (
        config_map if isinstance(config_map, str) else id(config_map),
        id(DEFAULT_CONFIG_MAP)
        if default_config is None else id(default_config)
    )

  def __init__(self, config_map=None, environ=None, default_config=None):
    if config_map is None:
      config_map = DEFAULT_CONFIG_MAP
    if environ is None:
      environ = os.environ
    if default_config is None:
      default_config = DEFAULT_CONFIG_MAP
    self.environ = environ
    config = ConfigParser()
    if isinstance(config_map, str):
      self.path = path = config_map
      with Pfx(path):
        read_ok = False
        if pathexists(path):
          try:
            config.read(path)
          except OSError as e:
            error("read error: %s", e)
          else:
            read_ok = True
        else:
          warning("missing config file")
      if not read_ok:
        warning("falling back to default configuration")
        config.read_dict(default_config)
    else:
      self.path = None
      config.read_dict(config_map)
    self.map = config
    self._clause_stores = {}  # clause_name => Result->Store
    self._lock = Lock()

  def __str__(self):
    if self.path is None:
      return repr(self)
    return "Config(%s)" % (shortpath(self.path),)

  def as_text(self):
    ''' Return a text transcription of the config.
    '''
    with StringIO() as S:
      self.map.write(S)
      return S.getvalue()

  def write(self, f):
    ''' Write the config to a file.
    '''
    self.map.write(f)

  def __getitem__(self, clause_name):
    ''' Return the Store defined by the named clause.
    '''
    with self._lock:
      is_new, R = singleton(
          self._clause_stores,
          clause_name,
          OnDemandResult,
          (self._make_clause_store, clause_name),
          {},
      )
    return R()

  def _make_clause_store(self, clause_name):
    ''' Instantiate the `Store` associated with `clause_name`.
    '''
    with Pfx("%s._make_clause_store(%r)", self, clause_name):
      try:
        bare_clause = self.map[clause_name]
      except KeyError:
        raise KeyError(f"no clause named [{clause_name}]")
      clause = dict(bare_clause)
      for discard in 'address', :
        clause.pop(discard, None)
      try:
        store_type = clause.pop('type')
      except KeyError:
        raise ValueError("missing type field in clause")
      store_name = "%s[%s]" % (self, clause_name)
      S = self.new_Store(
          store_name, store_type, clause_name=clause_name, **clause
      )
      return S

  def get_default(self, param, default=None):
    ''' Fetch a default parameter from the [GLOBALS] clause.
    '''
    G = self.map['GLOBAL']
    if not G:
      return default
    return G.get(param, default)

  def get_clause(self, clause_name):
    ''' Return the clause without opening it as a Store.
    '''
    return self.map[clause_name]

  @property
  def basedir(self):
    ''' The default location for local archives and stores.
    '''
    return longpath(self.get_default('basedir', DEFAULT_BASEDIR))

  @property
  def blockmapdir(self):
    ''' The global blockmapdir.
        Falls back to `{self.basedir}/blockmaps`.
    '''
    return longpath(
        self.get_default('blockmapdir', joinpath(self.basedir, 'blockmaps'))
    )

  @property
  def mountdir(self):
    ''' The default directory for mount points.
    '''
    return longpath(self.get_default('mountdir'))

  def archive(self, archivename):
    ''' Return the Archive named `archivename`.
    '''
    if (not archivename or '.' in archivename or '/' in archivename):
      raise ValueError("invalid archive name: %r" % (archivename,))
    arpath = joinpath(self.basedir, archivename + '.vt')
    return Archive(arpath)

  def parse_special(self, special, readonly):
    ''' Parse the mount command's special device from `special`.
        Return `(fsname,readonly,Store,Dir,basename,archive)`.

        Supported formats:
        * `D{...}`: a raw `Dir` transcription.
        * `[`*clause*`]`: a config clause name.
        * `[`*clause*`]`*archive*: a config clause name
        and a reference to a named archive associates with that clause.
        * *archive_file*`.vt`: a path to a `.vt` archive file.
    '''
    fsname = special
    specialD = None
    special_store = None
    archive = None
    if special.startswith('D{') and special.endswith('}'):
      # D{dir}
      specialD, offset = parse(special)
      if offset != len(special):
        raise ValueError("unparsed text: %r" % (special[offset:],))
      if not isinstance(specialD, Dir):
        raise ValueError(
            "does not seem to be a Dir transcription, looks like a %s" %
            (type(specialD),)
        )
      special_basename = specialD.name
      if not readonly:
        warning("setting readonly")
        readonly = True
    elif special.startswith('['):
      if special.endswith(']'):
        # expect "[clause]"
        clause_name, offset = get_ini_clausename(special)
        archive_name = ''
        special_basename = clause_name
      else:
        # expect "[clause]archive"
        # TODO: just pass to Archive(special,config=self)?
        # what about special_basename then?
        clause_name, archive_name, offset = get_ini_clause_entryname(special)
        special_basename = archive_name
      if offset < len(special):
        raise ValueError("unparsed text: %r" % (special[offset:],))
      fsname = str(self) + special
      try:
        special_store = self[clause_name]
      except KeyError:
        raise ValueError("unknown config clause [%s]" % (clause_name,))
      if archive_name is None or not archive_name:
        special_basename = clause_name
      else:
        special_basename = archive_name
      archive = special_store.get_Archive(archive_name)
    else:
      # pathname to archive file
      arpath = special
      if not isfilepath(arpath):
        raise ValueError("not a file")
      fsname = shortpath(realpath(arpath))
      spfx, sext = splitext(basename(arpath))
      if spfx and sext == '.vt':
        special_basename = spfx
      else:
        special_basename = special
      archive = FilePathArchive(arpath)
    return fsname, readonly, special_store, specialD, special_basename, archive

  def Store_from_spec(self, store_spec, runstate=None, hashclass=None):
    ''' Factory function to return an appropriate `BasicStore`* subclass
        based on its argument:

          store:...       A sequence of stores. Save new data to the
                          first, seek data in all from left to right.

        Multiple stores are combined into a `ProxyStore` which saves
        to the first `Store` and reads from all the `Store`s.

        See also the `.Stores_from_spec` method, which returns the
        separate `Store`s unassembled.
    '''
    with Pfx(repr(store_spec)):
      stores = self.Stores_from_spec(store_spec, hashclass=hashclass)
      if not stores:
        raise ValueError("empty Store specification: %r" % (store_spec,))
      if len(stores) == 1:
        S = stores[0]
      else:
        # multiple stores: save to the front store, read first from the
        # front store then from the rest
        S = ProxyStore(
            store_spec,
            stores[0:1],
            stores[0:1],
            read2=stores[1:],
            hashclass=hashclass
        )
      if runstate is not None:
        S.runstate = runstate
      return S

  def Stores_from_spec(self, store_spec, hashclass=None):
    ''' Parse a colon separated list of Store specifications,
        return a list of Stores.
    '''
    store_specs = list(parse_store_specs(store_spec))
    if not store_specs:
      raise ValueError("empty Store specification: %r" % (store_specs,))
    stores = []
    for store_text, store_type, params in store_specs:
      clause_name = params.pop('clause_name', f"<{store_text}>")
      stores.append(
          self.new_Store(
              store_text,
              store_type,
              clause_name=clause_name,
              hashclass=hashclass,
              **params
          )
      )
    return stores

  @pfx_method(use_str=True)
  def new_Store(
      self, store_name, store_type, *, clause_name, hashclass=None, **params
  ):
    ''' Construct a store given its specification.
    '''
    if hashclass is not None:
      params['hashclass'] = hashclass
    # process general purpose params
    # blockmapdir: location to store persistent blockmaps
    blockmapdir = params.pop('blockmapdir', None)
    if store_name is None:
      store_name = str(self) + '[' + clause_name + ']'
    constructor_name = store_type + '_Store'
    constructor = getattr(self, constructor_name, None)
    if not constructor:
      raise ValueError(
          "unsupported Store type (no .%s method)" % (constructor_name,)
      )
    S = constructor(store_name, clause_name, **params)
    if S.config is None:
      S.config = self
    if blockmapdir is not None:
      S.blockmapdir = blockmapdir
    return S

  @require(lambda clause_name: isinstance(clause_name, str))
  def config_Store(
      self,
      _,  # store_name, unused
      clause_name,
  ):
    ''' Construct a Store from a reference to a configuration clause.
    '''
    return self[clause_name]

  def datadir_Store(
      self,
      store_name,
      clause_name,
      *,
      path=None,
      basedir=None,
      hashclass=None,
      raw=False,
  ):
    ''' Construct a DataDirStore from a "datadir" clause.
    '''
    if basedir is None:
      basedir = self.get_default('basedir')
    if path is None:
      path = clause_name
    path = longpath(path)
    if not isabspath(path):
      if path.startswith('./'):
        path = abspath(path)
      else:
        if basedir is None:
          raise ValueError('relative path %r but no basedir' % (path,))
        basedir = longpath(basedir)
        path = joinpath(basedir, path)
    if isinstance(raw, str):
      raw = truthy_word(raw)
    return DataDirStore(store_name, path, hashclass=hashclass, raw=raw)

  def datafile_Store(
      self,
      store_name,
      clause_name,
      *,
      path=None,
      basedir=None,
      hashclass=None,
  ):
    ''' Construct a VTDStore from a "datafile" clause.
    '''
    if basedir is None:
      basedir = self.get_default('basedir')
    if path is None:
      path = clause_name
    path = longpath(path)
    if not isabspath(path):
      if path.startswith('./'):
        path = abspath(path)
      else:
        if basedir is None:
          raise ValueError('relative path %r but no basedir' % (path,))
        basedir = longpath(basedir)
        path = joinpath(basedir, path)
    return VTDStore(store_name, path, hashclass=hashclass)

  def filecache_Store(
      self,
      store_name,
      clause_name,
      *,
      path=None,
      max_files=None,
      max_file_size=None,
      basedir=None,
      backend=None,
      hashclass=None,
  ):
    ''' Construct a FileCacheStore from a "filecache" clause.
    '''
    if basedir is None:
      basedir = self.get_default('basedir')
    if path is None:
      path = clause_name
      debug("path from clausename: %r", path)
    path = longpath(path)
    debug("longpath(path) ==> %r", path)
    if isinstance(max_files, str):
      max_files = scaled_value(max_files)
    if isinstance(max_file_size, str):
      max_file_size = scaled_value(max_file_size)
    if backend is None:
      backend_store = None
    else:
      backend_store = self.Store_from_spec(backend)
    if not isabspath(path):
      if path.startswith('./'):
        path = abspath(path)
        debug("abspath ==> %r", path)
      else:
        if basedir is None:
          raise ValueError('relative path %r but no basedir' % (path,))
        basedir = longpath(basedir)
        debug("longpath(basedir) ==> %r", basedir)
        path = joinpath(basedir, path)
        debug("path ==> %r", path)
    return FileCacheStore(
        store_name,
        backend_store,
        path,
        max_cachefile_size=max_file_size,
        max_cachefiles=max_files,
        hashclass=hashclass,
    )

  @staticmethod
  def memory_Store(
      store_name,
      _,  # ignore the clause_name
      *,
      max_data=None,
      hashclass=None,
  ):
    ''' Construct a PlatonicStore from a "datadir" clause.
    '''
    if isinstance(max_data, str):
      max_data = scaled_value(max_data)
    return MemoryCacheStore(store_name, max_data, hashclass=hashclass)

  def platonic_Store(
      self,
      store_name,
      clause_name,
      *,
      path=None,
      basedir=None,
      follow_symlinks=False,
      meta=None,
      archive=None,
      hashclass=None,
  ):
    ''' Construct a PlatonicStore from a "datadir" clause.
    '''
    if basedir is None:
      basedir = self.get_default('basedir')
    if path is None:
      path = clause_name
      debug("path from clausename: %r", path)
    path = longpath(path)
    debug("longpath(path) ==> %r", path)
    if not isabspath(path):
      if path.startswith('./'):
        path = abspath(path)
        debug("abspath ==> %r", path)
      else:
        if basedir is None:
          raise ValueError('relative path %r but no basedir' % (path,))
        basedir = longpath(basedir)
        debug("longpath(basedir) ==> %r", basedir)
        path = joinpath(basedir, path)
        debug("path ==> %r", path)
    if follow_symlinks is None:
      follow_symlinks = False
    if meta is None:
      meta_store = None
    elif isinstance(meta, str):
      meta_store = Store(meta, self)
    if isinstance(archive, str):
      archive = longpath(archive)
    return PlatonicStore(
        store_name,
        path,
        hashclass=hashclass,
        indexclass=None,
        follow_symlinks=follow_symlinks,
        meta_store=meta_store,
        archive=archive,
        flags_prefix='VT_' + clause_name,
    )

  def proxy_Store(
      self,
      store_name,
      _,  # ignore clause_name
      *,
      save=None,
      read=None,
      save2=None,
      read2=None,
      copy2=None,
      archives=(),
      hashclass=None,
  ):
    ''' Construct a ProxyStore.
    '''
    if save is None:
      save_stores = []
      readonly = True
    else:
      if isinstance(save, str):
        save_stores = self.Stores_from_spec(save, hashclass=hashclass)
      else:
        save_stores = save
      readonly = not save_stores
    if read is None:
      read_stores = []
    elif isinstance(read, str):
      read_stores = self.Stores_from_spec(read, hashclass=hashclass)
    else:
      read_stores = read
    if save2 is None:
      save2_stores = []
    else:
      if isinstance(save2, str):
        save2_stores = self.Stores_from_spec(save2, hashclass=hashclass)
      else:
        save2_stores = save2
    if read2 is None:
      read2_stores = []
    elif isinstance(read2, str):
      read2_stores = self.Stores_from_spec(read2, hashclass=hashclass)
    else:
      read2_stores = read2
    if copy2 is None:
      copy2_stores = []
    elif isinstance(copy2, str):
      copy2_stores = self.Stores_from_spec(copy2, hashclass=hashclass)
    else:
      copy2_stores = copy2
    if isinstance(archives, str):
      archive_path, offset = get_archive_path(archives)
      if offset < len(archives):
        raise ValueError("unparsed archive path: %r" % (archives[offset:],))
      archives = []
      for clause_name, ptn in archive_path:
        with Pfx("[%s]%s", clause_name, ptn):
          AS = self[clause_name]
          archives.append((AS, ptn))
    S = ProxyStore(
        store_name,
        save_stores,
        read_stores,
        save2=save2_stores,
        read2=read2_stores,
        copy2=copy2_stores,
        archives=archives,
        hashclass=hashclass,
    )
    S.readonly = readonly
    return S

  @staticmethod
  def tcp_Store(
      store_name,
      clause_name,
      *,
      host=None,
      port=None,
      hashclass=None,
  ):
    ''' Construct a TCPClientStore from a "tcp" clause.
    '''
    if host is None:
      host = clause_name
    if port is None:
      raise ValueError('no "port"')
    if isinstance(port, str):
      port, _ = get_integer(port, 0)
    return TCPClientStore(store_name, (host, port), hashclass=hashclass)

  @staticmethod
  def socket_Store(
      store_name,
      clause_name,
      *,
      socket_path=None,
      hashclass=None,
  ):
    ''' Construct a `UNIXSocketClientStore` from a "socket" clause.
    '''
    if socket_path is None:
      socket_path = clause_name
    socket_path = expand_path(socket_path)
    return UNIXSocketClientStore(store_name, socket_path, hashclass=hashclass)
