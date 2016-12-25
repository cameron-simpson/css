#!/usr/bin/python
#
# The generic Store factory.
#   - Cameron Simpson <cs@zip.com.au> 20dec2016
#

from os.path import isabs as isabspath, abspath
from subprocess import Popen, PIPE
from cs.configutils import ConfigWatcher
from cs.fileutils import longpath
from cs.logutils import Pfx, X
from cs.threads import locked
from cs.py.func import prop
from .store import ChainStore, DataDirStore
from .stream import StreamStore
from .tcp import TCPStoreClient

def Store(store_spec, config=None):
  ''' Factory function to return an appropriate BasicStore* subclass
      based on its argument:

        store:...       A sequence of stores. Save new data to the
                        first, seek data in all from left to right.
  '''
  with Pfx(repr(store_spec)):
    stores = []
    offset = 0
    while offset < len(store_spec):
      with Pfx(offset+1):
        S, offset = parse_store_spec(store_spec, offset, config=config)
        stores.append(S)
        if offset < len(store_spec):
          sep = store_spec[offset]
          offset += 1
          if sep == ':':
            continue
          raise ValueError("unexpected separator %r at offset %d, expected ':'"
                           % (sep, offset-1))
    X("stores = %r", stores)
    if not stores:
      raise ValueError("no stores in %r" % (store_spec,))
    if len(stores) == 1:
      return stores[0]
    return ChainStore(store_spec, stores)

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
          ssh://host/[store-designator-as-above]
          unix:/path/to/socket
                        Connect to a daemon implementing the streaming protocol.
          http[s]://host/prefix
                        A Store presenting content under prefix:
                          /h/hashtype/hashcode  Block data by hashcode
                          /i/hashtype/hashcode  Indirect block by hashcode.
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
      raise ValueError("no config clause [%s]" %(clause_name,))
  else:
    # /path/to/datadir
    if s.startswith('/', offset) or s.startswith('./', offset):
      S = DataDirStore(s, s, None, None, None)
      offset = len(s)
    # |shell command
    elif s.startswith('|', offset):
      shcmd = s[offset+1:].strip()
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
      S = TCPStoreClient((hostpart, int(port)))
    else:
      raise ValueError("unrecognised Store spec")
  return S, offset

def get_colon(s, offset):
  ''' Fetch text to the next colon. Return text and new offset.
      Returns None if there is no colon.
  '''
  cpos = s.find(':', offset)
  if cpos < 0:
    return None, offset
  return s[offset:cps], cpos + 1

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
    S = self._stores.get(clause_name)
    if S is None:
      store_name = "%s[%s]" % (self, clause_name)
      with Pfx(store_name):
        clause = self[clause_name]
        stype = clause.get('type')
        if stype is None:
          raise ValueError("missing type")
        if stype == "datadir":
          path = clause.get('path')
          if path is None:
            path = clause_name
          path = longpath(path)
          if not isabspath(path):
            if path.startswith('./'):
              path = abspath(path)
            else:
              statedir = clause.get('statedir')
              if statedir is None:
                raise ValueError('relative path %r but no statedir' % (path,))
          datapath = clause.get('data')
          if datapath is not None:
            datapath = longpath(datapath)
          S = DataDirStore(store_name, path, datapath, None, None)
        else:
          raise ValueError("unsupported type %r", stype)
        self._stores[clause_name] = S
    return S
