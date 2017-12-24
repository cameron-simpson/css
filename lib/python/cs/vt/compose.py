#!/usr/bin/python
#
# The generic Store factory and parser for Store specifications.
#   - Cameron Simpson <cs@cskk.id.au> 20dec2016
#

from os.path import isabs as isabspath, abspath, join as joinpath
from subprocess import Popen, PIPE
from threading import Lock
from cs.configutils import ConfigWatcher
from cs.fileutils import longpath, shortpath
from cs.lex import skipwhite, get_identifier, get_qstr
from cs.logutils import debug
from cs.pfx import Pfx
from cs.result import Result
from cs.threads import locked
from cs.units import multiparse as multiparse_units, \
    BINARY_BYTES_SCALE, DECIMAL_BYTES_SCALE, DECIMAL_SCALE
from cs.x import X
from .archive import Archive
from .cache import FileCacheStore
from .store import DataDirStore, PlatonicStore, ProxyStore
from .stream import StreamStore
from .tcp import TCPStoreClient

def Store(store_spec, config=None):
  ''' Factory function to return an appropriate BasicStore* subclass
      based on its argument:

        store:...       A sequence of stores. Save new data to the
                        first, seek data in all from left to right.
  '''
  if config is None:
    config = Config({})
  with Pfx(repr(store_spec)):
    X("compose.Store(store_spec=%r)...", store_spec)
    stores = Stores_from_spec(store_spec, config)
    X("stores = %r", stores)
    if len(stores) == 0:
      raise ValueError("empty Store specification: %r" % (store_spec,))
    if len(stores) == 1:
      return stores[0]
    # multiple stores: save to the front store, read first from the
    # front store then from the rest
    return ProxyStore(store_spec, stores[0:1], stores[0:1], stores[1:])

def Stores_from_spec(store_spec, config):
  store_specs = list(parse_store_specs(store_spec))
  if not store_specs:
    raise ValueError("empty Store specification: %r" % (store_specs,))
  stores = [
      Store_from_type_and_params(store_text, store_type, params, config)
      for store_text, store_type, params
      in store_specs
  ]
  X("stores = %r", stores)
  return stores

def parse_store_specs(s, offset=0):
  ''' Parse the string `s` for a list of Store specifications.
  '''
  with Pfx("parse_store_spec(%r)", s):
    store_specs = []
    while offset < len(s):
      with Pfx("offset %d", offset):
        store_text, store_type, params, offset = parse_store_spec(s, offset)
        store_specs.append( (store_text, store_type, params) )
      if offset < len(s):
        with Pfx("offset %d", offset):
          sep = s[offset]
          offset += 1
          if sep == ':':
            continue
          raise ValueError("expected colon ':', found unexpected separator: %r" % (sep,))
    return store_specs

def parse_store_spec(s, offset):
  ''' Parse a single Store specification from a string. Return the text, store type, params and the new offset.

        "text"          Quoted store spec, needed to enclose some of
                        the following syntaxes if they do not consume the
                        whole string.

        [clause_name]   The name of a clause to be obtained from a Config.

        /path/to/store  A DataDirStore directory.
        ./subdir/to/store A relative path to a DataDirStore directory.

        |command        A subprocess implementing the streaming protocol.

        store_type(param=value,...)
                        A general Store specification.
        store_type:params...
                        An inline Store specification. Supported inline types:
                          tcp:[host]:port

        TODO:
          ssh://host/[store-designator-as-above]
          unix:/path/to/socket
                        Connect to a daemon implementing the streaming protocol.
          http[s]://host/prefix
                        A Store presenting content under prefix:
                          /h/hashcode.hashtype  Block data by hashcode
                          /i/hashcode.hashtype  Indirect block by hashcode.
  '''
  offset0 = offset
  if offset >= len(s):
    raise ValueError("empty string")
  if s.startswith('"', offset):
    # "store_spec"
    qs, offset = get_qstr(s, offset, q='"')
    _, store_type, params, offset2 = parse_store_spec(qs, 0)
    if offset2 < len(qs):
      raise ValueError("unparsed text inside quotes: %r", qs[offset2:])
  elif s.startswith('[', offset):
    # [clause_name]
    store_type = 'config'
    offset = skipwhite(s, offset + 1)
    clause_name, offset = get_qstr_or_identifier(s, offset)
    X("clause_name=%r, offset=%d", clause_name, offset)
    offset = skipwhite(s, offset)
    if offset >= len(s) or s[offset] != ']':
      raise ValueError("offset %d: missing closing ']'" % (offset,))
    offset += 1
    params = {'clause_name': clause_name}
  elif s.startswith('/', offset) or s.startswith('./', offset):
    # /path/to/datadir
    store_type = 'datadir'
    params = {'path': s[offset:] }
    offset = len(s)
  elif s.startswith('|', offset):
    # |shell command
    store_type = 'shell'
    params = {'shcmd': s[offset + 1:].strip()}
    offset = len(s)
  else:
    store_type, offset = get_identifier(s, offset)
    if not store_type:
      raise ValueError(
          "expected identifier at offset %d, found: %r"
          % (offset, s[offset:]))
    with Pfx(store_type):
      if s.startswith('(', offset):
        params, offset = get_params(s, offset + 1, ')')
      elif s.startswith(':', offset):
        offset += 1
        params = {}
        if store_type == 'tcp':
          hostpart, offset = get_token(s, offset)
          if not isinstance(hostpart, str):
            raise ValueError(
                "expected hostpart to be a string, got: %r" % (hostpart,))
          params['host'] = hostpart
          if not s.startswith(':', offset):
            raise ValueError(
                "missing port at offset %d, found: %r"
                % (offset, s[offset:]))
          offset += 1
          portpart, offset = get_token(s, offset)
          params['port'] = portpart
        else:
          raise ValueError("unrecognised Store type for inline form")
      else:
        raise ValueError("no parameters")
  return s[offset0:offset], store_type, params, offset

def get_params(s, offset, endchar):
  ''' Parse "param=value,...)". Return params dict and new offset.
  '''
  params = {}
  first = True
  while True:
    ch = s[offset:offset + 1]
    if not ch:
      raise ValueError("hit end of string, expected param or %r" % (endchar,))
    if ch == endchar:
      offset += 1
      return params, offset
    if not first and ch == ',':
      offset += 1
    param, offset = get_qstr_or_identifier(s, offset)
    if not param:
      raise ValueError("rejecting empty parameter name")
    offset = skipwhite(s, offset)
    ch = s[offset:offset + 1]
    if not ch:
      raise ValueError("hit end of string after param %r, expected '='" % (param,))
    if ch != '=':
      raise ValueError("expected '=', found %r" % (ch,))
    offset = skipwhite(s, offset + 1)
    ch = s[offset:offset + 1]
    if not ch:
      raise ValueError("hit end of string after param %r=, expected token" % (param,))
    if ch == endchar or ch == ',':
      raise ValueError("expected token, found %r" % (ch,))
    value, offset = get_token(s, offset)
    params[param] = value
    first = False

def get_qstr_or_identifier(s, offset):
  ''' Parse q quoted string or an identifier.
  '''
  if s.startswith('"', offset):
    return get_qstr(s, offset, q='"')
  return get_identifier(s, offset)

def get_token(s, offset):
  ''' Parse an integer value, an identifier or a quoted string.
  '''
  if offset == len(s):
    raise ValueError("unexpected end of string, expected token")
  if s[offset].isdigit():
    token, offset = get_integer(s, offset)
  else:
    token, offset = get_qstr_or_identifier(s, offset)
  return token, offset

def get_integer(s, offset):
  ''' Parse an integer followed by an optional scale and return computed value.
  '''
  return multiparse_units(
      s,
      (BINARY_BYTES_SCALE, DECIMAL_BYTES_SCALE, DECIMAL_SCALE),
      offset
  )

def CommandStore(shcmd, addif=False):
  ''' Factory to return a StreamStore talking to command.
  '''
  name = "StreamStore(%r)" % ("|" + shcmd, )
  P = Popen(shcmd, shell=True, stdin=PIPE, stdout=PIPE)
  return StreamStore(name, P.stdin, P.stdout, local_store=None, addif=addif)

class Config:
  ''' A configuration specification.

      This can be driven by any mapping of mappings: {clause_name => {param => value}}.
      It is modelled on a .ini file, with named clauses each containing named values.
  '''

  @classmethod
  def from_ini(cls, config_path, lock=None):
    ''' Return a Config from a path to a .ini file `config_path`.
    '''
    return cls(ConfigWatcher(config_path), config_path=config_path)

  def __init__(self, config_map, config_path=None):
    ''' Initialise the Config with `config_map` and optional `config_path`.
    '''
    self.map = config_map
    self.path = config_path
    self._stores_by_name = {}
    self._lock = Lock()
    self._stores_by_name = {}  # clause_name => Result->Store

  def __str__(self):
    if self.path is None:
      return repr(self)
    return "Config(%s)" % (shortpath(self.path),)

  def __getitem__(self, clause_name):
    ''' Return the Store defined by the named clause.
    '''
    X("%s.__getitem__[clause_name=%r]...", type(self).__name__, clause_name)
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
      S = Store_from_type_and_params(
          store_name,
          store_type,
          clause,
          config=self,
          clause_name=clause_name
      )
      R.result = S
    return S

def Store_from_type_and_params(store_name, store_type, params, config=None, clause_name=None):
  ''' Construct a store given its specification.
  '''
  with Pfx("Store_from_type_and_params(%r,type=%r,params=%r,...)", store_name, store_type, params):
    if store_type == 'config':
      S = Store_from_config_clause(store_name, config, **params)
    elif store_type == 'datadir':
      S = Store_from_datadir_clause(store_name, clause_name, **params)
    elif store_type == 'filecache':
      S = Store_from_filecache_clause(store_name, clause_name, **params)
    elif store_type == 'platonic':
      S = Store_from_platonic_clause(store_name, config, clause_name, **params)
    elif store_type == 'proxy':
      S = Store_from_proxy_clause(store_name, config, **params)
    elif store_type == 'tcp':
      if 'host' not in params:
        params['host'] = clause_name
      S = Store_from_tcp_clause(store_name, **params)
    else:
      raise ValueError("unsupported type %r" % (store_type,))
    return S

def Store_from_config_clause(
    store_name, config,
    *,
    type=None,
    clause_name=None,
    path=None,
):
  ''' Construct a Store from a reference to a configuration clause.
  '''
  if type is not None:
    assert type == 'config'
  if clause_name is None:
    raise ValueError("clause_name may not be None")
  if path is not None:
    config = Config.from_ini(path)
  return config[clause_name]

def Store_from_datadir_clause(
    store_name, clause_name,
    *,
    type=None,
    path=None,
    statedir=None,
    data=None,
):
  ''' Construct a DataDirStore from a "datadir" clause.
  '''
  if type is not None:
    assert type == 'datadir'
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
      debug("statedir=%r", statedir)
      if statedir is None:
        raise ValueError('relative path %r but no statedir' % (path,))
      statedir = longpath(statedir)
      debug("longpath(statedir) ==> %r", statedir)
      path = joinpath(statedir, path)
      debug("path ==> %r", path)
  if data is not None:
    data = longpath(data)
  return DataDirStore(store_name, path, data, None, None)

def Store_from_filecache_clause(
    store_name, clause_name,
    *,
    type=None,
    path=None,
    max_files=None,
    max_file_size=None,
    statedir=None,
):
  ''' Construct a FileCacheStorer from a "filecache" clause.
  '''
  if type is not None:
    assert type == 'filecache'
  if max_files is not None:
    max_files = int(max_files)
  if max_file_size is not None:
    if isinstance(max_file_size, str):
      s = max_file_size
      max_file_size, offset = get_integer(s, 0)
      offset = skipwhite(s, offset)
      if offset < len(s):
        raise ValueError("max_file_size: unparsed text: %r" % (s[offset:],))
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
      debug("statedir=%r", statedir)
      if statedir is None:
        raise ValueError('relative path %r but no statedir' % (path,))
      statedir = longpath(statedir)
      debug("longpath(statedir) ==> %r", statedir)
      path = joinpath(statedir, path)
      debug("path ==> %r", path)
  return FileCacheStore(
      store_name, None, path,
      max_cachefile_size=max_file_size,
      max_cachefiles=max_files,
  )

def Store_from_platonic_clause(
    store_name, config, clause_name,
    *,
    type=None,
    path=None,
    statedir=None,
    data=None,
    follow_symlinks=False,
    meta=None,
    archive=None,
):
  ''' Construct a DataDirStore from a "datadir" clause.
  '''
  if type is not None:
    assert type == 'platonic'
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
      debug("statedir=%r", statedir)
      if statedir is None:
        raise ValueError('relative path %r but no statedir' % (path,))
      statedir = longpath(statedir)
      debug("longpath(statedir) ==> %r", statedir)
      path = joinpath(statedir, path)
      debug("path ==> %r", path)
  if data is not None:
    data = longpath(data)
  if follow_symlinks is None:
    follow_symlinks = False
  if meta is None:
    meta_store = None
  elif isinstance(meta, str):
    meta_store = Store(meta, config)
  if archive is None:
    archive = None
  elif isinstance(archive, str):
    archive = Archive(longpath(archive))
  return PlatonicStore(
      store_name, path, data,
      hashclass=None, indexclass=None,
      follow_symlinks=follow_symlinks,
      meta_store=meta_store, archive=archive
  )

def Store_from_proxy_clause(
    store_name,
    config,
    *,
    type=None,
    save=None,
    read=None,
    read2=None,
):
  ''' Construct a ProxyStore.
  '''
  if type is not None:
    assert type == 'proxy'
  if save is None:
    save_stores = []
    readonly = True
  else:
    if isinstance(save, str):
      save_stores = Stores_from_spec(save, config)
    else:
      save_stores = save
    readonly = len(save_stores) == 0
  if read is None:
    read_stores = []
  elif isinstance(read, str):
    read_stores = Stores_from_spec(read, config)
  else:
    read_stores = read
  if read2 is None:
    read2_stores = []
  elif isinstance(read2, str):
    read2_stores = Stores_from_spec(read2, config)
  else:
    read2_stores = read2
  S = ProxyStore(store_name, save_stores, read_stores, read2_stores)
  S.readonly = readonly
  return S

def Store_from_tcp_clause(
    store_name,
    *,
    type=None,
    host=None,
    port=None,
):
  ''' Construct a TCPStoreClient from a "tcp" clause.
  '''
  if type is not None:
    assert type == 'tcp'
  if not host:
    host = 'localhost'
  if port is None:
    raise ValueError('no "port"')
  if isinstance(port, str):
    port, _ = get_integer(port, 0)
  return TCPStoreClient(store_name, (host, port))
