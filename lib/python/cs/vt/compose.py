#!/usr/bin/python
#
# The generic Store factory.
#   - Cameron Simpson <cs@cskk.id.au> 20dec2016
#

from os.path import isabs as isabspath, abspath, join as joinpath
from subprocess import Popen, PIPE
from cs.configutils import ConfigWatcher
from cs.fileutils import longpath, shortpath
from cs.lex import get_qstr, skipwhite
from cs.logutils import debug
from cs.pfx import Pfx
from cs.threads import locked
from cs.units import multiparse as multiparse_units, \
    BINARY_BYTES_SCALE, DECIMAL_BYTES_SCALE, DECIMAL_SCALE
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
  with Pfx(repr(store_spec)):
    stores = list(parse_store_specs(store_spec, config=config))
    if not stores:
      raise ValueError("empty Store specification: %r" % (store_spec,))
    if len(stores) == 1:
      return stores[0]
    # multiple stores: save to the front store, read first from the
    # front store then from the rest
    return ProxyStore(store_spec, stores[0:1], stores[0:1], stores[1:])

def parse_store_specs(s, offset=0, config=None):
  with Pfx("parse_store_spec(%r)", s):
    stores = []
    while offset < len(s):
      with Pfx("offset %d", offset):
        S, offset = parse_store_spec(s, offset, config=config)
        stores.append(S)
      if offset < len(s):
        with Pfx("offset %d", offset):
          sep = s[offset]
          offset += 1
          if sep == ':':
            continue
          raise ValueError("expected colon ':', found unexpected separator: %r" % (sep,))
    return stores

def parse_store_spec(s, offset, config=None):
  ''' Parse a single Store specification from a string.
      Return the Store and the new offset.

        "text"          Quoted store spec, needed to bound some of
                        the following syntaxes if they do not consume the
                        whole string.

        [config-clause] A Store as specified by the named config-clause.

        /path/to/store  A DataDirStore directory.
        ./subdir/to/store A relative path to a DataDirStore directory.

        |command        A subprocess implementing the streaming protocol.

        tcp:[host]:port Connect to a daemon implementing the streaming protocol.

        TODO:
          type(param=value,...)
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
    qs, offset = get_qstr(s, offset)
    S, offset2 = parse_store_spec(qs, 0, config=config)
    if offset2 < len(qs):
      raise ValueError("unparsed text inside quotes: %r", qs[offset2:])
  elif s.startswith('[', offset):
    offset += 1
    endpos = s.find(']', offset)
    if endpos < 0:
      raise ValueError("missing closing ']'")
    clause_name = s[offset:endpos]
    offset = endpos + 1
    if config is None:
      raise ValueError("no config supplied, rejecting %r" % (s[offset0:],))
    S = config.Store(clause_name)
    if S is None:
      raise ValueError("no config clause [%s]" % (clause_name,))
  else:
    # /path/to/datadir
    if s.startswith('/', offset) or s.startswith('./', offset):
      dirpath = s[offset:]
      S = DataDirStore(dirpath, dirpath)
      offset = len(s)
    # |shell command
    elif s.startswith('|', offset):
      shcmd = s[offset + 1:].strip()
      S = CommandStore(shcmd)
      offset = len(s)
    # TCP connection
    elif s.startswith('tcp:', offset):
      offset += 4
      # collect host part
      cpos = s.find(':', offset)
      if cpos < 0:
        raise ValueError("no host part terminating colon")
      hostpart = s[offset:cpos]
      offset = cpos + 1
      if not hostpart:
        hostpart = 'localhost'
      # collect port
      portpart = s[offset:]
      offset = len(s)
      S = TCPStoreClient("tcp:%s:%s" % (hostpart, portpart), (hostpart, int(portpart)))
    else:
      raise ValueError("unrecognised Store spec")
  return S, offset

def CommandStore(shcmd, addif=False):
  ''' Factory to return a StreamStore talking to command.
  '''
  name = "StreamStore(%r)" % ("|" + shcmd, )
  P = Popen(shcmd, shell=True, stdin=PIPE, stdout=PIPE)
  return StreamStore(name, P.stdin, P.stdout, local_store=None, addif=addif)

class ConfigFile(ConfigWatcher):
  ''' Live tracker of a vt configuration file.
  '''

  def __init__(self, config_path):
    ConfigWatcher.__init__(self, config_path)
    self._stores = {}

  @locked
  def Store(self, clause_name):
    debug("ConfigFile.Store(clause_name=%r)...", clause_name)
    S = self._stores.get(clause_name)
    if S is None:
      # not previously accessed, construct S
      store_name = "%s[%s]" % (shortpath(self.path), clause_name)
      with Pfx(store_name):
        clause = self[clause_name]
        stype = clause.get('type')
        if stype is None:
          raise ValueError("missing type")
        if stype == 'datadir':
          S = Store_from_datadir_clause(store_name, self, clause_name)
        elif stype == 'filecache':
          S = Store_from_filecache_clause(store_name, self, clause_name)
        elif stype == 'platonic':
          S = Store_from_platonic_clause(store_name, self, clause_name)
        elif stype == 'proxy':
          S = Store_from_proxy_clause(store_name, self, clause_name)
        elif stype == 'tcp':
          S = Store_from_tcp_clause(store_name, self, clause_name)
        else:
          raise ValueError("unsupported type %r" % (stype,))
        self._stores[clause_name] = S
    return S

def Store_from_datadir_clause(store_name, config, clause_name):
  ''' Construct a DataDirStore from a "datadir" clause.
  '''
  clause = config[clause_name]
  path = clause.get('path')
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
      statedir = clause.get('statedir')
      debug("statedir=%r", statedir)
      if statedir is None:
        raise ValueError('relative path %r but no statedir' % (path,))
      statedir = longpath(statedir)
      debug("longpath(statedir) ==> %r", statedir)
      path = joinpath(statedir, path)
      debug("path ==> %r", path)
  datapath = clause.get('data')
  if datapath is not None:
    datapath = longpath(datapath)
  return DataDirStore(store_name, path, datapath, None, None)

def Store_from_filecache_clause(store_name, config, clause_name):
  ''' Construct a FileCacheStorer from a "filecache" clause.
  '''
  clause = config[clause_name]
  path = clause.get('path')
  max_cachefiles = clause.get('max_files')
  if max_cachefiles is not None:
    max_cachefiles = int(max_cachefiles)
  max_cachefile_size = clause.get('max_file_size')
  if max_cachefile_size is not None:
    s = max_cachefile_size
    max_cachefile_size, offset = multiparse_units(
        s, (BINARY_BYTES_SCALE, DECIMAL_BYTES_SCALE, DECIMAL_SCALE))
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
      statedir = clause.get('statedir')
      debug("statedir=%r", statedir)
      if statedir is None:
        raise ValueError('relative path %r but no statedir' % (path,))
      statedir = longpath(statedir)
      debug("longpath(statedir) ==> %r", statedir)
      path = joinpath(statedir, path)
      debug("path ==> %r", path)
  return FileCacheStore(
      store_name, None, path,
      max_cachefile_size=max_cachefile_size,
      max_cachefiles=max_cachefiles,
  )

def Store_from_platonic_clause(store_name, config, clause_name):
  ''' Construct a DataDirStore from a "datadir" clause.
  '''
  clause = config[clause_name]
  path = clause.get('path')
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
      statedir = clause.get('statedir')
      debug("statedir=%r", statedir)
      if statedir is None:
        raise ValueError('relative path %r but no statedir' % (path,))
      statedir = longpath(statedir)
      debug("longpath(statedir) ==> %r", statedir)
      path = joinpath(statedir, path)
      debug("path ==> %r", path)
  datapath = clause.get('data')
  if datapath is not None:
    datapath = longpath(datapath)
  follow_symlinks = clause.get('follow_symlinks')
  if follow_symlinks is None:
    follow_symlinks = False
  meta_spec = clause.get('meta')
  if meta_spec is None:
    meta_store = None
  else:
    meta_store = Store(meta_spec, config)
  archive_path = clause.get('archive')
  if archive_path is None:
    archive = None
  else:
    archive = Archive(longpath(archive_path))
  return PlatonicStore(
      store_name, path, datapath,
      hashclass=None, indexclass=None,
      follow_symlinks=follow_symlinks,
      meta_store=meta_store, archive=archive
  )

def Store_from_proxy_clause(store_name, config, clause_name):
  ''' Construct a ProxyStore.
  '''
  clause = config[clause_name]
  save = clause.get('save')
  if save is None:
    save_stores = []
    readonly = True
  else:
    save_stores = list(parse_store_specs(save))
    readonly = len(save_stores) == 0
  read = clause.get('read')
  if read is None:
    read_stores = []
  else:
    read_stores = list(parse_store_specs(read))
  read2 = clause.get('read2')
  if read2 is None:
    read2_stores = []
  else:
    read2_stores = list(parse_store_specs(read2))
  S = ProxyStore(store_name, save_stores, read_stores, read2_stores)
  S.readonly = readonly
  return S

def Store_from_tcp_clause(store_name, config, clause_name):
  ''' Construct a TCPStoreClient from a "tcp" clause.
  '''
  clause = config[clause_name]
  hostpart = clause.get("host")
  if not hostpart:
    raise ValueError('no "host"')
  portpart = clause.get("port")
  if not portpart:
    raise ValueError('no "port"')
  return TCPStoreClient(store_name, (hostpart, int(portpart)))
