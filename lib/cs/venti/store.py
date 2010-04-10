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
import sys
import os
import os.path
import time
from thread import allocate_lock
import threading
from Queue import Queue
from zlib import compress, decompress
from cs.logutils import debug
from cs.misc import out, tb, seq, Loggable
from cs.serialise import toBS, fromBS, fromBSfp
from cs.threads import FuncMultiQueue, Q1, DictMonitor, NestingOpenClose
from cs.venti import tohex, defaults
from cs.venti.block import Block
from cs.venti.hash import Hash_SHA1
from cs.upd import out, nl

class BasicStore(Loggable, NestingOpenClose):
  ''' Core functions provided by all Stores.

      A subclass should provide thread-safe implementations of the following
      methods:
        __contains__(sel, hashcode) -> Boolean
        __getitem__(self, hashcode) -> data
        add(self, data) -> hashcode
        sync(self)

      A convenience .lock attribute is provided for simple mutex use.

      The .readonly attribute may be set to prevent writes and trap
      surprises; it relies on assert statements.

      The .writeonly attribute may be set to trap surprises when no blocks
      are expected to be fetched; it relies on asssert statements.

      The following "op" operation methods are provided:
        contains(hashcode) -> Boolean
        contains_bg(hashcode[, ch]) -> (tag, ch)
        get(hashcode) -> data
          Unlike __getitem__, .get() returns None if the hashcode does not
          resolve.
        get_bg(hashcode[, ch]) -> (tag, ch)
        add_bg(data[, ch]) -> (tag, ch)

      The non-_bg forms are equivalent to __contains__, __getitem__ and add()
      and are provided for consistency with the _bg forms.

      The _bg forms accept an optional 'ch' parameter and return a (tag, ch)
      pair. If 'ch' is not provided or None, a single use Channel is obtained.
      A .get() from the returned channel returns the tag and the function result.

      In normal use the caller will care only about the channel or the tag,
      rarely both. If no channel is presupplied then the return channel is
      a single use channel on which only the relevant (tag, result) response
      will be seen, so the tag is superfluous. In the case where a channel is
      presupplied it is possible for responses to requests to arrive in
      arbitrary order, so the tag is needed to identify the response with the
      calling request; however the caller already knows the channel.

      The hint noFlush, if specified and True, suggests that streaming
      store connections need not flush the request stream because another
      request will follow very soon after this request. This allows
      for more efficient use of streams. Users who set this hint to True
      must ensure that a "normal" flushing request, or a call of the
      ._flush() method, follows any noFlush requests promptly otherwise
      deadlocks may ensue.
  '''
  def __init__(self, name, capacity=None):
    debug("BasicStore.__init__...")
    if capacity is None:
      capacity = 1
    Loggable.__init__(self, name)
    NestingOpenClose.__init__(self)
    self.name=name
    self.logfp=None
    self.__funcQ=FuncMultiQueue(capacity)
    self.hashclass = Hash_SHA1
    self.lock = allocate_lock()
    self.readonly = False
    self.writeonly = False

  def hash(self, data):
    return self.hashclass.fromData(data)

  def __contains__(self, h):
    ''' Test if the supplied hashcode is present in the store.
    '''
    raise NotImplementedError
  def __getitem__(self, h):
    ''' Return the data block associated with the supplied hashcode.
        Raise KeyError if the hashcode is not present.
    '''
    raise NotImplementedError
  def add(self, data):
    ''' Add the supplied data block to the store.
    '''
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

  def __enter__(self):
    NestingOpenClose.__enter__(self)
    defaults.pushStore(self)

  def __exit__(self, exc_type, exc_value, traceback):
    defaults.popStore()
    return NestingOpenClose.__exit__(self, exc_type, exc_value, traceback)

  def __str__(self):
    return "Store(%s)" % self.name

  def keys(self):
    return ()

  def shutdown(self):
    ''' Called by final NestingOpenClose.close().
    '''
    self.sync()
    self.__funcQ.close()

  def contains(self, h):
    return self.__contains__(h)

  def contains_bg(self, h, ch=None):
    if ch is None: ch = Q1()
    tag = self.__funcQ.qbgcall(ch, self.contains, h)
    return tag, ch

  def get(self, h):
    ''' Return the data block associated with the supplied hashcode.
        Return None if the hashcode is not present.
    '''
    try:
      data = self[h]
    except KeyError:
      return None
    return data

  def get_bg(self, h, ch=None):
    if ch is None: ch = Q1()
    tag = self.__funcQ.qbgcall(ch, self.get, h)
    return tag, ch

  def add_bg(self, data, ch=None):
    if ch is None: ch = Q1()
    tag = self.__funcQ.qbgcall(ch, self.add, data)
    return tag, ch

  def sync_bg(self, ch=None):
    if ch is None: ch = Q1()
    tag = self.__funcQ.qbgcall(ch, self.sync)
    return tag, ch

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

  def multifetch(self,hs):
    ''' Generator returning a bunch of blocks in sequence corresponding to
        the iterable hashes.
        TODO: record to just use the normal funcQ and a heap of (index, data).
    '''
    # dispatch a thread to request the blocks
    tagQ=Queue(0)       # the thread echoes tags for eash hash in hs
    FQ=Queue(0)         # and returns (tag,block) on FQ, possibly out of order
    Thread(target=self.__multifetch_rq,args=(hs,tagQ,FQ)).start()

    waiting={}  # map of blocks that arrived out of order
    frontTag=None
    while True:
      if frontTag is not None:
        # we're waiting for a particular tag
        tag, block = FQ.get()
        if tag == frontTag:
          # it is the one desired, return it
          yield block
          frontTag = None
        else:
          # not what we wanted - save it for later
          waiting[tag]=block
      # get the next desired tag whose block has not yet arrived
      while frontTag is None:
        # get the next desired tag
        frontTag = tagQ.get()
        if frontTag is None:
          # end of tag stream
          break
        if frontTag in waiting:
          # has this tag already arrived?
          yield waiting.pop(frontTag)
          frontTag = None
    assert len(waiting.keys()) == 0

  def __multifetch_rq(self,hs,tagQ,FQ):
    h0=None
    for h in hs:
      tag, ch = self._tagch(FQ)
      self.fetch_bg(h,noFlush=True,ch=FQ)
      tagQ.put(tag)
      h0=h
    if h0 is not None:
      # dummy request to flush stream
      self.haveyou_bg(h0,ch=Get1())
    tagQ.put(None)

def Store(S):
  ''' Factory function to return an appropriate BasicStore subclass
      based on its argument.
  '''
  assert type(S) is str, "expected a str, got %s" % (S,)
  if S[0] == '/':
    # TODO: after tokyocabinet available, probe for index file name
    from cs.venti.gdbmstore import GDBMStore
    return GDBMStore(S)
  if S[0] == '|':
    # TODO: recode to use the subprocess module
    toChild, fromChild = os.popen2(S[1:])
    from cs.venti.stream import StreamStore
    return StreamStore(S,toChild,fromChild)
  if S.startswith("tcp:"):
    from cs.venti.tcp import TCPStore
    host, port = S[4:].rsplit(':',1)
    if len(host) == 0:
      host = '127.0.0.1'
    return TCPStore((host, int(port)))
  assert False, "unhandled Store name \"%s\"" % (S,)

def pullFromSerial(S1, S2):
  asked = 0
  for h in S2.keys():
    asked+=1
    out("%d %s" % (asked,tohex(h)))
    if not S1.haveyou(h):
      S1.store(S2.fetch(h))

def pullFrom(S1,S2):
  haveyou_ch = Queue(size=256)
  fetch_ch = Queue(size=256)
  pending = DictMonitor()
  watcher = Thread(target=_pullWatcher,args=(S1,S2,haveyou_ch,pending,fetch_ch))
  watcher.start()
  fetcher = Thread(target=_pullFetcher,args=(S1,fetch_ch))
  fetcher.start()
  asked = 0
  for h in S2.keys():
    asked+=1
    out("%d %s" % (asked, tohex(h)))
    tag = seq()
    pending[tag] = h
    S1.haveyou_ch(h,haveyou_ch,tag)
  nl('draining haveyou queue...')
  haveyou_ch.put((None,asked))
  watcher.join()
  nl('draining fetch queue...')
  fetcher.join()
  out('')

F_COMPRESSED = 0x01

def blockdataFromFileStoreFP(fp):
  ''' Read a block of data from a store file.
  '''
  flags = fromBSfp(fp)
  dsize = fromBSfp(fp)
  if dsize == 0:
    data = ''
  else:
    assert dsize > 0, "expected dsize > 0, got dsize=%s" % (dsize,)
    data = fp.read(dsize)
  assert len(data) == dsize
  if flags & F_COMPRESSED:
    data = decompress(data)
  assert (flags & ~F_COMPRESSED) == 0
  return data

def saveBlockData(fp, data):
  ''' Write a data block to a store file.
  '''
  flags = 0
  zdata = compress(data)
  if len(zdata) < len(data):
    flags |= F_COMPRESSED
    data = zdata
  fp.write(toBS(flags))
  fp.write(toBS(len(data)))
  fp.write(data)

class IndexedFileStore(BasicStore):
  ''' A file-based Store which keeps data blocks in flat files, compressed.
      Subclasses must implement the method ._getIndex() to obtain the
      associated index object (for examine, gdbm files) to the object.
  '''
  def __init__(self, dir, capacity=None):
    ''' Initialise this IndexedFileStore.
        'dir' specifies the directory in which the files and their index live.
    '''
    debug("IndexedStore.__init__...")
    BasicStore.__init__(self, dir, capacity=capacity)
    self.dir = dir
    self.savefile = None
    self.index = self._getIndex()
    self.readfiles = {}
    self.storeData = self.__loadStoreMap()
    self.added = 0

  def _getIndex(self): raise NotImplementedError

  def __loadStoreMap(self):
    M = {}
    for name in os.listdir(self.dir):
      if name.endswith('.vtd'):
        pfx = name[:-4]
        if pfx.isdigit():
          pfxn = int(pfx)
          if str(pfxn) == pfx:
            M[pfxn] = name
    return M

  def __makeNewFile(self):
    mapkeys = self.storeData.keys()
    if mapkeys:
      n = max(mapkeys)
    else:
      n = 0
    n += 1
    while True:
      pathname = os.path.join(self.dir, "%d.vtd" % (n,))
      if not os.path.exists(pathname):
        open(os.path.join(self.dir, pathname), "ab").close()
        self.storeData[n] = pathname
        return n
      n += 1

  def _open_n(self, n, mode):
    ''' Open the data file numbered 'n' in the specified mode.
    '''
    pathname = os.path.join(self.dir, "%d.vtd" % (n,))
    debug("open(%s, %s)", pathname, mode)
    return open(pathname, mode)

  def _savefile(self):
    with self.lock:
      if self.savefile is None:
        mapkeys = self.storeData.keys()
        if mapkeys:
          n = max(mapkeys)
        else:
          n = self.__makeNewFile()
        self.savefile = self._open_n(n, "a+b")
        self.savefile_n = n
    return self.savefile

  def _readfile(self, n):
    with self.lock:
      if n not in self.readfiles:
        self.readfiles[n] = self._open_n(n, "rb")
      return self.readfiles[n]

  def __contains__(self, h):
    with self.lock:
      return h in self.index

  def __getitem__(self, h):
    n, offset = self.decodeIndexEntry(self.index[h])
    assert n >= 0
    assert offset >= 0
    rf = self._readfile(n)
    with self.lock:
      rf.seek(offset)
      data = blockdataFromFileStoreFP(rf)
    return data

  def addBlock(self, block, noFlush=False):
    return self.add(block.blockdata(), noFlush=noFlush)

  def add(self, data, noFlush=False):
    assert type(data) is str, "expected str, got %s" % (`data`,)
    assert not self.readonly
    h = self.hash(data)
    sf = self._savefile()
    if h not in self:
      with self.lock:
        sf.seek(0, 2)
        offset = sf.tell()
        saveBlockData(sf, data)
        if not noFlush:
          pass ## sf.flush()
      self.index[h] = self.encodeIndexEntry(self.savefile_n, offset)
      self.added += 1
    return h

  def flush(self):
    if self.savefile:
      self.savefile.flush()
      self.index.flush()

  def sync(self):
    self.flush()
    self.index.sync()

  def decodeIndexEntry(self, noz):
    n, noz = fromBS(noz)
    offset, noz = fromBS(noz)
    assert len(noz) == 0
    return n, offset

  def encodeIndexEntry(self, n, offset):
    return toBS(n) + toBS(offset)

def _pullWatcher(S1,S2,ch,pending,fetch_ch):
  closing = False
  answered = 0
  fetches = 0
  while not closing or asked > answered:
    tag, yesno = ch.get()
    if tag is None:
      asked = yesno
      closing = True
      continue
    answered+=1
    if closing:
      left = asked-answered
      if left % 10 == 0:
        out(str(left))
    h = pending[tag]
    if not yesno:
      fetches+=1
      S2.fetch_ch(h,fetch_ch)
    del pending[tag]
  fetch_ch.put((None,fetches))

def _pullFetcher(S1,ch):
  closing = False
  fetched = 0
  while not closing or fetches > fetched:
    tag, block = ch.get()
    if tag is None:
      fetches = block
      closing = True
      continue
    if closing:
      left = fetches-fetched
      if left % 10 == 0:
        out(str(left))
    fetched+=1
    h = S1.store(block)
