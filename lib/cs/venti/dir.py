import stat
from cs.venti.blocks import decodeBlockRef, BlockRef
from cs.venti.blockify import topBlockRefString
from cs.venti.meta import Meta
from cs.venti import tohex
from cs.lex import unctrl
from cs.misc import seq, toBS, fromBS, fromBSfp, verbose, isdebug, debug, cmderr

uid_nobody = -1
gid_nogroup = -1

''' Directries (Dir, a subclass of dict) and Direory Entries (Dirent).
'''

def storeDir(path,aspath=None,S=None):
  ''' Store a real directory into a Store, return the new Dir.
  '''
  if aspath is None:
    aspath=path
  D=Dir(aspath)
  debug("path=%s"%(path,))
  D.updateFrom(path,
               S=S,
               ignoreTimes=False,
               deleteMissing=True,
               overWrite=True)
  return D

D_FILE_T=0
D_DIR_T=1

F_HASMETA=0x01
F_HASNAME=0x02

class Dirent:
  ''' Incomplete base class for Dirent objects.
  '''
  def __init__(self,type,name,meta):
    assert isinstance(type,int), "type=%s"%(type,)
    self.type=type
    assert name is None or isinstance(name,str), "name=%s"%(name,)
    self.name=name
    assert isinstance(meta,Meta), "meta=%s"%(meta,)
    self.meta=meta
    self.d_ino=None
    ##print "META=%s" % meta
    assert meta is not None

  def __str__(self):
    return "<%s:%s:%s>" \
           % ( self.name,
               "D_DIR_T" if self.type == D_DIR_T
               else "D_FILE_T" if self.type == D_FILE_T else str(self.type),
               self.meta,
             )

  def isfile(self):
    ''' Is this a file Dirent?
    '''
    return self.type == D_FILE_T

  def isdir(self):
    ''' Is this a directory Dirent?
    '''
    return self.type == D_DIR_T

  def encode(self,noname=False):
    ''' Serialise the dirent.
        Output format: bs(type)bs(flags)[bs(metalen)meta][bs(namelen)name]bref
    '''
    flags=0

    meta=self.meta
    if meta is not None:
      ##debug("%s:META=%s"%(self.name,meta,))
      assert isinstance(meta,Meta)
      metatxt=meta.encode()
      if len(metatxt) > 0:
        metatxt=toBS(len(metatxt))+metatxt
        flags|=F_HASMETA
    else:
      metatxt=""

    name=self.name
    if noname:
      name=""
    elif name is not None and len(name) > 0:
      name=toBS(len(name))+name
      flags|=F_HASNAME
    else:
      name=""

    bref=self.getBref()
    assert isinstance(bref,BlockRef)
    return toBS(self.type) \
         + toBS(flags) \
         + metatxt \
         + name \
         + bref.encode()

  def size(self):
    return self.getBref().span

  def mtime(self,newtime=None):
    if newtime is None:
      if self.meta is None:
        return 0.0
      return self.meta.mtime()
    self.meta().mtime(newtime)

  def stat(self):
    meta=self.meta
    user, group, unixmode = meta.unixPerms()
    if user is None:
      uid=uid_nobody
    else:
      try:             uid=getpwnam(user)[2]
      except KeyError: uid=uid_nobody

    if group is None:
      gid=gid_nogroup
    else:
      try:             gid=getpwnam(user)[2]
      except KeyError: gid=gid_nogroup

    if self.type == D_DIR_T:
      unixmode|=stat.S_IFDIR
    else:
      unixmode|=stat.S_IFREG

    if self.d_ino is None:
      self.d_ino=seq()
    ino=self.d_ino

    dev=0       # FIXME: we're not hooked to a FS?
    nlink=1
    size=self.size()
    atime=0
    mtime=self.mtime()
    ctime=0

    return (unixmode, ino, dev, nlink, uid, gid, size, atime, mtime, ctime)

class _BasicDirent(Dirent):
  ''' A _BasicDirent represents a file or directory in the store.
  '''
  def __init__(self,type,name,meta,bref):
    Dirent.__init__(self,type,name,meta)
    self.__bref=bref

  def getBref(self):
    return self.__bref

  def __getitem__(self,name):
    if self.isdir():
      return self.asdir()[name]
    raise KeyError, "\"%s\" not in %s" % (name, self)

def FileDirent(name,meta,bref):
  return _BasicDirent(D_FILE_T,name,meta,bref)

def decodeDirent(s, justone=False):
  ''' Unserialise a dirent, return object.
      Input format: bs(type)bs(flags)[bs(metalen)meta][bs(namelen)name]bref
  '''
  s0 = s
  type, s = fromBS(s)
  flags, s = fromBS(s)
  if flags & F_HASMETA:
    metalen, s = fromBS(s)
    assert metalen < len(s)
    meta = s[:metalen]
    s=s[metalen:]
  else:
    meta=""
  meta=Meta(meta)
  if flags & F_HASNAME:
    namelen, s = fromBS(s)
    assert namelen < len(s)
    name = s[:namelen]
    s=s[namelen:]
  else:
    name=""
  bref, s = decodeBlockRef(s)
  if type == D_DIR_T:
    E=Dir(name,meta=meta,parent=None,content=bref)
  else:
    E=_BasicDirent(type,name,meta,bref)
  ##if isdebug: cmderr("decode(%s) -> %s" % (tohex(s0), E))
  if justone:
    assert len(s) == 0, \
           "unparsed stuff after decoding %s: %s" % (tohex(s0), tohex(s))
    return E
  return E, s

class Dir(Dirent):
  def __init__(self,name,meta=None,parent=None,content=None):
    if meta is None:
      meta=Meta()
    Dirent.__init__(self,D_DIR_T,name,meta)
    self.parent=parent
    self.__content={}
    self.__contentBref=content

  def isdir(self,name=None):
    if name is None:
      return Dirent.isdir(self)
    return self[name].isdir()

  def dirs(self):
    return [ name for name in self.keys() if self[name].isdir() ]

  def files(self):
    return [ name for name in self.keys() if self[name].isfile() ]

  def overlayBlockRef(self,bref,S=None):
    from cs.venti.file import ReadFile
    iblock=ReadFile(bref,S=S).read()
    while len(iblock) > 0:
      oiblock=iblock
      E, iblock = decodeDirent(iblock)
      assert len(iblock) < len(oiblock) and oiblock.endswith(iblock)
      if E.name is None or len(E.name) == 0:
        FIXME("skip unnamed dirent")
        continue
      if E.name == '.' or E.name == '..':
        FIXME("skip \"%s\"" % E.name)
        continue
      if E.isdir():
        E.parent=self
      self.__content[E.name]=E

  def __validname(self,name):
    return len(name) > 0 and name.find('/') < 0

  def __needContent(self,S=None):
    if self.__contentBref is not None:
      bref=self.__contentBref
      self.__contentBref=None
      self.overlayBlockRef(bref,S=S)
    return self.__content

  def get(self,name,dflt=None,S=None):
    self.__needContent(S=S)
    if name not in self:
      return dflt
    return self[name]

  def keys(self,S=None):
    return self.__needContent(S=S).keys()

  def __contains__(self,name):
    if name == '.':
      return True
    if name == '..':
      return self.parent is not None
    self.__needContent()
    return name in self.__content

  def __iter__(self,S=None):
    return self.keys(S=S)

  def __getitem__(self,name,S=None):
    if name == '.':
      return self
    if name == '..':
      return self.parent
    return self.__needContent(S=S)[name]

  def __setitem__(self,name,E):
    ##debug("<%s>[%s]=%s" % (self.name,name,E))
    assert self.__validname(name)
    assert name not in self
    assert isinstance(E,Dirent)
    self.__needContent()[name]=E

  def __delitem__(self,name,S=None):
    assert self.__validname(name)
    assert name != '.' and name != '..'
    self.__needContent(S=S)
    del self.__content[name]

  def getBref(self):
    ''' Return the top BlockRef referring to an encoding of this Dir.
    '''
    names=self.keys()
    names.sort()
    s="".join( self[name].encode()
               for name in names
               if name != '.' and name != '..')
    ##debug("Dir.data = <%s>" % unctrl(s))
    return topBlockRefString(s)

  def rename(self,oldname, newname):
    E=self[oldname]
    del E[oldname]
    E.name=newname
    self[newname]=E

  def open(self,name):
    from cs.venti.file import ReadFile
    return ReadFile(self[name].getBref())

  def mkdir(self,name):
    debug("<%s>.mkdir(%s)..." % (self.name, name))
    D=self[name]=Dir(name,parent=self)
    return D

  def chdir1(self,name):
    D=self[name]
    assert D.isdir()
    if not isinstance(D,Dir):
      D=self[name]=Dir(D.name,parent=self)
    return D

  def chdir(self,path):
    D=self
    for name in path.split('/'):
      if len(name) == 0:
        continue
      D=D.chdir1(name)
    return D

  def makedirs(self,path):
    ''' Like os.makedirs(), create a directory path at need.
        Returns the bottom directory.
    '''
    D=self
    for name in path.split('/'):
      if len(name) == 0:
        continue
      E=D.get(name)
      if E is None:
        E=D.mkdir(name)
      else:
        assert E.isdir
      D=E
    return D

  def updateFrom(self,
                 osdir,
                 S=None,
                 ignoreTimes=False,
                 deleteMissing=False,
                 overWrite=False):
    ''' Update the target from the real file tree at source.
        Return the top Dir (target).
    '''
    from cs.venti.file import storeFile
    import os
    ##debug("osdir=%s" % (osdir,))
    osdirpfx=os.path.join(osdir,'')
    for dirpath, dirs, files in os.walk(osdir,topdown=False):
      ##debug("dirpath=%s" % dirpath)
      if dirpath == osdir:
        D=self
      else:
        assert dirpath.startswith(osdirpfx), \
                "dirpath=%s, osdirpfx=%s" % (dirpath, osdirpfx)
        subdirpath=dirpath[len(osdirpfx):]
        D=self.makedirs(subdirpath)

      for dir in dirs:
        if dir in D:
          if not D[dir].isdir():
            del D[dir]
            D.mkdir(dir)

      for subfile in files:
        filepath=os.path.join(dirpath,subfile)
        try:
          st=os.stat(filepath)
        except OSError, e:
          cmderr("%s: stat: %s" % (filepath,e))
          continue
        except IOError, e:
          cmderr("%s: stat: %s" % (filepath,e))
          continue
        if subfile in D:
          if not overWrite:
            progress("%s: not overwriting" % filepath)
            continue
          if not ignoreTimes:
            E=D[subfile]
            if st.st_size == E.size() and int(st.st_mtime) == int(E.mtime()):
              verbose("%s: same size and mtime, skipping" % filepath)
              continue
            verbose("%s: differing size(%s:%s)/mtime(%s:%s)" % (filepath,st.st_size,E.size(),int(st.st_mtime),int(E.mtime())))
          else:
            verbose("%s: IGNORETIMES=True" % filepath)
        else:
          verbose("%s not in dir"%subfile)

        debug("storing %s ..." % subfile)
        M=Meta()
        M.mtime(st.st_mtime)
        stored=storeFile(open(filepath),S=S)
        stored.name=subfile
        stored.meta=M
        if subfile in D:
          del D[subfile]
        D[subfile]=stored
