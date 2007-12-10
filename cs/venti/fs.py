#!/usr/bin/python -tt
#
# Filesystem abstractions for stores.
#       - Cameron Simpson <cs@zip.com.au> 01dec2007
#

from cs.misc import toBS, fromBS, fromBSfp, progress, verbose, warn, cmderr, debug
from cs.venti.file import ReadFile, WriteFile, storeFile
from cs.venti.blocks import decodeBlockRefFP

T_FILE=0
T_DIR=1
T_SYMLINK=2

class FS:
  def __init__(self,S,rootBref=None):
    self.store=S
    self.root=Dir(self,'',None,rootBref)

  def open(self,path,mode='r'):
    return self.root.open(path,mode)

  def sync(self):
    return self.root.sync()

  def updateDir(self,source,target=None,ignoreTimes=False,deleteMissing=False,overWrite=False):
    ''' Update the target from the real file tree at source.
        Return the top Dir (target).
    '''
    if target is None:
      T=self.root
    else:
      T=self.root.chdir(target,True)
    debug("updateDir(source=%s,target=%s,ignoreTimes=%s,...)"%(source,target,ignoreTimes))

    import os
    for (dirpath, dirs, files) in os.walk(source,topdown=False):
      D = T.chdir(dirpath,True)

      for dir in dirs:
        D.chdir1(dir,True)

      for subfile in files:
        filepath=os.path.join(dirpath,subfile)
        try:
          st=os.stat(filepath)
        except IOError, e:
          cmderr("%s: stat: %s" % (filepath,e))
          continue
        if subfile in D:
          if not overWrite:
            progress("%s: not overwriting" % filepath)
            continue
          if not ignoreTimes:
            E=D[subfile]
            if st.st_size == E.size() and st.st_mtime == int(E.mtime()):
              verbose("%s: same size and mtime, skipping" % filepath)
              continue
            else:
              verbose("%s: differing size/mtime, storing" % filepath)
          else:
            verbose("IGNORETIMES=True")
        else:
          verbose("%s not in dir"%subfile)

        M=Meta()
        M['mtime']=st.st_mtime
        fp=open(filepath)
        ##debug("fp(%s)=%s" % (filepath,fp))
        D[subfile]=Dirent(T_FILE,storeFile(self.store,fp),M)

    return T

class Dir(dict):
  def __init__(self,FS,path,parent=None,dref=None):
    self.fs=FS
    self.path=path
    self.__parent=parent
    if dref:
      ''' Read directory content from BlockRef.
      '''
      fp=ReadFile(self.fs.store,dref)
      name, E = decodeDirent(fp)
      while name is not None:
        self[name]=E
        name, E = decodeDirent(fp)

  def resolve(self,path,domkdir=False):
    ''' Walk down path starting at the context directory.
        Return the new context directory and the remaining path component.
    '''
    D=self
    names=path.split('/')
    while len(names) > 1:
      name=names.pop(0)
      if len(name) == 0:
        pass
      elif name == '.':
        pass
      elif name == '..':
        if self.__parent:
          D=self.__parent
      D=D.chdir1(name,domkdir)
    return D, names[0]

  def isdir(self,name):
    if name not in self:
      return False
    E=self[name]
    if isinstance(E, Dirent):
      return E.d_type == T_DIR
    if isinstance(E, Dir):
      return True
    return False

  def dirs(self):
    return [name for name in self.keys() if self.isdir(name)]

  def files(self):
    return [name for name in self.keys() if not self.isdir(name)]

  def walk(self,topdown=True):
    dirs=self.dirs()
    files=self.files()
    if topdown:
      yield (self,dirs,files)
    for subD in [self.chdir1(name) for name in dirs]:
      for i in subD.walk(topdown=topdown):
        yield i
    if not topdown:
      yield (self,dirs,files)

  def name2bref(self,name):
    return self[name].bref()

  def chdir(self,path,domkdir=False):
    D, name = self.resolve(path,domkdir)
    return D.chdir1(name,domkdir)

  def chdir1(self,name,domkdir=False):
    ''' Return the subdirectory for name, a path component.
    '''
    if len(name) == 0 or name == '.':
      return self
    if name == '..':
      return self.__parent
    E=self.get(name)
    subpath=("%s/%s"%(self.path,name) if len(self.path) else name)
    if E is None:
      if domkdir:
        D=Dir(self.fs,subpath,self)
        self[name]=D
      else:
        raise IndexError, "no entry named \"%s\"" % name
    else:
      if isinstance(E, Dirent):
        assert E.d_type == T_DIR
        D=Dir(self.fs,subpath,self,E.d_dref)
        self[name]=D
      else:
        assert isinstance(E, Dir)
        D=E
    return D

  def open(self,path,mode='r'):
    D, name = self.resolve(path)
    return D.open1(name,mode)

  def open1(self,name,mode='r'):
    E=D.get(name)
    if E is None:
      # new entry - really easy
      if mode == 'r':
        assert False, "no file called \"%s\"" % name
      else:
        F=WriteFile(self.fs.store)
    else:
      # existing entry - check types
      t=type(E)
      if t is Dirent:
        assert E.d_type == T_FILE
        if mode == 'r':
          F=ReadFile(self.fs.store, E.d_dref)
        else:
          F=WriteFile(self.fs.store)
      else:
        assert False, "%s: can't open files twice yet" % name

    D[name]=OpenFileDirent(F)
    return F

  def sync(self):
    ''' Sync directory to store, return BlockRef.
    '''
    import cs.venti.store
    assert isinstance(self.fs.store, cs.venti.store.BasicStore), "self.fs.store=%s, self.fs=%s"%(self.fs.store,self.fs)
    F=WriteFile(self.fs.store)
    names=self.keys()
    names.sort()
    for name in names:
      E=self[name]
      encodeDirent(name,E.dirent(),F)
    return F.close()

  def dirent(self):
    ''' Sync directory to store, return dirent.
    '''
    return Dirent(T_DIR,self.sync(),None)

  def bref(self):
    return self.sync()

class OpenFileDirent:
  def __init__(self,FS,F):
    self.__fp=F
    self.fs=FS
  def dirent(self):
    return Dirent(T_FILE,self.bref(),None)
  def sync(self):
    return self.__fp.sync()
  def bref(self):
    return self.__fp.sync()
  def size(self):
    return self.bref().span

def encodeDirent(name,E,fp):
  ''' Write a Dirent to a stream.
      Format is:
        BS(namelen)
        name
        type
        metalen
        meta
        T_FILE, T_DIR:
          dataRef
        T_SYMLINK:
          BS(symlen)
          sym
  '''
  fp.write(toBS(len(name)))
  fp.write(name)
  fp.write(toBS(E.d_type))
  if E.d_meta is None:
    fp.write(toBS(0))
  else:
    mencode=E.d_meta.encode()
    fp.write(toBS(len(mencode)))
    fp.write(mencode)
  if E.d_type == T_FILE or E.d_type == T_DIR:
    fp.write(E.d_dref.encode())
  else:
    assert False, "unsupported Dirent type %d" % E.d_type

def decodeDirent(fp):
  ''' Read a Dirent from a stream.
  '''
  namelen=fromBSfp(fp)
  if namelen is None:
    # EOF
    return None, None
  assert namelen > 0
  name=fp.read(namelen)
  assert len(name) == namelen, \
          "expected %d chars, got %d (%s)" % (namelen,len(name),`name`)

  type=fromBSfp(fp)
  metalen=fromBSfp(fp)
  if metalen == 0:
    meta=None
  else:
    mencode=fp.read(metalen)
    assert len(mencode) == metalen
    meta=decodeMeta(mencode)
  if type == T_FILE or type == T_DIR:
    dref=decodeBlockRefFP(fp)
    assert dref is not None, \
           "unexpected EOF reading data blockref"
  else:
    assert False, "unsupported Dirent type %d" % E.d_type

  return name, Dirent(type,dref,meta)

class Dirent:
  def __init__(self,type,dataRef,meta=None):
    self.d_type=type
    self.d_dref=dataRef
    self.d_meta=meta

  def __str__(self):
    return "<%s %s %s>" \
           % ( "T_DIR" if self.d_type == T_DIR else "T_FILE" if self.d_type == T_FILE else str(self.d_type),
               self.d_meta,
               self.d_dref
             )

  def encode(self,fp):
    encodeDirent(self,fp)

  def dirent(self):
    return self

  def bref(self):
    return self.d_dref

  def size(self):
    return self.bref().span

  def mtime(self,newtime=None):
    if newtime is not None:
      if self.d_meta is None:
        self.d_meta=Meta()
      self.d_meta['mtime']=newtime
      return
    if self.d_meta is None:
      return 0
    return self.d_meta.get('mtime',0)

def decodeMeta(s):
  M=Meta()
  for line in s.split('\n'):
    line=line.strip()
    if len(line) == 0 or line[0] == '#':
      continue
    k,v = line.split(':',1)
    if k in ('mtime',):
      v=float(v)
    M[k]=v
  return M

class Meta(dict):
  ''' Metadata:
        mtime:unix-seconds(int or float)
        u:login:perms-perms
        g:group:perms-perms
        *:perms-perms
        acl:hash-of-encoded-Meta
        acl:/path/to/encoded-Meta
  '''
  def __str__(self):
    return "[%s]" % self.encode().replace('\n',';')

  def encode(self):
    from StringIO import StringIO
    fp=StringIO()
    self.encodeFP(fp)
    return fp.getvalue()

  def encodeFP(self,fp):
    for t in ('mtime',):
      if t in self:
        fp.write("%s:%d\n" % (t, self[t]))
    for k in self.keys():
      if k not in ('mtime',):
        fp.write("%s:%s\n" % (k, self[k]))
