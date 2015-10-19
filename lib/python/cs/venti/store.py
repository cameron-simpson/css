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
from binascii import hexlify
import os
import os.path
import sys
from threading import Lock, RLock
from threading import Thread
from cs.py3 import Queue
from cs.asynchron import report as reportLFs
from cs.later import Later
from cs.logutils import info, debug, warning, Pfx, D, X
from cs.resources import MultiOpenMixin
from cs.threads import Q1, Get1
from . import defaults, totext
from .datafile import DataDirMapping
from .hash import Hash_SHA1, HashCodeUtilsMixin

class _BasicStoreCommon(MultiOpenMixin, HashCodeUtilsMixin):
  ''' Core functions provided by all Stores.

      A subclass should provide thread-safe implementations of the following
      methods:

        .add(block) -> hashcode
        .get(hashcode, [default=None]) -> block (or default)
        .contains(hashcode) -> boolean
        .flush()

      A convenience .lock attribute is provided for simple mutex use.

      The .readonly attribute may be set to prevent writes and trap
      surprises; it relies on assert statements.

      The .writeonly attribute may be set to trap surprises when no blocks
      are expected to be fetched; it relies on asssert statements.

      The background (*_bg) functions return cs.later.LateFunction instances
      for deferred collection of the operation result.

      The mapping special methods __getitem__ and __contains__ call
      the implementation methods .get() and .contains().
  '''

  def __init__(self, name, capacity=None):
    with Pfx("_BasicStoreCommon.__init__(%s,..)", name):
      if capacity is None:
        capacity = 1
      MultiOpenMixin.__init__(self)
      self.name = name
      self.logfp = None
      self.__funcQ = Later(capacity, name="%s:Later(__funcQ)" % (self.name,)).open()
      self.hashclass = Hash_SHA1
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
    '''
    return self.hashclass.from_data(data)

  def keys(self):
    ''' For a big store this is almost certainly unreasonable.
    '''
    raise NotImplementedError

  def startup(self):
    self.__funcQ.open()

  def shutdown(self):
    ''' Called by final MultiOpenMixin.close().
    '''
    self.__funcQ.close()

  def missing(self, hashes):
    ''' Yield hashcodes that are not in the store from an iterable hash
        code list.
    '''
    for h in hashes:
      if h not in self:
        yield h

  def prefetch(self, hashes):
    ''' Prefetch the blocks associated with hs, an iterable returning hashes.
        This is intended to hint that these blocks will be wanted soon,
        and so implementors might queue the fetches on an "idle" queue so as
        not to penalise other store users.
        This default implementation does nothing, which may be perfectly
        legitimate for some stores.
    '''
    pass

  def multifetch(self, hs, ordered=False):
    ''' Generator yielding:
          hash, data
        for each hash in `hs`.
        If `ordered` is true, yield data in the order of `hs`
        otherwise yield data as it arrives from the Store.
    '''
    LFs = []
    for h in hs:
      LF = self.fetch_bg(h)
      h2LF[h] = self.fetch_bg(h)
      LF2h[LF] = h
    if ordered:
      for h in hs:
        yield h, h2LF[h]()
    else:
      for LF in reportLFs(LF2h.keys()):
        yield LF2h[LF], LF()

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
      return DataDirStore(os.path.abspath(spec))
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

  def __init__(self, mapping, name=None, capacity=None):
    if name is None:
      name = "MappingStore(%s)" % (type(mapping),)
    BasicStoreSync.__init__(self, name, capacity=capacity)
    self.mapping = mapping

  def add(self, data):
    with Pfx("add %d bytes", len(data)):
      h = self.hash(data)
      if h not in self.mapping:
        info("NEW, save with hashcode=%s", h)
        self.mapping[h] = data
      else:
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

  def first(self, hashclass=None):
    ''' Return the first hashcode in the Store or None if empty.
        `hashclass`: specify the hashcode type, default from defaults.S
    '''
    return self.mapping.first(hashclass=hashclass)

  def hashcodes(self, hashclass=None, hashcode=None, reverse=None, after=False, length=None):
    ''' Generator yielding the Store's in order hashcodes starting with optional `hashcode`.
        `hashclass`: specify the hashcode type, default from defaults.S
        `hashcode`: the first hashcode; if missing or None, iteration
                    starts with the first key in the index
        `reverse`: iterate backwards if true, forwards if false and in no
                   specified order if missing or None
        `after`: commence iteration after the first hashcode
        `length`: if not None, the maximum number if hashcodes to yield
    '''
    return self.mapping.hashcodes(hashclass=hashclass, hashcode=hashcode,
                                  reverse=reverse, after=after, length=length)

def DataDirStore(dirpath, indexclass=None, rollover=None):
  return MappingStore(DataDirMapping(dirpath, indexclass=indexclass, rollover=rollover))

if __name__ == '__main__':
  import cs.venti.store_tests
  cs.venti.store_tests.selftest(sys.argv)
