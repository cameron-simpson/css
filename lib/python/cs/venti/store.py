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
from thread import allocate_lock
from threading import Thread
from Queue import Queue
from cs.later import Later, report as reportLFs
from cs.logutils import info, debug, warning, Pfx, D
from cs.serialise import toBS, fromBS
from cs.threads import Q1, Get1, NestingOpenClose
from . import defaults, totext
from .hash import Hash_SHA1

class BasicStore(NestingOpenClose):
  ''' Core functions provided by all Stores.

      A subclass should provide thread-safe implementations of the following
      methods:

        .add(block) -> hashcode
        .get(hashcode, [default=None]) -> block (or default)
        .contains(hashcode) -> boolean
        .sync()

      A convenience .lock attribute is provided for simple mutex use.

      The .readonly attribute may be set to prevent writes and trap
      surprises; it relies on assert statements.

      The .writeonly attribute may be set to trap surprises when no blocks
      are expected to be fetched; it relies on asssert statements.


      The background (*_bg) functions return cs.later.LateFunction instances
      for deferred collection of the operation result.

      The mapping special methods __getitem__ and __contains__ call
      the implementation methods .get() and .contains().

      [ TODO: NO LONGER IMPLEMENTED, BUT IT SHOULD BE ]
      The hint noFlush, if specified and True, suggests that streaming
      store connections need not flush the request stream because another
      request will follow very soon after this request. This allows
      for more efficient use of streams. Users who set this hint to True
      must ensure that a "normal" flushing request, or a call of the
      ._flush() method, follows any noFlush requests promptly otherwise
      deadlocks may ensue.
  '''
  def __init__(self, name, capacity=None):
    with Pfx("BasicStore(%s,..)" % (name,)):
      if capacity is None:
        capacity = 1
      NestingOpenClose.__init__(self)
      self.name = name
      self.logfp = None
      self.__funcQ = Later(capacity)
      self.hashclass = Hash_SHA1
      self._lock = allocate_lock()
      self.readonly = False
      self.writeonly = False

  def add(self, data):
    ''' Add the supplied data bytes to the store.
    '''
    raise NotImplementedError

  def get(self, h, default=None):
    ''' Return the data bytes associated with the supplied hashcode.
        Return None if the hashcode is not present.
    '''
    raise NotImplementedError

  def contains(self, h):
    raise NotImplementedError

  def flush(self):
    ''' Flush outstanding I/O operations on the store.
        This is generally discouraged because it causes less efficient
        operation but it is sometimes necessary, for example at shutdown or
        after *_bg() calls with the noFlush=True hint.
        This does not imply that outstanding transactions have completed,
        merely that they have been dispatched, for example sent down the
        stream of a StreamStore.
        See the sync() call for transaction completion.
    '''
    raise NotImplementedError

  def sync(self):
    ''' Flush outstanding I/O operations on the store and wait for completion.
    '''
    raise NotImplementedError

  #####################################
  ## Background versions of operations.
  ##

  def add_bg(self, data):
    return self._defer(self.add, data)

  def get_bg(self, h):
    return self._defer(self.get, h)

  def contains_bg(self, h):
    return self._defer(self.contains, h)

  def sync_bg(self):
    return self._defer(self.sync)

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
      raise KeyError
    return block

  def __enter__(self):
    NestingOpenClose.__enter__(self)
    defaults.pushStore(self)

  def __exit__(self, exc_type, exc_value, traceback):
    defaults.popStore()
    return NestingOpenClose.__exit__(self, exc_type, exc_value, traceback)

  def __str__(self):
    return "Store(%s)" % self.name

  def hash(self, data):
    ''' Return a Hash object from data bytes.
    '''
    return self.hashclass.fromData(data)

  def keys(self):
    ''' For a big store this is almost certainly unreasonable.
    '''
    raise NotImplementedError

  def shutdown(self):
    ''' Called by final NestingOpenClose.close().
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

def Store(store_spec):
  ''' Factory function to return an appropriate BasicStore subclass
      based on its argument:
        /path/to/store  A GDBMStore directory (later, tokyocabinet etc)
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
      from .gdbmstore import GDBMStore
      return GDBMStore(os.path.abspath(spec))
    if storepath.endswith('.kch'):
      from .kyotostore import KyotoCabinetStore
      return KyotoCabinetStore(storepath)
    raise ValueError("unsupported file store: %s" % (storepath,))
  if scheme == "exec":
    from .stream import StreamStore
    from subprocess import Popen, PIPE
    P = Popen(spec, shell=True, stdin=PIPE, stdout=PIPE)
    return StreamStore("exec:"+spec, P.stdin, P.stdout)
  if scheme == "tcp":
    from .tcp import TCPStore
    host, port = spec.rsplit(':', 1)
    if len(host) == 0:
      host = '127.0.0.1'
    return TCPStore((host, int(port)))
  if sheme == "ssh":
    # TODO: path to remote vt command
    # TODO: $VT_SSH envvar
    import cs.sh
    from .stream import StreamStore
    from subprocess import Popen, PIPE
    assert spec.startswith('//') and not spec.startswith('///'), \
           "bad spec ssh:%s, expect ssh://target/remote-spec" % (spec,)
    sshto, remotespec = spec[2:].split('/', 1)
    rcmd = './bin/vt -S %s listen -' % cs.sh.quotestr(remotespec)
    P = Popen( ['set-x', 'ssh', sshto, 'set -x; '+rcmd],
               shell=False, stdin=PIPE, stdout=PIPE)
    return StreamStore("ssh:"+spec, P.stdin, P.stdout)
  raise ValueError("unsupported store scheme: %s" % (scheme,))

def pullFromSerial(S1, S2):
  asked = 0
  for h in S2.keys():
    asked += 1
    info("%d %s" % (asked, totext(h)))
    if not S1.contains(h):
      S1.store(S2.fetch(h))

class MappingStore(BasicStore):
  ''' A Store built on an arbitrary mapping object.
  '''

  def __init__(self, mapping, name=None, capacity=None):
    if name is None:
      name = "MappingStore(%s)" % (mapping,)
    BasicStore.__init__(self, name, capacity=capacity)
    self.mapping = mapping

  def add(self, block):
    h = self.hash(block)
    self.mapping[h] = block
    return h

  def get(h, default=None):
    return self.mapping.get(h, default)

  def contains(self, h):
    return h in self.mapping

  def sync(self):
    debug("%s: sync() is a no-op", self)

def GDBMStore(dir):
  from .datafile import GDBMDataDir
  return MappingStore(GDBMDataDr(dir))

def KyotoStore(dir):
  from .datafile import KyotoDataDir
  return MappingStore(KyotoDataDr(dir))

if __name__ == '__main__':
  import cs.venti.store_tests
  cs.venti.store_tests.selftest(sys.argv)
