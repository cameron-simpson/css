#!/usr/bin/python
#
# Configuration file and services.
#   - Cameron Simpson <cs@cskk.id.au> 2017-12-25
#

'''
Store definition configuration file.
'''

import os
from os.path import abspath, isabs as isabspath, join as joinpath
from threading import Lock
from cs.configutils import ConfigWatcher
from cs.fileutils import shortpath, longpath
from cs.logutils import debug
from cs.pfx import Pfx
from cs.result import Result
from . import defaults
from .cache import FileCacheStore, MemoryCacheStore
from .compose import parse_store_specs
from .convert import get_integer, \
    convert_param_int, convert_param_scaled_int, \
    convert_param_path
from .store import PlatonicStore, ProxyStore, DataDirStore
from .socket import TCPClientStore, UNIXSocketClientStore

def Store(spec, config):
  ''' Factory to construct Stores from string specifications.
  '''
  return config.Store_from_spec(spec)

class Config:
  ''' A configuration specification.

      This can be driven by any mapping of mappings: {clause_name => {param => value}}.
      It is modelled on a .ini file, with named clauses each containing named values.

      Parameters:
      * `config_map`: either a mapping of mappings: `{clause_name: {param: value}}`
        or the filename of a file in `.ini` format
      * `environ`: optional environment mapp for `$varname` substitution.
        Default: `os.environ`
      * `runstate`: a optional `cs.resources.RunState` to share
        with the various Stores
  '''

  def __init__(self, config_map, environ=None, runstate=None):
    if environ is None:
      environ = os.environ
    self.environ = environ
    if isinstance(config_map, str):
      self.path = config_map
      config_map = ConfigWatcher(self.path)
    if runstate is None:
      runstate = defaults.runstate
    self.map = config_map
    self.runstate = runstate
    self._stores_by_name = {}  # clause_name => Result->Store
    self._lock = Lock()

  def __str__(self):
    if self.path is None:
      return repr(self)
    return "Config(%s)" % (shortpath(self.path),)

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
      clause = self.map[clause_name]
      store_type = clause.get('type')
      S = Store
      if store_type is None:
        raise ValueError("missing type")
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
    G = self.map.get('GLOBAL')
    if not G:
      return default
    return G.get(param, default)

  def Store_from_spec(self, store_spec):
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
      stores = self.Stores_from_spec(store_spec)
      if not stores:
        raise ValueError("empty Store specification: %r" % (store_spec,))
      if len(stores) == 1:
        return stores[0]
      # multiple stores: save to the front store, read first from the
      # front store then from the rest
      return ProxyStore(store_spec, stores[0:1], stores[0:1], stores[1:])

  def Stores_from_spec(self, store_spec):
    ''' Parse a colon separated list of Store specifications, return a list of Stores.
    '''
    store_specs = list(parse_store_specs(store_spec))
    if not store_specs:
      raise ValueError("empty Store specification: %r" % (store_specs,))
    stores = [
        self.new_Store(store_text, store_type, params)
        for store_text, store_type, params
        in store_specs
    ]
    return stores

  def new_Store(self, store_name, store_type, params, clause_name=None):
    ''' Construct a store given its specification.
    '''
    with Pfx("new_Store(%r,type=%r,params=%r,...)", store_name, store_type, params):
      if not isinstance(params, dict):
        params = dict(params)
        if 'type' in params:
          # shuffle to avoid using builtin "type" as parameter name
          params['type_'] = params.pop('type')
      # process general purpose params
      # blockmapdir: location to store persistent blockmaps
      blockmapdir = params.pop('blockmapdir', None)
      # mountdir: default location for "mount [clausename]" => mountdir/clausename
      mountdir = params.pop('mountdir', None)
      if mountdir is None:
        mountdir = self.get_default('mountdir')
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
      if mountdir is not None:
        S.mountdir = mountdir
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
      data=None,
      runstate=None,
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
    if data is not None:
      data = longpath(data)
    if runstate is None:
      runstate = self.runstate
    return DataDirStore(store_name, path, datadirpath=data, runstate=runstate)

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
      runstate=None,
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
      raise ValueError('missing backend')
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
    if runstate is None:
      runstate = self.runstate
    return FileCacheStore(
        store_name, backend_store, path,
        max_cachefile_size=max_file_size,
        max_cachefiles=max_files,
        runstate=runstate,
    )

  def memory_Store(
      self,
      store_name, clause_name,
      *,
      type_=None,
      max_data=None,
      runstate=None,
  ):
    ''' Construct a PlatonicStore from a "datadir" clause.
    '''
    if type_ is not None:
      assert type_ == 'memory'
    if max_data is None:
      raise ValueError("missing max_data")
    if runstate is None:
      runstate = self.runstate
    return MemoryCacheStore(store_name, max_data)

  def platonic_Store(
      self,
      store_name, clause_name,
      *,
      type_=None,
      path=None,
      basedir=None,
      data=None,
      follow_symlinks=False,
      meta=None,
      archive=None,
      runstate=None,
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
    if data is not None:
      data = longpath(data)
    if follow_symlinks is None:
      follow_symlinks = False
    if meta is None:
      meta_store = None
    elif isinstance(meta, str):
      meta_store = Store(meta, self)
    if isinstance(archive, str):
      archive = longpath(archive)
    if runstate is None:
      runstate = self.runstate
    return PlatonicStore(
        store_name, path,
        datadirpath=data,
        hashclass=None, indexclass=None,
        follow_symlinks=follow_symlinks,
        meta_store=meta_store, archive=archive,
        flag_prefix='VT_' + clause_name,
        runstate=runstate,
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
      runstate=None,
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
        save_stores = self.Stores_from_spec(save)
      else:
        save_stores = save
      readonly = not save_stores
    if read is None:
      read_stores = []
    elif isinstance(read, str):
      read_stores = self.Stores_from_spec(read)
    else:
      read_stores = read
    if save2 is None:
      save2_stores = []
    else:
      if isinstance(save2, str):
        save2_stores = self.Stores_from_spec(save2)
      else:
        save2_stores = save2
    if read2 is None:
      read2_stores = []
    elif isinstance(read2, str):
      read2_stores = self.Stores_from_spec(read2)
    else:
      read2_stores = read2
    if runstate is None:
      runstate = self.runstate
    S = ProxyStore(
        store_name,
        save_stores, read_stores,
        save2=save2_stores, read2=read2_stores,
        runstate=runstate)
    S.readonly = readonly
    return S

  def tcp_Store(
      self,
      store_name,
      *,
      type_=None,
      host=None,
      port=None,
      runstate=None,
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
    if runstate is None:
      runstate = self.runstate
    return TCPClientStore(store_name, (host, port), runstate=runstate)

  def socket_Store(
      self,
      store_name,
      *,
      type_=None,
      socket_path=None,
      runstate=None,
  ):
    ''' Construct a UNIXSocketClientStore from a "socket" clause.
    '''
    if type_ is not None:
      assert type_ == 'socket'
    if runstate is None:
      runstate = self.runstate
    return UNIXSocketClientStore(store_name, socket_path, runstate=runstate)
