#!/usr/bin/python

''' A data store after the style of the Venti scheme:
      http://library.pantek.com/general/plan9.documents/venti/venti.html
    but supporting variable sized blocks and arbitrary sizes.
    See also the Plan 9 Venti support manual pages:
      http://swtch.com/plan9port/man/man7/venti.html
    and the Wikipedia entry:
      http://en.wikipedia.org/wiki/Venti

    TODO: rolling hash
            augment with assorted recognition strings by hash
            pushable parser for nested data
          optional compression in store
          metadata O:owner u:user[+-=]srwx* g:group[+-=]srwx*
          don't compress metadata
          cache seek()ed block in readOpen class
          extend directory blockref:
            flags:
              1: indirect
              2: inode ref
          inode chunk:
            flags
            [meta] (if flags&0x01)
            blockref
          multiple store files in store n.vtd
          store index index.dbm: h => (n, offset, zsize)
          caching store - fetch&store locally
          store priority queue - tuples=pool
          remote store: http? multifetch? udp?
'''

import os
import os.path
from zlib import compress, decompress
## NOTE: migrate to hashlib sometime when python 2.5 more common
import sha
from cs.cache import LRU
from cs.misc import cmderr, warn, progress, verbose, out, fromBS, toBS, fromBSfp
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

class RawStoreIndexGDBM:
  def __init__(self,path):
    import gdbm
    self.__db=gdbm.open(os.path.join(path,"index"),"cf")
  def sync(self):
    self.__db.sync()
  def __setitem__(self,h,noz):
    # encode then store
    self.__db[h]=toBS(noz[0])+toBS(noz[1])+toBS(noz[2])
  def __getitem__(self,h):
    # fetch and decode
    noz=self.__db[h]
    (n,noz)=fromBS(noz)
    (offset,noz)=fromBS(noz)
    (zsize,noz)=fromBS(noz)
    assert len(noz) == 0
    return (n,offset,zsize)
  def get(self,h,default=None):
    try:
      return self[h]
    except KeyError:
      return default
  def __contains__(self,h):
    noz=self.get(h)
    return noz is not None
  def __len__(self):
    return len(self.__db)
  def keys(self):
    k = self.__db.firstkey()
    while k is not None:
      yield k
      k = self.__db.nextkey()
  def __iter__(self):
    for h in self.__db.keys():
      yield h
  def iterkeys(self):
    for h in self.__db.keys():
      yield h

class GenericStore:
  ''' Store methods implemented entirely in terms of other public methods,
      or common to all (eg hash()).
  '''
  def get(self,h,default=None):
    ''' Return block for hash, or None if not present in store.
    '''
    if h not in self:
      return None
    return self[h]

  def hash(self,block):
    ''' Compute the hash for a block.
    '''
    return hash_sha(block)

  # compatibility wrapper for __setitem__
  # does a store() and then ensures the hashes match
  def __setitem__(self,h,block):
    stored=self.store(block)
    assert h == stored
    return block

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

  def storeFile(self,ifp,rsize=None):
    if rsize is None: rsize=8192
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
        verbose("storeDir: storeFile "+filepath)
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

  def namei(self,hexarg):
    ''' Given a path of the form
          hexarg/sub1/sub2/...
        return the Dirent for the end of the path, or None.
        hexarg is a hex(bref.encode()) as used by "vt cat" or "vt ls".
    '''
    slash=hexarg.find('/')
    if slash < 0:
      # no slash - presume file reference
      return Dirent(str2BlockRef(unhex(hexarg)),False)

    subpath=[p for p in hexarg[slash+1:].split('/') if len(p)]
    if len(subpath) == 0:
      return Dirent(str2BlockRef(unhex(hexarg[:slash])), hexarg[-1] == '/')

    hexarg=hexarg[:slash]
    D=self.opendir(unhex(hexarg))
    while len(subpath) > 1:
      D=D.subdir(subpath.pop(0))
    return D[subpath[0]]

  def walk(self,dirref):
    for i in self.opendir(dirref).walk():
      yield i

class StoreWrapper(GenericStore):
  def __init__(self,S):
    self.__S=S
  def hash(self,block):               return self.__S.hash(block)
  def store(self,block):              return self.__S.store(block)
  def sync(self):                     return self.__S.sync()
  def __getitem__(self,h):            return self.__S[h]
  def __contains__(self,h):           return h in self.__S

class RawStore(GenericStore):
  def __init__(self,path,doindex=False):
    self.__path=path
    stores=[ int(name[:-4])
             for name in os.listdir(path)
             if len(name) > 4
                and name.endswith('.vtd')
                and name[:-4].isdigit()
           ]
    if len(stores) == 0:
      stores=[0]
    self.__newStore=max(stores)
    self.__open={}
    for n in stores:
      self.__open[n]=None
    self.__opened=[]    # dumb FIFO of open files
    self.__index=RawStoreIndexGDBM(path)
    if doindex:
      for n in stores:
        self.__loadIndex(n)
      self.sync()

  def store(self,block):
    ''' Store a block, return the hash.
    '''
    h=self.hash(block)
    if h in self:
      verbose(self.__path,"already contains",hex(h))
    else:
      zblock=compress(block)
      fp=self.__storeOpen(self.__newStore)
      fp.seek(0,2)
      fp.write(toBS(len(zblock)))
      offset=fp.tell()
      fp.write(zblock)
      self.__index[h]=(self.__newStore,offset,len(zblock))

    return h

  def sync(self):
    self.__index.sync()
    for n in self.__open:
      fp=self.__open[n]
      if fp:
        fp.flush()

  def __storeOpen(self,n):
    name=str(n)+'.vtd'
    if self.__open[n] is None:
      # flush an older file
      if len(self.__opened) >= 16:
        oldn=self.__opened.pop(0)
        oldfp=self.__open[oldn]
        oldfp.close()
        self.__open[oldn]=None
      self.__open[n]=newfp=open(os.path.join(self.__path,name),"a+b")
      self.__opened.append(newfp)
    return self.__open[n]

  def __getitem__(self,h):
    ''' Return block for hash, or raise IndexError if not in store.
    '''
    n,offset,zsize = self.__index[h]
    fp=self.__storeOpen(n)
    fp.seek(offset)
    return decompress(fp.read(zsize))

  def __contains__(self,h):
    return h in self.__index

  def __loadIndex(self,n):
    progress("load index from store", str(n))
    fp=self.__storeOpen(n)
    while True:
      zsize=fromBSfp(fp)
      if zsize is None:
        break
      offset=fp.tell()
      zblock=fp.read(zsize)
      block=decompress(zblock)
      h=self.hash(block)
      self.__index[h]=toBS(n)+toBS(offset)+toBS(zsize)

MAX_LRU=1024
class LRUCacheStore(LRU,StoreWrapper):
  ''' A subclass of RawStore that keeps an LRU cache of referenced blocks.
      TODO: Recode to take an arbitrary Store backend.
            This will let me put Store pools behind a single cache.
  '''
  def __init__(self,backstore,max=None):
    if maxCache is None: maxCache=MAX_LRU
    LRU.__init__(self,backstore,max)
    StoreWrapper.__init__(self,backstore)

class Store(StoreWrapper):
  def __init__(self,S):
    if type(S) is str:
      if S[0] == '/':
        S=RawStore(S)
    StoreWrapper.__init__(self,S)

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
  assert len(h) == hlen, "(indir=%s, span=%d): expected hlen=%d bytes, read %d bytes: %s"%(indirect,span,hlen,len(h),hex(h))
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
  ''' A file interface supporting seek(), read(), readline(), readlines()
      and tell() methods.
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
      # at or past EOF - return empty read
      return ''
    b=self.__store[h]
    assert offset < len(b)
    chunk=b[offset:]
    assert len(chunk) > 0
    self.seek(len(chunk),1)
    return chunk

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
  ''' A BlockSink supports storing a sequence of blocks or BlockRefs,
      assembling indirect blocks as necessary. It maintains a list of
      BlockLists containing direct or indirect BlockRefs. As each BlockList
      overflows its storage limit it is encoded as a block and stored and a
      new BlockList made for further data; the encoded block is added
      to BlockList for the next level of indirection.

      On close() the BlockLists are stored.
      As a storage optimisation, a BlockList with only one BlockRef is
      discarded, and the BlockRef passed to the next level up.
      When there are no more higher levels, the current blockref is
      returned as the top level reference to the file. For small files
      this is a direct BlockRef to the sole block of storage for the file.
  '''
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
        self.__lists.append(BlockList(S))

  def close(self):
    ''' Store all the outstanding indirect blocks (if they've more than one etry).
        Return the top blockref.
    '''
    S=self.__store

    # special case - empty file
    if len(self.__lists) == 1 and len(self.__lists[0]) == 0:
      self.appendBlock('')

    # Fold BlockLists into blocks and pass them up the chain until we have
    # a single BlockRef - it becomes the file handle.
    # record the lower level indirect blocks in their parents
    assert len(self.__lists) > 0

    # Reduce the BlocList stack to one level.
    while True:
      blist=self.__lists.pop(0)
      assert len(blist) > 0
      if len(blist) == 1:
        # discard block with just one pointer - keep the pointer
        bref=blist[0]
      else:
        # store and record the indirect block
        bref=BlockRef(S.store(blist.pack()),True,blist.span())

      if len(self.__lists) == 0:
        break

      # put the new blockref into the popped stack
      self.appendBlockRef(bref)

    # POST: bref if the top blockref
    assert len(self.__lists) == 0
    self.__lists=None

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
      meta=decodeMetaData(meta)
    else:
      meta=None
    bref=decodeBlockRefFP(fp)

    assert flags&~0x03 == 0

    return (name,Dirent(bref,isdir,meta))

def MetaData(dict):
  def __init__(self,isdir,meta=None):
    dict.__init__(self)
    self.isdir=isdir
    self.__encoded=meta

  def __init(self):
    if self.__encoded is not None:
      self['OWNER']=None
      self['ACL']=[]
      for melem in decompress(self.__encoded).split():
        if melem.startswidth("o:"):
          self['OWNER']=melem[2:]
        else:
          self['ACL'].append(melem)
      self.__encoded=None

  def encode(self):
    self.__init()
    m=[]
    o=self['OWNER']
    if o is not None:
      m.append("o:"+o)
    for ac in self['ACL']:
      m.append(ac)
    return compress(" ".join(m))

  def chown(self,user):
    self['OWNER']=user

  def ACL(self):
    acl=self.get('ACL')
    if acl is None:
      acl=()
    return acl

  def UNIXstat(self):
    import stat
    owner=None
    mode=0

    o=self.get('OWNER')
    if o is not None:
      owner=o
      tag="u:"+o+":"
      acl=self.acl()
      acl.reverse()
      for ac in [a for a in self.acl() if a.startswith(tag)]:
        perms=ac[len(tag):]
        if perms[:1] == '-':
          for p in perms[1:]:
            if p == '*':
              mode &= ~0700
            elif p == 'r':
              mode &= ~0400
            elif p == 'w':
              mode &= ~0200
            elif p == 'x':
              mode &= ~0100
        else:
          for p in perms:
            if p == '*':
              mode |= 0700
            elif p == 'r':
              mode |= 0400
            elif p == 'w':
              mode |= 0200
            elif p == 'x':
              mode |= 0100

    ##group=None
    ##for ac in [a for a in self.acl() if a.startswith("g:")]:
    ##  if group is None:

    return None

def debuggingEncodeDirent(fp,name,dent):
  from StringIO import StringIO
  sfp=StringIO()
  realEncodeDirent(sfp,name,dent)
  enc=sfp.getvalue()
  nsfp=StringIO(enc)
  decName, decEnt = decodeDirent(nsfp)
  assert nsfp.tell() == len(enc) and decName == name, "len(enc)=%d len(decenc)=%d, name=%s, decname=%s"%(len(enc),nsfp.tell(),name,decName)
  fp.write(enc)

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
    return meta.encode()

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
    names=self.keys()
    names.sort()
    for name in names:
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

  def subdir(self,name):
    return Dir(self.__store,self,self[name].bref)

  def walk(self,topdown=True):
    dirs=self.dirs()
    files=self.files()
    if topdown:
      yield (self,dirs,files)
    for subD in [self.subdir(name) for name in dirs]:
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

