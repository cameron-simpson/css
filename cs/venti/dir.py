#!/usr/bin/python
#
# Directory related stuff.      - Cameron Simpson <cs@zip.com.au>
#

import os
from cs.misc import progress, verbose, toBS, fromBSfp
from cs.venti import tohex
from cs.venti.blocks import decodeBlockRefFP

def storeDir(S,path):
  ''' Store a directory tree in a Store, return the top dir ref.
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
      ref=subD.sync()
      S.log("store dir %s %s" % (tohex(ref.encode()), subpath))
      D.add(dir,ref,True)
    for subfile in files:
      filepath=os.path.join(dirpath,subfile)
      verbose("storeDir: storeFile "+filepath)
      try:
        D.add(subfile,S.storeFile(open(filepath)),False)
      except IOError, e:
        cmderr("%s: can't store: %s" % (filepath, `e`))
    subdirs[dirpath]=D

  topdirs=subdirs.keys()
  assert len(topdirs) == 1, "expected one top dir, got "+`topdirs`
  ref=subdirs[topdirs[0]].sync()
  S.log("store dir %s %s" % (tohex(ref.encode()), path))
  return ref

class Dirent:
  def __init__(self,bref,isdir,meta=None):
    self.bref=bref
    self.isdir=isdir
    self.meta=meta
  def encodeMeta(self):
    assert False, "encode self.meta instead?"
    return meta.encode()

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
  def __init__(self,S,parent,dirref=None):
    self.__store=S
    self.__parent=parent
    if dirref is not None:
      from cs.venti.file import open
      fp=open(S,dirref,"r")
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
    import cs.venti.file
    fp=cs.venti.file.open(self.__store,None,"w")
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
