#!/usr/bin/python
#
# Configuration file and services.
#   - Cameron Simpson <cs@cskk.id.au> 2017-12-25
#

'''
Store definition configuration file.
'''

from configparser import ConfigParser
import os
from os.path import abspath, isabs as isabspath, join as joinpath, exists as pathexists
import sys
from cs.fileutils import shortpath, longpath
from cs.logutils import debug, warning, error
from cs.pfx import Pfx
from cs.result import Result
from . import Lock, DEFAULT_CONFIG
from .archive import Archive
from .cache import FileCacheStore, MemoryCacheStore
from .compose import parse_store_specs, get_archive_path
from .convert import get_integer, \
    convert_param_int, convert_param_scaled_int, \
    convert_param_path
from .store import PlatonicStore, ProxyStore, DataDirStore
from .socket import TCPClientStore, UNIXSocketClientStore

def Store(spec, config, runstate=None, hashclass=None):
  ''' Factory to construct Stores from string specifications.
  '''
  return config.Store_from_spec(spec, runstate=runstate, hashclass=hashclass)

class Config:
  ''' A configuration specification.

      This can be driven by any mapping of mappings: {clause_name => {param => value}}.
      It is modelled on a .ini file, with named clauses each containing named values.

      Parameters:
      * `config_map`: either a mapping of mappings: `{clause_name: {param: value}}`
        or the filename of a file in `.ini` format
      * `environ`: optional environment mapp for `$varname` substitution.
        Default: `os.environ`
  '''

  def __init__(self, config_map, environ=None, default_config=None):
    if environ is None:
      environ = os.environ
    if default_config is None:
      default_config = DEFAULT_CONFIG
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
      config.read_dict(config_map)
    self.map = config
    self._stores_by_name = {}  # clause_name => Result->Store
    self._lock = Lock()

  def __str__(self):
    if self.path is None:
      return repr(self)
    return "Config(%s)" % (shortpath(self.path),)

  def write(self, fp=None):
    ''' Write the configuration out to the file `fp`.
    '''
    if fp is None:
      fp = sys.stdout
    self.map.write(fp)

  def __getitem__(self, clause_name):
    ''' Return the Store defined by the named clause.
    '''
    with self._lock:
      R = self._stores_by_name.get(clause_name)
      if R is not None:
        return R()
      R = self._stores_by_name[clause_name] = Result("[%s]" % (clause_name,))
    # not previously accessed, construct S
    store_name = "%s[%s]" % (self, clause_name)
    with Pfx(store_name):
      clause = dict(self.map[clause_name])
      for discard in 'address', :
        clause.pop(discard, None)
      try:
        store_type = clause.pop('type')
      except KeyError:
        raise ValueError("missing type field in clause")
      S = self.new_Store(
          store_name,
          store_type,
          clause,
          clause_name=clause_name
      )
      R.result = S
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
    return longpath(self.get_default('basedir'))

  @property
  def mountdir(self):
    ''' The default directory for mount points.
    '''
    return longpath(self.get_default('mountdir'))

  def archive(self, archivename):
    ''' Return the Archive named `archivename`.
    '''
    if (
        not archivename
        or '.' in archivename
        or '/' in archivename
    ):
      raise ValueError("invalid archive name: %r" % (archivename,))
    arpath = joinpath(self.basedir, archivename + '.vt')
    return Archive(arpath)

  def Store_from_spec(self, store_spec, runstate=None, hashclass=None):
    ''' Factory function to return an appropriate BasicStore* subclass
        based on its argument:

          store:...       A sequence of stores. Save new data to the
                          first, seek data in all from left to right.

        Multiple stores are combined into a ProxyStore which saves
        to the first Store and reads from all the Stores.

        See also the .Stores_from_spec method, which returns the
        separate Stores unassembled.
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
            store_spec, stores[0:1], stores[0:1],
            read2=stores[1:], hashclass=hashclass)
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
    stores = [
        self.new_Store(store_text, store_type, params, hashclass=hashclass)
        for store_text, store_type, params
        in store_specs
    ]
    return stores

  def new_Store(self, store_name, store_type, params, clause_name=None, hashclass=None):
    ''' Construct a store given its specification.
    '''
    with Pfx("new_Store(%r,type=%r,params=%r,...)", store_name, store_type, params):
      if not isinstance(params, dict):
        params = dict(params)
      if hashclass is not None:
        params['hashclass'] = hashclass
      # process general purpose params
      # blockmapdir: location to store persistent blockmaps
      blockmapdir = params.pop('blockmapdir', None)
      if store_name is None:
        store_name = str(self) + '[' + clause_name + ']'
      if store_type == 'config':
        S = self.config_Store(store_name, **params)
      elif store_type == 'datadir':
        S = self.datadir_Store(store_name, clause_name, **params)
      elif store_type == 'filecache':
        convert_param_int(params, 'max_files')
        convert_param_scaled_int(params, 'max_file_size')
        S = self.filecache_Store(store_name, clause_name, **params)
      elif store_type == 'memory':
        convert_param_scaled_int(params, 'max_data')
        S = self.memory_Store(store_name, clause_name, **params)
      elif store_type == 'platonic':
        S = self.platonic_Store(store_name, clause_name, **params)
      elif store_type == 'proxy':
        S = self.proxy_Store(store_name, **params)
      elif store_type == 'socket':
        if 'socket_path' not in params:
          params['socket_path'] = clause_name
        convert_param_path(params, 'socket_path')
        S = self.socket_Store(store_name, **params)
      elif store_type == 'tcp':
        if 'host' not in params:
          params['host'] = clause_name
        S = self.tcp_Store(store_name, **params)
      else:
        raise ValueError("unsupported type %r" % (store_type,))
      if S.config is None:
        S.config = self
      if blockmapdir is not None:
        S.blockmapdir = blockmapdir
      return S

  def config_Store(
      self,
      _,    # store_name, unused
      *,
      type_=None,
      clause_name=None,
  ):
    ''' Construct a Store from a reference to a configuration clause.
    '''
    if type_ is None:
      type_ = 'config'
    else:
      assert type_ == 'config'
    if clause_name is None:
      raise ValueError("clause_name may not be None")
    return self[clause_name]

  def datadir_Store(
      self,
      store_name, clause_name,
      *,
      type_=None,
      path=None,
      basedir=None,
      hashclass=None,
  ):
    ''' Construct a DataDirStore from a "datadir" clause.
    '''
    if type_ is not None:
      assert type_ == 'datadir'
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
    return DataDirStore(store_name, path, hashclass=hashclass)

  def filecache_Store(
      self,
      store_name, clause_name,
      *,
      type_=None,
      path=None,
      max_files=None,
      max_file_size=None,
      basedir=None,
      backend=None,
      hashclass=None,
  ):
    ''' Construct a FileCacheStore from a "filecache" clause.
    '''
    if type_ is not None:
      assert type_ == 'filecache'
    if basedir is None:
      basedir = self.get_default('basedir')
    if path is None:
      path = clause_name
      debug("path from clausename: %r", path)
    path = longpath(path)
    debug("longpath(path) ==> %r", path)
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
        store_name, backend_store, path,
        max_cachefile_size=max_file_size,
        max_cachefiles=max_files,
        hashclass=hashclass,
    )

  def memory_Store(
      self,
      store_name, clause_name,
      *,
      type_=None,
      max_data=None,
      hashclass=None,
  ):
    ''' Construct a PlatonicStore from a "datadir" clause.
    '''
    if type_ is not None:
      assert type_ == 'memory'
    if max_data is None:
      raise ValueError("missing max_data")
    return MemoryCacheStore(store_name, max_data, hashclass=hashclass)

  def platonic_Store(
      self,
      store_name, clause_name,
      *,
      type_=None,
      path=None,
      basedir=None,
      follow_symlinks=False,
      meta=None,
      archive=None,
      hashclass=None,
  ):
    ''' Construct a PlatonicStore from a "datadir" clause.
    '''
    if type_ is not None:
      assert type_ == 'platonic'
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
        store_name, path,
        hashclass=hashclass, indexclass=None,
        follow_symlinks=follow_symlinks,
        meta_store=meta_store, archive=archive,
        flags_prefix='VT_' + clause_name,
    )

  def proxy_Store(
      self,
      store_name,
      *,
      type_=None,
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
    if type_ is not None:
      assert type_ == 'proxy'
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
          archives.append( (AS, ptn) )
    S = ProxyStore(
        store_name,
        save_stores, read_stores,
        save2=save2_stores, read2=read2_stores,
        copy2=copy2_stores,
        archives=archives,
        hashclass=hashclass,
    )
    S.readonly = readonly
    return S

  def tcp_Store(
      self,
      store_name,
      *,
      type_=None,
      host=None,
      port=None,
      hashclass=None,
  ):
    ''' Construct a TCPClientStore from a "tcp" clause.
    '''
    if type_ is not None:
      assert type_ == 'tcp'
    if not host:
      host = 'localhost'
    if port is None:
      raise ValueError('no "port"')
    if isinstance(port, str):
      port, _ = get_integer(port, 0)
    return TCPClientStore(store_name, (host, port), hashclass=hashclass)

  def socket_Store(
      self,
      store_name,
      *,
      type_=None,
      socket_path=None,
      hashclass=None,
  ):
    ''' Construct a UNIXSocketClientStore from a "socket" clause.
    '''
    if type_ is not None:
      assert type_ == 'socket'
    return UNIXSocketClientStore(store_name, socket_path, hashclass=hashclass)
