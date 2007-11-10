#!/usr/bin/python
#
# Directory related stuff.      - Cameron Simpson <cs@zip.com.au>
#

import os
from cs.misc import warn, progress, verbose, toBS, fromBSfp, DictAttrs
from cs.venti import tohex
from cs.venti.blocks import BlockRef, decodeBlockRefFP
from cs.venti.file import ReadFile, WriteFile

def storeDir(S,path):
  ''' storeDir(Store,path) -> Dir
      Store a directory tree in a Store, return the top Dir.
  '''
  subdirs={}
  for (dirpath, dirs, files) in os.walk(path,topdown=False):
    progress("storeDir", dirpath)
    assert dirpath not in subdirs
    D=Dir(S,None)
    for dir in dirs:
      subpath=os.path.join(dirpath,dir)
      subD=subdirs[subpath]
      del subdirs[subpath]
      D.link(dir,subD)
    for subfile in files:
      filepath=os.path.join(dirpath,subfile)
      verbose("storeDir: storeFile "+filepath)
      try:
        D.link(subfile,S.storeFile(open(filepath)))
      except IOError, e:
        cmderr("%s: can't store: %s" % (filepath, `e`))
    subdirs[dirpath]=D

  topdirs=subdirs.keys()
  assert len(topdirs) == 1, "expected one top dir, got "+`topdirs`
  return subdirs[topdirs[0]]

class Dirent:
  ''' A Dirent represents a directory entry.
        .bref   A BlockRef to the entry's data.
        .isdir  Whether the entry is a directory.
        .meta   Meta data, if any.
  '''
  def __init__(self,bref,isdir,meta=None):
    self.bref=bref
    self.isdir=isdir
    self.meta=meta
  def encodeMeta(self):
    assert False, "encode self.meta instead?"
    return meta.encode()
  def lstat(self,S):
    import stat
    s=DictAttrs({ st_ino: id(self),
                  st_nlink: 1,
                  st_uid: os.geteuid(),
                  st_gid: os.getegid(),
                  st_size: open(S,self.bref,"r").span(),
                })
    if self.isdir:
      s.st_mode=stat.S_IFDIR|0755
    else:
      s.st_mode=stat.S_IFREG|0644
    return s

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

class Dir(dict):
  ''' A directory object using a Store.
  '''
  def __init__(self,S,parent,dirref=None):
    self.isdir=True
    self.meta=None
    self.__store=S
    self.__parent=parent
    if dirref is not None:
      from cs.venti.file import open
      fp=open(S,dirref,"r")
      (name,dent)=decodeDirent(fp)
      while name is not None:
        ##warn("%s: load %s" % (dirref,name))
        dict.__setitem__(self,name,dent)
        (name,dent)=decodeDirent(fp)

  def __setitem__(self,key,value):
    raise IndexError

  def link(self,name,dent,meta=None):
    assert name not in self
    if isinstance(dent, BlockRef):
      dent=Dirent(dent, False)
    else:
      assert type(dent) in (Dirent, Dir, WriteFile), "dent = %s" % `dent`
    dict.__setitem__(self,name,dent)

  def unlink(self,name):
    del self[name]

  def sync(self):
    ''' Encode dir to store, return blockref of encode.
    '''
    import cs.venti.file
    fp=cs.venti.file.open(self.__store,None,"w")
    names=self.keys()
    names.sort()
    for name in names:
      dent=self[name]
      if isinstance(dent, Dir):
        # convert open Dir to static Dirent
        dent=Dirent(dent.sync(),True)
      elif isinstance(dent, WriteFile):
        # convert open WriteFile to static Dirent
        dent=Dirent(dent.close(),False)
      else:
        assert isinstance(dent, Dirent), "dent = %s" % `dent`
      encodeDirent(fp,name,dent)
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

  def chdir(self,name):
    dent=self[name]
    if type(dent) is not Dir:
      # convert static Dirent into open Dir
      dent=Dir(self.__store,self,dent.bref)
    return dent

  def mkdir(self,name):
    assert name not in self, "mkdir(%s): already exists" % name
    dent=Dir(self.__store,self)
    dict.__setitem__(self,name,dent)
    return dent

  def open(self,name,mode="r"):
    if mode == "r":
      dent=self[name]
      assert not dent.isdir, "%s: is a directory" % name
      assert type(dent) is Dirent
      return ReadFile(self.__store, dent.bref)

    if mode == "w":
      dent=WriteFile(self.__store)
      self.link(name, dent)
      return dent

    assert False, "open(%s,mode=%s): unsupported mode" % (name,mode)

  def walk(self,topdown=True):
    dirs=self.dirs()
    files=self.files()
    if topdown:
      yield (self,dirs,files)
    for subD in [self.chdir(name) for name in dirs]:
      for i in subD.walk(topdown=topdown):
        yield i
    if not topdown:
      yield (self,dirs,files)

  def unpack(self,basepath):
    S=self.__store
    from cs.venti.file import open
    for f in self.files():
      fpath=os.path.join(basepath,f)
      progress("create file", fpath)
      ofp=open(fpath, "w")
      ifp=open(S,self[f].bref,"r")
      buf=ifp.readShort()
      while len(buf) > 0:
        ofp.write(buf)
        buf=ifp.readShort()
    for d in self.dirs():
      dirpath=os.path.join(basepath,d)
      progress("mkdir", dirpath)
      os.mkdir(dirpath)
      Dir(S,self,dirref=self[d].bref).unpack(dirpath)
