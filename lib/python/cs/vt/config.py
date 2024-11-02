#!/usr/bin/env python3
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
    exists as existspath,
    expanduser,
    isabs as isabspath,
)
from typing import List, Mapping, Optional, Union

from icontract import require
from typeguard import typechecked

from cs.deco import fmtdoc, default_params, promote, Promotable
from cs.fs import shortpath, longpath, HasFSPath
from cs.logutils import debug
from cs.obj import SingletonMixin, singleton
from cs.pfx import Pfx, pfx, pfx_call, pfx_method
from cs.resources import RunState, uses_runstate
from cs.result import OnDemandResult
from cs.threads import HasThreadState, ThreadState

from . import (
    Lock,
    Store,
    DEFAULT_BASEDIR,
    DEFAULT_CONFIG_ENVVAR,
    DEFAULT_CONFIG_MAP,
    DEFAULT_CONFIG_PATH,
)
from .archive import Archive
from .compose import (
    parse_store_specs,
    get_archive_path,
)
from .convert import (
    expand_path,
    get_integer,
    scaled_value,
)

# pylint: disable=too-many-public-methods
class Config(SingletonMixin, HasFSPath, HasThreadState, Promotable):
  ''' A configuration specification.

      This can be driven by any mapping of mappings: {clause_name => {param => value}}.
      It is modelled on a .ini file, with named clauses each containing named values.

      Parameters:
      * `config_map`: either a mapping of mappings: `{clause_name: {param: value}}`
        or the filename of a file in `.ini` format
  '''

  perthread_state = ThreadState()

  @classmethod
  @fmtdoc
  @typechecked
  def resolve_config_spec(
      cls,
      config_spec: Optional[Union[str, Mapping]] = None,
      default_config_map: Optional[Mapping] = None
  ) -> Union[str, dict]:
    ''' Resolve a `Config` specification. with fallback to a default.
        This returns the filesystem path of a configuration file
        or a mapping such as a `dict`.

        Parameters:
        * `config_spec`: optional configuration specification
        * `default_config_map`: optional fallback configuration

        If supplied, `config_spec` may be a filesystem path (`str`)
        or a mapping of *clause_name*->*param*->*value*.
        If not supplied, the environment variable ${DEFAULT_CONFIG_ENVVAR}
        is looked up, defaulting to {DEFAULT_CONFIG_PATH!r};
        if that is not an existing filesystem path
        then `default_config_map` is used.

        The `default_config_map` parameter may be used to specify a fallback
        mapping; it defaults to `DEFAULT_CONFIG_MAP`.
    '''
    if config_spec is None:
      # look for configuration file
      config_spec = os.environ.get(
          DEFAULT_CONFIG_ENVVAR, expanduser(DEFAULT_CONFIG_PATH)
      )
      if not existspath(config_spec):
        if default_config_map is None:
          default_config_map = DEFAULT_CONFIG_MAP
        config_spec = default_config_map
    return config_spec

  @classmethod
  @typechecked
  def _singleton_key(
      cls,
      config_spec: Optional[Union[str, Mapping]] = None,
      default_config_map: Optional[Mapping] = None
  ):
    config_spec = cls.resolve_config_spec(config_spec, default_config_map)
    return config_spec if isinstance(config_spec, str) else id(config_spec)

  @typechecked
  def __init__(
      self,
      config_spec: Optional[Union[str, dict]] = None,
      default_config_map: Optional[dict] = None
  ):
    if hasattr(self, 'map'):
      return
    config_spec = self.resolve_config_spec(config_spec, default_config_map)
    config = ConfigParser()
    if isinstance(config_spec, str):
      self.path = path = config_spec
      pfx_call(config.read, path)
    else:
      self.path = None
      config.read_dict(config_spec)
    self.map = config
    self._clause_stores = {}  # clause_name => Result->Store
    self._lock = Lock()

  def __str__(self):
    if self.path is None:
      return repr(self)
    return "Config(%s)" % (shortpath(self.path),)

  @classmethod
  def from_str(cls, spec: str) -> "Config":
    ''' Promote a config spec to a `Config`.
    '''
    return cls(config_spec=spec)

  def write(self, f):
    ''' Write the config to a file.
    '''
    self.map.write(f)

  def as_text(self) -> str:
    ''' Return a text transcription of the config.
    '''
    with StringIO() as f:
      self.write(f)
      return f.getvalue()

  def __getitem__(self, clause_name: str) -> Store:
    ''' Return the `Store` defined by the named clause.
    '''
    with self._lock:
      _, R = singleton(
          self._clause_stores,
          clause_name,
          OnDemandResult,
          (self._make_clause_store, clause_name),
          {},
      )
    return R()

  @pfx_method
  def _make_clause_store(self, clause_name: str) -> Store:
    ''' Instantiate the `Store` associated with `clause_name`.
    '''
    try:
      bare_clause = self.map[clause_name]
    except KeyError:
      # pylint: disable=raise-missing-from
      raise KeyError(f"no clause named [{clause_name}]")
    clause = dict(bare_clause)
    for discard in 'address', :
      clause.pop(discard, None)
    try:
      store_type = clause.pop('type')
    except KeyError:
      # pylint: disable=raise-missing-from
      raise ValueError("missing type field in clause")
    store_name = "%s[%s]" % (self, clause_name)
    S = self.new_Store(
        store_name, store_type, clause_name=clause_name, **clause
    )
    return S

  def get_global(self, param: str, default=None):
    ''' Fetch a default parameter from the [GLOBALS] clause.
    '''
    G = self.map['GLOBAL']
    if not G:
      return default
    return G.get(param, default)

  def get_clause(self, clause_name) -> Mapping:
    ''' Return the clause without opening it as a Store.
    '''
    return self.map[clause_name]

  @property
  def basedir(self) -> str:
    ''' The default location for local archives and stores.
    '''
    return longpath(self.get_global('basedir', DEFAULT_BASEDIR))

  @property
  def fspath(self) -> str:
    ''' The `.fspath` is the basedir.
    '''
    return self.basedir

  @property
  def blockmapdir(self) -> str:
    ''' The global blockmapdir.
        Falls back to `{self.basedir}/blockmaps`.
    '''
    return longpath(self.get_global('blockmapdir', self.pathto('blockmaps')))

  @property
  def mountdir(self) -> str:
    ''' The default directory for mount points.
    '''
    return longpath(self.get_global('mountdir'), self.pathto('mnt'))

  def archive(self, archivename) -> Archive:
    ''' Return the Archive named `archivename`.
    '''
    if (not archivename or '.' in archivename or '/' in archivename):
      raise ValueError(f'invalid archive name: {archivename!r}')
    arpath = self.pathto(f'{archivename}.vt')
    return Archive(arpath)

  # pylint: disable=too-many-branches
  @pfx_method
  def parse_special(self, special, readonly) -> "MountSpec":
    ''' Parse the mount command's special device from `special`
        using `MountSpec.from_str`, return the resulting `MountSpec`.
    '''
    from .fs import MountSpec
    return MountSpec.from_str(special, config=self, readonly=readonly)

  @pfx
  @uses_runstate
  def Store_from_spec(
      self, store_spec: str, runstate: RunState, hashclass=None
  ) -> Store:
    ''' Factory function to return an appropriate `Store` instance
        based on its argument:

          store:...       A sequence of stores. Save new data to the
                          first, seek data in all from left to right.

        Multiple stores are combined into a `ProxyStore` which saves
        to the first `Store` and reads from all the `Store`s.

        See also the `.Stores_from_spec` method, which returns the
        separate `Store`s unassembled.
    '''
    from .store import ProxyStore  # pylint: disable=import-outside-toplevel
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
    S.runstate = runstate
    return S

  def Stores_from_spec(self, store_spec, hashclass=None) -> List[Store]:
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

  def new_Store(
      self,
      store_name,
      store_type,
      *,
      clause_name,
      hashclass=None,
      **params
  ) -> Store:
    ''' Construct a store given its specification.
    '''
    if hashclass is not None:
      params['hashclass'] = hashclass
    # process general purpose params
    # blockmapdir: location to store persistent blockmaps
    blockmapdir = params.pop('blockmapdir', None)
    if store_name is None:
      store_name = f'{self}[{clause_name}]'
    constructor_name = f'{store_type}_Store'
    constructor = getattr(self, constructor_name, None)
    if not constructor:
      raise ValueError(
          f'unsupported Store type (no .{constructor_name} method)'
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
    ''' Construct a `Store` from a reference to a configuration clause.
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
  ):
    ''' Construct a DataDirStore from a "datadir" clause.
    '''
    from .store import DataDirStore  # pylint: disable=import-outside-toplevel
    if basedir is None:
      basedir = self.basedir
    if path is None:
      path = clause_name
    path = longpath(path)
    if not isabspath(path):
      if path.startswith('./'):
        path = abspath(path)
      else:
        path = self.pathto(path)
    return DataDirStore(store_name, path, hashclass=hashclass)

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
    from .store import VTDStore  # pylint: disable=import-outside-toplevel
    if basedir is None:
      basedir = self.basedir
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
        path = self.pathto(path)
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
    from .store import FileCacheStore  # pylint: disable=import-outside-toplevel
    if basedir is None:
      basedir = self.basedir
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
        path = self.pathto(path)
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
    from .store import MemoryCacheStore  # pylint: disable=import-outside-toplevel
    if isinstance(max_data, str):
      max_data = scaled_value(max_data)
    return MemoryCacheStore(store_name, max_data, hashclass=hashclass)

  @promote
  def platonic_Store(
      self,
      store_name,
      clause_name,
      *,
      path=None,
      basedir=None,
      follow_symlinks=False,
      meta_store: Optional[Store] = None,
      archive=None,
      hashclass=None,
  ):
    ''' Construct a `PlatonicStore` from a "datadir" clause.
    '''
    from .store import PlatonicStore  # pylint: disable=import-outside-toplevel
    if basedir is None:
      basedir = self.basedir
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
        path = self.pathto(path)
        debug("path ==> %r", path)
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

  # pylint: disable=too-many-branches,too-many-locals
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
    from .store import ProxyStore  # pylint: disable=import-outside-toplevel
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
    elif isinstance(save2, str):
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
      addif=False,
  ):
    ''' Construct a TCPClientStore from a "tcp" clause.
    '''
    from .socket import TCPClientStore  # pylint: disable=import-outside-toplevel
    if host is None:
      host = clause_name
    if port is None:
      raise ValueError('no "port"')
    if isinstance(port, str):
      port, _ = get_integer(port, 0)
    return TCPClientStore(
        store_name,
        (host, port),
        hashclass=hashclass,
        addif=addif,
    )

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
    from .socket import UNIXSocketClientStore  # pylint: disable=import-outside-toplevel
    if socket_path is None:
      socket_path = clause_name
    socket_path = expand_path(socket_path)
    return UNIXSocketClientStore(store_name, socket_path, hashclass=hashclass)

uses_Config = default_params(config=Config.default)
