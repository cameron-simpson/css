#!/usr/bin/python
#
# Block stores.
#       - Cameron Simpson <cs@zip.com.au>
#

''' Basic Store classes.

    Throughout these classes the term 'channel' means an object with a .get()
    method and usually a .put() method (unless it is instantiated with a
    pre-queued value for the .get()). It may be a Queue, Q1, Channel, Get1
    or any similar object for delivery of a result "later".
'''

from __future__ import with_statement
from abc import ABC, abstractmethod
from binascii import hexlify
from contextlib import contextmanager
import os
import os.path
import sys
from threading import Lock, RLock
from threading import Thread
from time import sleep
from cs.py3 import Queue
from cs.asynchron import report as reportLFs
from cs.later import Later
from cs.logutils import status, info, debug, warning, Pfx, D, X, XP
from cs.progress import Progress
from cs.resources import MultiOpenMixin
from cs.seq import Seq
from cs.threads import Q1, Get1
from . import defaults, totext
from .datafile import DataDir, DEFAULT_INDEXCLASS
from .hash import DEFAULT_HASHCLASS, HashCodeUtilsMixin

class _BasicStoreCommon(MultiOpenMixin, HashCodeUtilsMixin, ABC):
  ''' Core functions provided by all Stores.

      Subclasses should not subclass this class but BasicStoreSync
      or BasicStoreAsync; these provide the *_bg or non-*_bg sibling
      methods of those described below so that a subclass need only
      implement the synchronous or asynchronous forms. Most local
      Stores will derive from BasicStoreSync and remote Stores
      derive from BasicStoreAsync.

      A subclass should provide thread-safe implementations of the following
      methods:

        .add(block) -> hashcode
        .get(hashcode, [default=None]) -> block (or default)
        .contains(hashcode) -> boolean
        .flush()

      A subclass _may_ provide thread-safe implementations of the following
      methods:

        .hashcodes(starting_hashcode, length) -> iterable-of-hashcodes

      The background (*_bg) functions return cs.later.LateFunction instances
      for deferred collection of the operation result.

      A convenience .lock attribute is provided for simple mutex use.

      The .readonly attribute may be set to prevent writes and trap
      surprises; it relies on assert statements.

      The .writeonly attribute may be set to trap surprises when no blocks
      are expected to be fetched; it relies on asssert statements.

      The mapping special methods __getitem__ and __contains__ call
      the implementation methods .get() and .contains().
  '''

  _seq = Seq()

  def __init__(self, name, capacity=None, hashclass=None, lock=None):
    with Pfx("_BasicStoreCommon.__init__(%s,..)", name):
      if not isinstance(name, str):
        raise TypeError("initial `name` argument must be a str, got %s", type(name))
      if name is None:
        name = "%s%d" % (self.__class__.__name__, next(_BasicStoreCommon._seq()))
      if capacity is None:
        capacity = 4
      if hashclass is None:
        hashclass = DEFAULT_HASHCLASS
      MultiOpenMixin.__init__(self, lock=lock)
      self.name = name
      self.hashclass = hashclass
      self.logfp = None
      self.__funcQ = Later(capacity, name="%s:Later(__funcQ)" % (self.name,)).open()
      self.readonly = False
      self.writeonly = False

  def __str__(self):
    return "%s(%s)" % (self.__class__.__name__, self.name)

  def _defer(self, func, *args, **kwargs):
    return self.__funcQ.defer(func, *args, **kwargs)

  ###################
  ## Special methods.
  ##

  def __contains__(self, h):
    ''' Test if the supplied hashcode is present in the store.
    '''
    return self.contains(h)

  def __iter__(self):
    return self.hashcodes_from()

  def keys(self):
    return iter(self)

  def __getitem__(self, h):
    ''' Return the data bytes associated with the supplied hashcode.
        Raise KeyError if the hashcode is not present.
    '''
    block = self.get(h)
    if block is None:
      raise KeyError("missing hash %r" % (h,))
    return block

  def __enter__(self):
    defaults.pushStore(self)
    return MultiOpenMixin.__enter__(self)

  def __exit__(self, exc_type, exc_value, traceback):
    if exc_value:
      import traceback as TB
      TB.print_tb(traceback, file=sys.stderr)
    defaults.popStore()
    return MultiOpenMixin.__exit__(self, exc_type, exc_value, traceback)

  def __str__(self):
    return "Store(%s)" % self.name

  def hash(self, data):
    ''' Return a Hash object from data bytes.
        NB: does _not_ store the data.
    '''
    return self.hashclass.from_data(data)

  def startup(self):
    # Later already open
    pass

  def shutdown(self):
    ''' Called by final MultiOpenMixin.close().
    '''
    self.__funcQ.close()
    if not self.__funcQ.closed:
      debug("%s.shutdown: __funcQ not closed yet", self)
    self.__funcQ.wait()

  def missing(self, hashes):
    ''' Yield hashcodes that are not in the store from an iterable hash
        code list.
    '''
    for h in hashes:
      if h not in self:
        yield h

  @abstractmethod
  def add(self, data):
    pass

  @abstractmethod
  def add_bg(self, data):
    pass

  @abstractmethod
  def get(self, h):
    pass

  @abstractmethod
  def get_bg(self, h):
    pass

  @abstractmethod
  def contains(self, h):
    pass

  @abstractmethod
  def contains_bg(self, h):
    pass

  @abstractmethod
  def flush(self):
    pass

  @abstractmethod
  def flush_bg(self):
    pass

class BasicStoreSync(_BasicStoreCommon):
  ''' Subclass of _BasicStoreCommon expecting synchronous operations and providing asynchronous hooks, dual of BasicStoreAsync.
  '''

  #####################################
  ## Background versions of operations.
  ##

  def add_bg(self, data):
    return self._defer(self.add, data)

  def get_bg(self, h):
    return self._defer(self.get, h)

  def contains_bg(self, h):
    return self._defer(self.contains, h)

  def flush_bg(self):
    return self._defer(self.flush)

class BasicStoreAsync(_BasicStoreCommon):
  ''' Subclass of _BasicStoreCommon expecting asynchronous operations and providing synchronous hooks, dual of BasicStoreSync.
  '''

  #####################################
  ## Background versions of operations.
  ##

  def add(self, data):
    return self.add_bg(data)()

  def get(self, h):
    return self.get_bg(h)()

  def contains(self, h):
    return self.contains_bg(h)()

  def flush(self):
    return self.flush_bg()()

def Store(store_spec):
  ''' Factory function to return an appropriate BasicStore* subclass
      based on its argument:

        /path/to/store  A DataDirStore directory.

        |command        A subprocess implementing the streaming protocol.

        tcp:[host]:port Connect to a daemon implementing the streaming protocol.

        ssh://host/[store-designator-as-above]

        relative/path/to/store
                        If the string doesn't start with /, | or foo:
                        and specifies a directory then treat like
                        /cwd/relative/path/to/store.
  '''
  assert type(store_spec) is str, "expected a str, got %s" % (store_spec,)
  if store_spec.startswith('/'):
    return Store("file:"+store_spec)
  if store_spec.startswith('|'):
    return Store("exec:"+store_spec)
  if ':' not in store_spec:
    return Store("file:"+store_spec)
  scheme = store_spec[:store_spec.index(':')]
  if not scheme.isalpha():
    return Store("file:"+store_spec)
  spec = store_spec[len(scheme)+1:]
  if scheme == "file":
    # TODO: after tokyocabinet available, probe for index file name
    storepath = os.path.abspath(spec)
    if os.path.isdir(storepath):
      return DataDirStore(spec, os.path.abspath(spec), hashclass=DEFAULT_HASHCLASS)
    raise ValueError("unsupported file store: %s" % (storepath,))
  if scheme == "exec":
    from .stream import StreamStore
    from subprocess import Popen, PIPE
    P = Popen(spec, shell=True, stdin=PIPE, stdout=PIPE)
    return StreamStore("exec:"+spec, P.stdin, P.stdout)
  if scheme == "tcp":
    from .tcp import TCPStoreClient
    host, port = spec.rsplit(':', 1)
    if not host:
      host = '127.0.0.1'
    return TCPStoreClient((host, int(port)))
  if scheme == "ssh":
    # TODO: path to remote vt command
    # TODO: $VT_SSH envvar
    import cs.sh
    from .stream import StreamStore
    from subprocess import Popen, PIPE
    if spec.startswith('//') and not spec.startswith('///'):
      sshto, remotespec = spec[2:].split('/', 1)
      rcmd = './bin/vt -S %s listen -' % (cs.sh.quotestr(remotespec),)
      P = Popen( ['set-x', 'ssh', sshto, 'set -x; '+rcmd],
                 shell=False, stdin=PIPE, stdout=PIPE)
      return StreamStore("ssh:"+spec, P.stdin, P.stdout)
    else:
      raise ValueError("bad spec ssh:%s, expect ssh://target/remote-spec" % (spec,))
  raise ValueError("unsupported store scheme: %s" % (scheme,))

class MappingStore(BasicStoreSync):
  ''' A Store built on an arbitrary mapping object.
  '''

  def __init__(self, name, mapping, **kw):
    BasicStoreSync.__init__(self, "MappingStore(%s)" % (name,), **kw)
    self.mapping = mapping

  def startup(self):
    mapping = self.mapping
    try:
      openmap = mapping.open
    except AttributeError:
      pass
    else:
      openmap()
    BasicStoreSync.startup(self)

  def shutdown(self):
    mapping = self.mapping
    try:
      closemap = mapping.close
    except AttributeError:
      pass
    else:
      closemap()
    BasicStoreSync.shutdown(self)

  def add(self, data):
    with Pfx("add %d bytes", len(data)):
      h = self.hash(data)
      if h not in self.mapping:
        self.mapping[h] = data
      elif False:
        with Pfx("EXISTING HASH"):
          try:
            data2 = self.mapping[h]
          except Exception as e:
            error("fetch FAILED: %s", e)
          else:
            if data != data2:
              warning("data mismatch: .add data=%r, Store data=%r", data, data2)
      return h

  def get(self, h, default=None):
    try:
      data = self.mapping[h]
    except KeyError:
      return default
    return data

  def contains(self, h):
    return h in self.mapping

  def flush(self):
    ''' Call the .flush method of the underlying mapping, if any.
    '''
    map_flush = getattr(self.mapping, 'flush', None)
    if map_flush is not None:
      map_flush()

  def __len__(self):
    return len(self.mapping)

  def __iter__(self):
    ''' Return iterator over the mapping; required for use of HashCodeUtilsMixin.hashcodes_from.
    '''
    return iter(self.mapping)

  def hashcodes_from(self, start_hashcode=None, reverse=False):
    ''' Use the mapping's .hashcodes_from if present, otherwise use HashCodeUtilsMixin.hashcodes_from.
    '''
    try:
      hashcodes_method = self.mapping.hashcodes_from
    except AttributeError:
      return HashCodeUtilsMixin.hashcodes_from(self, start_hashcode=start_hashcode, reverse=reverse)
    return hashcodes_method(start_hashcode=start_hashcode, reverse=reverse)

class DataDirStore(MappingStore):
  ''' A MappingStore using a DataDir as its backend.
  '''

  def __init__(self, name, statedirpath, datadirpath=None, hashclass=None, indexclass=None, rollover=None, **kw):
    if hashclass is None:
      raise ValueError("hashclass is mandatory")
    if indexclass is None:
      indexclass = DEFAULT_INDEXCLASS
    self._datadir = DataDir(statedirpath, datadirpath, hashclass, indexclass, rollover=rollover)
    MappingStore.__init__(self, name, self._datadir, **kw)

  def open(self, **kw):
    self._datadir.open()
    return MappingStore.open(self, **kw)

  def close(self):
    self._datadir.close()
    return MappingStore.close(self)

class _ProgressStoreTemplateMapping(object):

  def __init__(self, PS):
    self.PS = PS

  def __getitem__(self, key):
    try:
      category, aspect = key.rsplit('_', 1)
    except ValueError:
      category = key
      aspect = 'position'
    P = self.PS._progress[category]
    try:
      value = getattr(P, aspect)
    except AttributeError as e:
      raise KeyError("%s: aspect=%r" % (key, aspect))
    return value

class ProgressStore(BasicStoreSync):

  def __init__(self, name, S, template='rq  {requests_all_position}  {requests_all_throughput}/s', **kw):
    lock = kw.pop('lock', None)
    if lock is None:
      lock = S._lock
    BasicStoreSync.__init__(self, "ProgressStore(%s)" % (name,), lock=lock, **kw)
    self.S = S
    self.template = template
    self.template_mapping = _ProgressStoreTemplateMapping(self)
    Ps = {}
    for category in 'add', 'get', 'contains', 'requests', 'add_bytes', 'get_bytes':
      # active actions
      Ps[category] = Progress(name='-'.join((str(S), category)), throughput_window=4)
      # cumulative actions
      Ps[category+'_all'] = Progress(name='-'.join((str(S), category, 'all')), throughput_window=10)
    self._progress = Ps
    self.run = True
    self._last_status = ''
    T = Thread(name='%s-status-line' % (self.S,), target=self._run_status_line)
    T.daemon = True
    T.start()

  def __str__(self):
    return self.status_line()

  def shutdown(self):
    self.run = False
    BasicStoreSync.shutdown(self)

  def status_line(self, template=None):
    if template is None:
      template = self.template
    return template.format_map(self.template_mapping)

  @property
  def requests(self):
    return self._progress['requests'].position

  @requests.setter
  def requests(self, value):
    return self._progress['requests'].update(value)

  @contextmanager
  def do_request(self, category):
    Ps = self._progress
    self.requests += 1
    Ps['requests_all'] += 1
    if category is not None:
      Pactive = self._progress[category]
      Pactive.update(Pactive.position + 1)
      Pall = self._progress[category + '_all']
      Pall.update(Pall.position + 1)
    yield
    if category is not None:
      Pactive.update(Pactive.position - 1)
    self.requests -= 1

  def add(self, data):
    with self.do_request('add'):
      return self.S.add(data)

  def get(self, hashcode):
    with self.do_request('get'):
      return self.S.get(hashcode)

  def contains(self, hashcode):
    with self.do_request('contains'):
      return self.S.contains(hashcode)

  def flush(self):
    with self.do_request(None):
      return self.S.flush()

  def _run_status_line(self):
    while self.run:
      text = self.status_line()
      old_text = self._last_status
      if text != self._last_status:
        self._last_status = text
        status(text)
      sleep(0.25)

if __name__ == '__main__':
  import cs.venti.store_tests
  cs.venti.store_tests.selftest(sys.argv)
