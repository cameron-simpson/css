#!/usr/bin/python

''' A data store after the style of the Venti scheme:
      http://library.pantek.com/general/plan9.documents/venti/venti.html
    but supporting variable sized blocks.
    See also the Plan 9 Venti support manual pages:
      http://swtch.com/plan9port/man/man7/venti.html
    and the Wikipedia entry:
      http://en.wikipedia.org/wiki/Venti

    TODO: rolling hash
            augment with assorted recognition strings by hash
            pushable parser for nested data
          optional compression in store
          extend directory blockref:
            flags:
              1: indirect
              2: inode ref
          inode chunk:
            flags
            [meta] (if flags&0x01)
            blockref
          generic LRUCache class
          caching store - fetch&store locally
          store priority queue - tuples=pool
          remote store: http? multifetch? udp?
          argument order: always ({h|block}, indirect, span)
'''

import os
import os.path
from zlib import compress, decompress
## NOTE: migrate to hashlib sometime when python 2.5 more common
import sha
from cs.misc import cmderr, warn, progress, verbose, fromBS, toBS, fromBSfp
import cs.hier
from cs.lex import unctrl

HASH_SIZE=20                                    # size of SHA-1 hash
MAX_BLOCKSIZE=16383                             # fits in 2 octets BS-encoded
MAX_SUBBLOCKS=MAX_BLOCKSIZE/(HASH_SIZE+4)       # flags(1)+span(2)+hlen(1)+hash

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
    self.__loadIndex()

  def __setitem__(self,h,value):
    raise IndexError

  def __getitem__(self,h):
    ''' Return block for hash, or raise IndexError if not in store.
    '''
    v=dict.__getitem__(self,h)
    self.__fp.seek(v[0])
    return decompress(self.__fp.read(v[1]))

  def hash(self,block):
    ''' Compute the hash for a block.
    '''
    return hash_sha(block)

  def store(self,block):
    ''' Store a block, return the hash.
    '''
    h=self.hash(block)
    if h in self:
      verbose(self.__path,"already contains",hex(h))
    else:
      zblock=compress(block)
      self.__fp.seek(0,2)
      self.__fp.write(toBS(len(zblock)))
      offset=self.__fp.tell()
      self.__fp.write(zblock)
      dict.__setitem__(self,h,(offset,len(zblock)))

    return h

  def fetch(self,h):
    ''' Return block for hash, or None if not present in store.
    '''
    if h not in self:
      return None
    return self[h]

  def __loadIndex(self):
    fp=self.__fp
    fp.seek(0)
    while True:
      zsize=fromBSfp(fp)
      if zsize is None:
        break
      offset=fp.tell()
      zblock=fp.read(zsize)
      block=decompress(zblock)
      h=self.hash(block)
      dict.__setitem__(self,h,(offset,zsize))

  def cat(self,bref,fp=None):
    if type(bref) is str:
      bref=str2BlockRef(bref)
    if fp is None:
      import sys
      fp=sys.stdout
    for b in bref.leaves(self):
      fp.write(b)

  def readOpen(self,bref):
    ''' Open a BlockRef for read.
    '''
    if type(bref) is str:
      bref=str2BlockRef(bref)
    return ReadFile(self,bref)

  def writeOpen(self):
    ''' Open a file for write, close returns a BlockRef.
    '''
    return WriteFile(self)

  def storeFile(self,ifp,rsize=8192):
    ofp=self.writeOpen()
    buf=ifp.read(rsize)
    while len(buf) > 0:
      ofp.write(buf)
      buf=ifp.read(rsize)
    return ofp.close()

  def storeDir(self,path):
    subdirs={}
    for (dirpath, dirs, files) in os.walk(path,topdown=False):
      progress("storeDir", dirpath)
      assert dirpath not in subdirs
      D=Dir(self,None)
      for dir in dirs:
        subpath=os.path.join(dirpath,dir)
        subD=subdirs[subpath]
        del subdirs[subpath]
        D.add(dir,subD.sync(),True)
      for subfile in files:
        filepath=os.path.join(dirpath,subfile)
        progress("storeDir: storeFile", filepath)
        try:
          D.add(subfile,self.storeFile(open(filepath)),False)
        except IOError, e:
          cmderr("%s: can't store: %s" % (filepath, `e`))
      subdirs[dirpath]=D

    topdirs=subdirs.keys()
    assert len(topdirs) == 1, "expected one top dir, got "+`topdirs`
    return subdirs[topdirs[0]].sync()

  def opendir(self,dirref):
    ''' Open a BlockRef that refers to a directory.
    '''
    return Dir(self,None,dirref)

  def walk(self,dirref):
    for i in self.opendir(dirref).walk():
      yield i

MAX_LRU=1024
class LRUCacheStore(RawStore):
  ''' A subclass of RawStore that keeps an LRU cache of referenced blocks.
      TODO: Recode to take an arbitrary Store backend.
            This will let me put Store pools behind a single cache.
  '''
  def __init__(self,path,maxCache=None):
    ##print "new LRUCacheStore"
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
      ##print "LRU bubble up"
      prev[3]=node[3]
      node[2]=prev[2]
      prev[2]=node
      node[3]=prev

  def __newBlock(self,h,block):
    ##print "LRU new"
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
      ##print "LRU pop"
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
  ''' Default Store is a RawStore with a cache.
  '''
  pass

def encodeBlockRef(flags,span,h):
    ''' Encode a blockref for storage.
    '''
    return toBS(flags)+toBS(span)+toBS(len(h))+h

def decodeBlockRef(iblock):
  ''' Decode a block ref from an indirect block.
      It consist of:
        flags: BSencoded ordinal
        span:  BSencoded ordinal
        hashlen: BSencoded ordinal
        hash:  raw hash
      Return (BlockRef,unparsed) where unparsed is remaining data.
  '''
  (flags,iblock)=fromBS(iblock)
  indirect=bool(flags&0x01)
  (span,iblock)=fromBS(iblock)
  (hlen,iblock)=fromBS(iblock)
  h=iblock[:hlen]
  iblock=iblock[hlen:]
  print "decode: flags=%x, span=%d, h=%s" % (flags,span,hex(h))
  return (BlockRef(h,indirect,span), iblock)

def str2BlockRef(s):
  (bref,etc)=decodeBlockRef(s)
  assert len(etc) == 0
  return bref

def decodeBlockRefFP(fp):
  ''' Decode a block ref from an indirect block.
      It consist of:
        flags: BSencoded ordinal
        span:  BSencoded ordinal
        hashlen: BSencoded ordinal
        hash:  raw hash
      Return (BlockRef,unparsed) where unparsed is remaining data.
  '''
  flags=fromBSfp(fp)
  indirect=bool(flags&0x01)
  span=fromBSfp(fp)
  hlen=fromBSfp(fp)
  h=fp.read(hlen)
  assert len(h) == hlen
  return BlockRef(h,indirect,span)

class BlockRef:
  ''' A reference to a file consisting of a hash and an indirect flag.
      Tiny files are not indirect, and the hash is the block holding
      their content. For larger files indirect is True and the hash
      refers to their top indirect block.
  '''
  def __init__(self,h,indirect,span):
    self.h=h
    self.indirect=indirect
    self.span=span
  def flags(self):
    return 0x01 & int(self.indirect)
  def __len__(self):
    return self.span
  def blocklist(self,S):
    assert self.indirect
    return BlockList(S,self.h)
  def encode(self):
    ''' Return an encoded BlockRef for storage, inverse of decodeBlockRef().
    '''
    h=self.h
    return encodeBlockRef(self.flags(),self.span,self.h)
  def leaves(self,S):
    if self.indirect:
      for b in self.blocklist(S).leaves():
        yield b
    else:
      yield S[self.h]

class BlockList(list):
  ''' An in-memory BlockList, used to store or retrieve sequences of
      data blocks.
  '''
  def __init__(self,S,h=None):
    ''' Create a new blocklist.
        This will be empty initially unless the hash of a top level
        indirect block is supplied, in which case it is loaded and
        decoded into BlockRefs.
    '''
    self.__store=S
    if h is not None:
      iblock=S[h]
      while len(iblock) > 0:
        (bref, iblock)=decodeBlockRef(iblock)
        self.append(bref)

  def pack(self):
    return "".join([bref.encode() for bref in self])

  def span(self):
    return self.offsetTo(len(self))

  def offsetTo(self,ndx):
    ''' Offset to start of blocks under entry ndx.
    '''
    return sum([bref.span for bref in self])

  def seekToBlock(self,offset):
    ''' Seek to a leaf block.
        Return the block hash and the offset within the block of the target.
        If the seek is beyond the end of the blocklist, return None and the
        remaining offset.
    '''
    for bref in self:
      if offset < bref.span:
        if bref.indirect:
          return bref.blocklist(self.__store).seekToBlock(offset)
        return (bref.h,offset)
      offset-=bref.span
    return (None,offset)

  def leaves(self):
    ''' Iterate over the leaf blocks.
    '''
    S=self.__store
    for bref in self:
      for b in bref.leaves(S):
        yield b

class ReadFile:
  ''' A file interface supporting seek(), read() and tell() methods.
  '''
  def __init__(self,S,bref):
    self.__store=S
    if bref.indirect:
      self.__blist=bref.blocklist(S)
    else:
      self.__blist=BlockList(S)
      self.__blist.append(bref)
    self.__pos=0

  def seek(self,offset,whence=0):
    if whence == 1:
      offset+=self.tell()
    elif whence == 2:
      offset+=self.span()
    self.__pos=offset

  def tell(self):
    return self.__pos

  def readShort(self):
    (h,offset)=self.__blist.seekToBlock(self.tell())
    if h is None:
      return ''
    b=self.__store[h]
    self.__pos+=len(b)-offset
    return b[offset:]

  def read(self,size=None):
    opos=self.__pos
    buf=''
    while size is None or size > 0:
      chunk=self.readShort()
      if len(chunk) == 0:
        break
      if size is None:
        buf+=chunk
      elif size <= len(chunk):
        buf+=chunk[:size]
        size=0
      else:
        buf+=chunk
        size-=len(chunk)

    self.seek(opos+len(buf))
    return buf

  def readline(self,size=None):
    opos=self.__pos
    line=''
    while size is None or size > 0:
      chunk=self.readShort()
      nlndx=chunk.find('\n')
      if nlndx >= 0:
        # there is a NL
        if size is None or nlndx < size:
          # NL in the available chunk
          line+=chunk[:nlndx+1]
        else:
          # NL not available - ergo size not None and inside chunk
          line+=chunk[:size]
        break

      if size is None or size >= len(chunk):
        # we can suck in the whole chunk
        line+=chunk
        if size is not None:
          size-=len(chunk)
      else:
        # take its prefix and quit
        line+=chunk[:size]
        break

    self.seek(opos+len(line))
    return line

  def readlines(self,sizehint=None):
    lines=[]
    byteCount=0
    while sizehint is None or byteCount < sizehint:
      line=self.readline()
      if len(line) == 0:
        break
      lines.append(line)
      byteCount+=len(line)

    return lines

class BlockSink:
  def __init__(self,S):
    self.__store=S
    self.__lists=[BlockList(S)]

  def appendBlock(self,block):
    ''' Append a block to the BlockSink.
    '''
    self.appendBlockRef(BlockRef(self.__store.store(block),False,len(block)))

  def appendBlockRef(self,bref):
    ''' Append a BlockRef to the BlockSink.
    '''
    S=self.__store
    level=0
    while True:
      blist=self.__lists[level]
      ##print "append: blist =", `blist`
      if len(blist) < MAX_SUBBLOCKS:
        blist.append(bref)
        return

      # The blocklist is full.
      # Make a BlockRef to refer to the stored version.
      # We will store it at the next level of indirection.
      nbref=BlockRef(S.store(blist.pack()),True,blist.span())

      # Prepare fresh empty block at this level.
      # and add the old bref to it instead.
      blist=self.__lists[level]=BlockList(S)
      blist.append(bref)

      # Advance to the next level of indirection and repeat
      # to store the just produced full indirect block.
      level+=1
      bref=nbref
      if level == len(self.__lists):
        print "new indir level", level
        self.__lists.append(BlockList(S))

  def close(self):
    ''' Store all the outstanding indirect blocks (if they've more than one etry).
        Return the top blockref.
    '''
    S=self.__store

    # special case - empty file
    if len(self.__lists) == 1 and len(self.__lists[0]) == 0:
      return BlockRef(S.store(''),False,0)

    # record the lower level indirect blocks in their parents
    while len(self.__lists) > 1:
      blist=self.__lists.pop(0)
      assert len(blist) > 0
      if len(blist) == 1:
        # discard block with just one pointer - keep the pointer
        bref=blist[0]
      else:
        # store and record the indirect block
        bref=BlockRef(S.store(blist.pack()),True,blist.span())
      self.appendBlockRef(bref)

    # There's just one top block now.
    # Pull it and disable the blocklist stack.
    assert len(self.__lists) == 1
    blist=self.__lists[0]
    self.__lists=None

    assert len(blist) > 0
    if len(blist) == 1:
      # The top block has only one BlockRef - return the BlockRef.
      bref=blist[0]
    else:
      # stash the top block
      bref=BlockRef(S.store(blist.pack()),True,blist.span())

    return bref

# Assorted findEdge functions.
#
def findEdgeDumb(block):
  ''' Find edge for unstructured data.
  '''
  global MAX_BLOCKSIZE
  if len(self.__q) >= MAX_BLOCKSIZE:
    return MAX_BLOCKSIZE
  return 0

def findEdgeCode(block):
  ''' Find edge for UNIX mbox files and code.
  '''
  start=0
  found=block.find('\n')
  while found >= 0:
    if found > MAX_BLOCKSIZE:
      return MAX_BLOCKSIZE

    # UNIX mbox file
    # python/perl top level things
    if block[found+1:found:6] == "From " \
    or block[found+1:found+5] == "def " \
    or block[found+1:found+7] == "class " \
    or block[found+1:found+9] == "package ":
      return found+1

    # C/C++/perl etc end of function
    if block[found+1:found+3] == "}\n":
      return found+3

    start=found+1
    found=block.find('\n',start)

  return 0

class WriteFile:
  ''' A File-like class that supplies only write, close, flush.
      flush() forces any unstored data to the store.
      close() flushes all data and returns a BlockRef for the whole file.
  '''
  def __init__(self,S,findEdge=None):
    if findEdge is None:
      findEdge=findEdgeCode

    self.__sink=BlockSink(S)
    self.__findEdge=findEdge
    self.__q=''

  def close(self):
    ''' Close the file. Return the hash code of the top indirect block.
    '''
    self.flush()
    return self.__sink.close()

  def flush(self):
    ''' Force any remaining data to the store.
    '''
    if len(self.__q) > 0:
      self.__dequeue()
      if len(self.__q) > 0:
        self.__sink.appendBlock(self.__q)
        self.__q=''

  def write(self,data):
    ''' Queue data for writing.
    '''
    self.__q+=data
    self.__dequeue()

  def __dequeue(self):
    ''' Flush complete blocks to the store.
    '''
    edgendx=self.__findEdge(self.__q)
    while edgendx > 0:
      self.__sink.appendBlock(self.__q[:edgendx])
      self.__q=self.__q[edgendx:]
      edgendx=self.__findEdge(self.__q)

def decodeDirent(fp):
    namelen=fromBSfp(fp)
    if namelen is None:
      return (None,None)
    assert namelen > 0
    name=fp.read(namelen)
    assert len(name) == namelen, \
            "expected %d chars, got %d (%s)" % (namelen,len(name),`name`)

    flags=fromBSfp(fp)
    isdir=bool(flags&0x01)
    hasmeta=bool(flags&0x02)
    if hasmeta:
      metalen=fromBSfp(fp)
      assert metalen > 1
      meta=fp.read(metalen)
      assert len(meta) == metalen
    else:
      meta=None
    bref=decodeBlockRefFP(fp)

    return (name,Dirent(bref,isdir,meta))

def encodeDirent(fp,name,dent):
  assert len(name) > 0
  fp.write(toBS(len(name)))
  fp.write(name)
  hasmeta=dent.meta is not None
  flags=int(dent.isdir)|(0x02*int(hasmeta))
  fp.write(toBS(flags))
  if hasmeta:
    menc=dent.encodeMeta()
    fp.write(toBS(len(menc)))
    fp.write(menc)
  fp.write(dent.bref.encode())

class Dirent:
  def __init__(self,bref,isdir,meta=None):
    self.bref=bref
    self.isdir=isdir
    self.meta=meta
  def encodeMeta(self):
    return meta

class Dir(dict):
  def __init__(self,S,parent,dirref=None):
    self.__store=S
    self.__parent=parent
    if dirref is not None:
      fp=S.readOpen(dirref)
      (name,dent)=decodeDirent(fp)
      while name is not None:
        dict.__setitem__(self,name,dent)
        (name,dent)=decodeDirent(fp)

  def __setitem__(self,key,value):
    raise IndexError
  def add(self,name,bref,isdir,meta=None):
    dict.__setitem__(self,name,Dirent(bref,isdir,meta))

  def sync(self):
    ''' Encode dir to store, return blockref of encode.
    '''
    fp=self.__store.writeOpen()
    for name in self:
      encodeDirent(fp,name,self[name])
    return fp.close()

  def dirs(self):
    return [name for name in self.keys() if self[name].isdir]

  def files(self):
    return [name for name in self.keys() if not self[name].isdir]

  def ancestry(self):
    ''' Return parent directories, closest first.
    '''
    p=self.__parent
    while p is not None:
      yield p
      p=p.__parent

  def walk(self,topdown=True):
    dirs=self.dirs()
    files=self.files()
    if topdown:
      yield (self,dirs,files)
    for subD in [Dir(self.__store,D,self[name].bref) for name in dirs]:
      for i in subD.walk(topdown=topdown):
        yield i
    if not topdown:
      yield (self,dirs,files)

  def unpack(self,basepath):
    S=self.__store
    for f in self.files():
      fpath=os.path.join(basepath,f)
      progress("create file", fpath)
      ofp=open(fpath, "w")
      ifp=S.readOpen(self[f].bref)
      buf=ifp.readShort()
      while len(buf) > 0:
        ofp.write(buf)
        buf=ifp.readShort()
    for d in self.dirs():
      dirpath=os.path.join(basepath,d)
      progress("mkdir", dirpath)
      os.mkdir(dirpath)
      Dir(S,self,dirref=self[d].bref).unpack(dirpath)

# horrible hack because the Fuse class doesn't seem to tell fuse file
# objects which class instantiation they belong to
mainFuseStore=None

def fuse(backfs,store):
  ''' Run a FUSE filesystem with the specified basefs backing store
      and Venti storage.
      This is a separate function to defer the imports.
  '''

  from fuse import Fuse, Direntry
  from errno import ENOSYS
  class FuseStore(Fuse):
    def __init__(self, *args, **kw):
      global mainFuseStore
      assert mainFuseStore is None, "multiple instantiations of FuseStore forbidden"

      print "FuseStore:"
      print "  args =", `args`
      print "  kw =", `kw`
      Fuse.__init__(self, *args, **kw)

      import os.path
      assert os.path.isdir(backfs)
      self.__backfs=backfs
      self.__store=store

      # HACK: record fuse class object for use by files :-(
      mainFuseStore=self
      self.file_class=self.__File

    def __abs(self, path):
      assert path[0] == '/'
      return os.path.join(self.__backfs, path[1:])

    def getattr(self,path):
      print "getattr", path
      return os.lstat(self.__abs(path))
    def readlink(self, path):
      print "readlink", path
      return os.readlink(self.__abs(path))
    def readdir(self, path, offset):
      print "readdir", path
      yield Direntry('.')
      yield Direntry('..')
      for e in os.listdir(self.__abs(path)):
        print "readdir yield"
        yield Direntry(e)
    def unlink(self, path):
      print "unlink", path
      os.unlink(self.__abs(path))
    def rmdir(self, path):
      print "rmdir", path
      os.rmdir(self.__abs(path))
    def symlink(self, path, path1):
      print "symlink", path
      os.symlink(path, self.__abs(path1))
    def rename(self, path, path1):
      print "rename", path, path1
      os.rename(self.__abs(path), self.__abs(path1))
    def link(self, path, path1):
      print "link", path, path1
      os.link(self.__abs(path), self.__abs(path1))
    def chmod(self, path, mode):
      print "chmod 0%03o %s" % (mode,path)
      os.chmod(self.__abs(path), mode)
    def chown(self, path, user, group):
      print "chown %d:%d %s" % (user,group,path)
      os.chown(self.__abs(path), user, group)
    def truncate(self, path, len):
      print "truncate", path, len
      return -ENOSYS
    def mknod(self, path, mode, dev):
      print "mknod", path, mode, dev
      os.mknod(self.__abs(path), mode, dev)
    def mkdir(self, path, mode):
      print "mkdir 0%03o %s" % (mode,path)
      os.mkdir(self.__abs(path), mode)
    def utime(self, path, times):
      print "utime", path
      os.utime(self.__abs(path), times)
    def access(self, path, mode):
      print "access", path, mode
      if not os.access(self.__abs(path), mode):
        return -EACCES
    def statfs(self):
      print "statfs"
      return os.statvfs(self.__basefs)

    class __File(object):
      def __init__(self, path, flags, *mode):
        print "new __File: path =", path, "flags =", `flags`, "mode =", `mode`
        global mainFuseStore
        assert mainFuseStore is not None
        self.__Fuse=mainFuseStore
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

  FS=FuseStore()
  FS.parser.add_option(mountopt="root", metavar="PATH",
                       help='file system adjunct to store')
  FS.parse(values=FS, errex=1)
  FS.main()

