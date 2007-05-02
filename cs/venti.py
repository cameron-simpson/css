#!/usr/bin/python

''' A data store patterned after the Venti scheme:
      http://library.pantek.com/general/plan9.documents/venti/venti.html
    but supporting variable sized blocks.
    See also the Plan 9 Venti support manual pages:
      http://swtch.com/plan9port/man/man7/venti.html
    and the Wikipedia entry:
      http://en.wikipedia.org/wiki/Venti

    TODO:
      Compress the data chunks in the data file.
      MetaFS interface using real files for metadata and their contents
	as hashes.
'''

import os.path
from zlib import compress, decompress
## NOTE: migrate to hashlib sometime when python 2.5 more common
import sha
from cs.misc import warn, progress, verbose
import cs.hier
from cs.lex import unctrl

HASH_SIZE=20    # size of SHA-1 hash
MAX_BLOCKSIZE=65536
MAX_SUBBLOCKS=MAX_BLOCKSIZE/(HASH_SIZE+2)

def hash_sha(block):
  ''' Returns the SHA-1 checksum for the supplied block.
  '''
  hash=sha.new(block)
  return hash.digest()

def unhex(hexstr):
  ''' Return raw byte array from hexadecimal string.
  '''
  return "".join([chr(int(hexstr[i:i+2],16)) for i in range(0,len(hexstr),2)])

def genHex(data):
  for c in data:
    yield '%02x'%ord(c)

def hex(data):
  return "".join(genHex(data))

def writehex(fp,data):
  ''' Write data in hex to file.
  '''
  for w in genHex(data):
    fp.write(w)

class RawStore(dict):
  def __init__(self,path):
    dict.__init__(self)
    self.__path=path
    progress("indexing", path)
    self.__fp=open(path,"a+b")
    self.loadIndex()

  def __setitem__(self,key,value):
    raise IndexError

  def __getitem__(self,key):
    v=dict.__getitem__(self,key)
    self.__fp.seek(v[0])
    return decompress(self.__fp.read(v[1]))

  def hash(self,block):
    ''' Compute the hash for a block.
    '''
    return hash_sha(block)

  def store(self,block):
    h=self.hash(block)
    if h in self:
      verbose(self.__path,"already contains",hex(h))
    else:
      zblock=compress(block)
      self.__fp.seek(0,2)
      self.__fp.write(str(len(zblock)))
      self.__fp.write("\n")
      offset=self.__fp.tell()
      self.__fp.write(zblock)
      dict.__setitem__(self,h,(offset,len(zblock)))

    return h

  def loadIndex(self):
    fp=self.__fp
    fp.seek(0)
    while True:
      e=self.__loadEntryHead(fp)
      if e is None:
        break
      (offset, zsize)=e
      zblock=fp.read(zsize)
      block=decompress(zblock)
      h=self.hash(block)
      dict.__setitem__(self,h,(offset,zsize))

  def __loadEntryHead(self,fp,offset=None):
    if offset is not None:
      fp.seek(offset)
    line=fp.readline()
    if len(line) == 0:
      return None
    assert len(line) > 1 and line[-1] == '\n'
    number=line[:-1]
    assert number.isdigit()
    return (fp.tell(), int(number))

  def blocklist(self,h=None):
    return BlockList(self,h)

  def blocksink(self):
    return BlockSink(self)

  def datasink(self):
    return Sink(self)

  def cat(self,h,fp=None):
    if fp is None:
      import sys
      fp=sys.stdout
    for block in self.blocklist(h):
      fp.write(block)

  def storeFile(self,fp):
    sink=self.datasink()
    buf=fp.read()
    while len(buf) > 0:
      sink.write(buf)
      buf=fp.read()
    return sink.close()

MAX_LRU=1024
class LRUCacheStore(RawStore):
  def __init__(self,path,maxCache=None):
    print "new LRUCacheStore"
    if maxCache is None: maxCache=MAX_LRU
    RawStore.__init__(self,path)
    self.__max=maxCache
    self.__index={}
    self.__first=None
    self.__last=None
    self.__len=0

  def store(self,block):
    h=RawStore.store(self,block)
    if h in self.__index:
      self.__hitBlock(h)
    else:
      self.__newBlock(h,block)

    return h

  def __hitBlock(self,h):
    node=self.__index[h]
    prev=node[2]
    if prev is not None:
      # swap with the node to the left
      print "LRU bubble up"
      prev[3]=node[3]
      node[2]=prev[2]
      prev[2]=node
      node[3]=prev

  def __newBlock(self,h,block):
    print "LRU new"
    oldfirst=self.__first
    node=[h,block,None,oldfirst]
    self.__first=node
    if oldfirst is None:
      # first node - is also the last
      self.__last=node
    else:
      # prepend to old first node
      oldfirst[2]=node

    self.__len+=1
    if self.__len > self.__max:
      # remove the last node
      print "LRU pop"
      newlast=self.__last[2]
      newlast[3]=None
      self.__last=newlast

  def __getitem__(self,h):
    if h in self.__index:
      block=self.__index[h][1]
      self.__hitBlock(h)
    else:
      # new block - load it up at the front
      block=RawStore.__getitem__(self,h)
      self.__newBlock(h,block)

    return block

class Store(LRUCacheStore):
  pass

class BlockList:
  def __init__(self,S,h=None):
    self.__store=S
    self.__blocks=[]
    if h is not None:
      iblock=S[h]
      while len(iblock) > 0:
        isIndirect=bool(ord(iblock[0]))
        hlen=ord(iblock[1])
        h=iblock[2:2+hlen]
        self.append(h,isIndirect)
        iblock=iblock[2+hlen:]

  def __len__(self):
    return len(self.__blocks)

  def append(self,h,isIndirect):
    self.__blocks.append((isIndirect,h))

  def __getitem__(self,i):
    return self.__blocks[i]

  def pack(self):
    return "".join( [ chr(int(sb[0]))+chr(len(sb[1]))+sb[1] 
                      for sb in self.__blocks
                    ]
                  )

  def __iter__(self):
    S=self.__store
    for (isIndirect,h) in self.__blocks:
      if isIndirect:
        for subblock in BlockList(S,h):
          yield subblock
      else:
        yield S[h]

class BlockSink:
  def __init__(self,S):
    self.__store=S
    self.__lists=[BlockList(S)]
  
  def append(self,block,isIndirect=False):
    S=self.__store
    level=0
    h0=h=S.store(block)
    while True:
      bl=self.__lists[level]
      ##print "append: bl =", `bl`
      if len(bl) < MAX_SUBBLOCKS:
        bl.append(h,isIndirect)
        return h0

      # pack up full block for storage at next indirection level
      fullblock=bl.pack()
      # prepare fresh empty block at this level
      bl=self.__lists[level]=BlockList(S)
      # store the current block in the fresh indirect block
      bl.append(h,isIndirect)
      # advance to the next level of indirection and repeat
      # to store the full indirect block
      level+=1
      isIndirect=True
      block=fullblock
      h=S.store(block)
      if level == len(self.__lists):
        print "new indir level", level
        self.__lists.append(BlockList(S))

  def close(self):
    ''' Store all the outstanding indirect blocks.
        Return the hash of the top level block.
    '''
    S=self.__store
    # record the lower level indirect blocks in their parents
    while len(self.__lists) > 1:
      bl=self.__lists.pop(0)
      self.append(bl.pack(),True)

    # stash and return the topmost indirect block
    h=S.store(self.__lists[0].pack())
    self.__lists=None
    return h

class ManualDataSink:
  ''' A File-like class that supplies only write, close, flush.
      Returned by the sink() method of Store.
      Write(), close() and flush() return the hash code for flushed data.
      Write() usually returns None unless too much is queued; then it
      flushes some data and returns the hash.
      Flush() may return None if there is no queued data.
  '''
  def __init__(self,S):
    ''' Arguments: fp, the writable File object to encapsulate.
                   S, a cs.venti.Store compatible object.
    '''
    self.__bsink=S.blocksink()
    self._q=''

  def close(self):
    ''' Close the file. Return the hash code of the unreported data.
    '''
    self.flush()
    return self.__bsink.close()

  def write(self,data):
    ''' Queue data for writing.
    '''
    self._q+=data

    # flush complete blocks to the store
    edgendx=self.findEdge()
    while edgendx > 0:
      self.__bsink.append(self._q[:edgendx])
      self._q=self._q[edgendx:]
      edgendx=self.findEdge()

  def findEdge(self):
    ''' Return offset of next block start, if present.
    '''
    global MAX_BLOCKSIZE
    if len(self._q) >= MAX_BLOCKSIZE:
      return MAX_BLOCKSIZE
    return 0

  def flush(self):
    ''' Like flush(), but returns the hash code of the data since the last
        flush. It may return None if there is nothing to flush.
    '''
    if len(self._q) == 0:
      return None

    h=self.__bsink.append(self._q)
    self._q=''
    return h

# make patch friendly blocks
class CodeDataSink(ManualDataSink):
  def __init__(self,S):
    ManualDataSink.__init__(self,S)

  def findEdge(self):
    q=self._q
    start=0
    found=q.find('\n')
    while found >= 0:
      if found > MAX_BLOCKSIZE:
        return MAX_BLOCKSIZE

      # UNIX mbox file
      # python/perl top level things
      if q[found+1:found:6] == "From " \
      or q[found+1:found+5] == "def " \
      or q[found+1:found+7] == "class " \
      or q[found+1:found+9] == "package ":
        return found+1

      # C/C++/perl etc end of function
      if q[found+1:found+3] == "}\n":
        return found+3

      start=found+1
      found=q.find('\n',start)

    return 0

class Sink(CodeDataSink):
  pass

def fuse(backfs,store):
  ''' Run a FUSE filesystem with the specified basefs backing store
      and Venti storage.
      This is a separate function to defer the imports.
  '''

  from fuse import Fuse
  from errno import ENOSYS
  class FuseStore(Fuse):
    def __init__(self, *args, **kw):
      Fuse.__init__(self, *args, **kw)

      # TODO: get this from kw?
      backfs='/home/cameron/tmp/venti/fsdir'
      store='/home/cameron/tmp/venti/store'

      import os.path
      assert os.path.isdir(backfs)
      self.__backfs=backfs

      if type(store) is str:
        store=Store(store)
      self.__store=store

      self.file_class=self.__File

    def __abs(self, path):
      return os.path.join(self.__backfs, path)

    def getattr(self,path):
      return os.lstat(self.__abs(path))
    def readlink(self, path):
      return os.readlink(self.__abs(path))
    def readdir(self, path, offset):
      for e in os.listdir(self.__abs(path)):
        yield fuse.Direntry(e)
    def unlink(self, path):
      os.unlink(self.__abs(path))
    def rmdir(self, path):
      os.rmdir(self.__abs(path))
    def symlink(self, path, path1):
      os.symlink(path, self.__abs(path1))
    def rename(self, path, path1):
      os.rename(self.__abs(path), self.__abs(path1))
    def link(self, path, path1):
      os.link(self.__abs(path), self.__abs(path1))
    def chmod(self, path, mode):
      os.chmod(self.__abs(path), mode)
    def chown(self, path, user, group):
      os.chown(self.__abs(path), user, group)
    def truncate(self, path, len):
      return -ENOSYS
    def mknod(self, path, mode, dev):
      os.mknod(self.__abs(path), mode, dev)
    def mkdir(self, path, mode):
      os.mkdir(self.__abs(path), mode)
    def utime(self, path, times):
      os.utime(self.__abs(path), times)
    def access(self, path, mode):
      if not os.access(self.__abs(path), mode):
        return -EACCES
    def statfs(self):
      return os.statvfs(self.__basefs)

    class __File(object):
      def __init__(self, path, flags, *mode):
        if flags == os.O_RDONLY:
          
          self.file = os.fdopen(os.open("." + path, flags, *mode),
                                flag2mode(flags))
          self.fd = self.file.fileno()

      def read(self, length, offset):
          self.file.seek(offset)
          return self.file.read(length)

      def write(self, buf, offset):
          self.file.seek(offset)
          self.file.write(buf)
          return len(buf)

      def release(self, flags):
          self.file.close()

      def fsync(self, isfsyncfile):
          if isfsyncfile and hasattr(os, 'fdatasync'):
              os.fdatasync(self.fd)
          else:
              os.fsync(self.fd)

      def flush(self):
          self.file.flush()
          # cf. xmp_flush() in fusexmp_fh.c
          os.close(os.dup(self.fd))

      def fgetattr(self):
          return os.fstat(self.fd)

      def ftruncate(self, len):
          self.file.truncate(len)

  def main(self, *a, **kw):

      self.file_class = self.XmpFile

      return Fuse.main(self, *a, **kw)

return FuseStore(backfs,store)

def main():

  usage = """
Userspace nullfs-alike: mirror the filesystem tree from some point on.

""" + Fuse.fusage

  server = Xmp(version="%prog " + fuse.__version__,
               usage=usage,
               dash_s_do='setsingle')

  server.parser.add_option(mountopt="root", metavar="PATH", default='/',
                           help="mirror filesystem from under PATH [default: %default]")
  server.parse(values=server, errex=1)

  try:
      if server.fuse_args.mount_expected():
          os.chdir(server.root)
  except OSError:
      print >> sys.stderr, "can't enter root of underlying filesystem"
      sys.exit(1)

  server.main()


if __name__ == '__main__':
  main()
