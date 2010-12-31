import os
import stat
import sys
if sys.hexversion < 0x02060000:
  from sets import Set as set
from thread import allocate_lock
from cs.logutils import Pfx, debug, error, info, warn
from cs.venti.block import decodeBlock
from cs.venti.blockify import blockFromString
from cs.venti.meta import Meta
from cs.venti import totext, fromtext
from cs.lex import hexify
from cs.misc import seq
from cs.serialise import toBS, fromBS

uid_nobody = -1
gid_nogroup = -1

# Directories (Dir, a subclass of dict) and directory entries (Dirent).

def storeDir(path, aspath=None, trust_size_mtime=False):
  ''' Store a real directory into a Store, return the new Dir.
  '''
  if aspath is None:
    aspath = path
  D = Dir(aspath)
  D.updateFrom(path, trust_size_mtime=trust_size_mtime)
  return D

D_FILE_T = 0
D_DIR_T = 1
def D_type2str(type_):
  if type_ == D_FILE_T:
    return "D_FILE_T"
  if type_ == D_DIR_T:
    return "D_DIR_T"
  return str(type_)

F_HASMETA = 0x01
F_HASNAME = 0x02

class Dirent(object):
  ''' Incomplete base class for Dirent objects.
  '''

  def __init__(self, type_, name, meta):
    assert isinstance(type_, int), "type=%s"%(type_, )
    self.type = type_
    assert name is None or isinstance(name, str), "name=%s"%(name, )
    self.name = name
    assert isinstance(meta, Meta), "meta=%s"%(meta, )
    self.meta = meta
    self.d_ino = None
    assert meta is not None

  def __str__(self):
    return self.textEncode()

  def __repr__(self):
    return "Dirent(%s, %s, %s)" % (D_type2str, self.name, self.meta)

  def isfile(self):
    ''' Is this a file Dirent?
    '''
    return self.type == D_FILE_T

  def isdir(self, name=None):
    ''' Is this a directory Dirent?
    '''
    assert name is None
    return self.type == D_DIR_T

  def encode(self, noname=False):
    ''' Serialise the dirent.
        Output format: bs(type)bs(flags)[bs(metalen)meta][bs(namelen)name]block
    '''
    flags = 0

    meta = self.meta
    if meta:
      assert isinstance(meta, Meta)
      metatxt = meta.encode()
      if len(metatxt) > 0:
        metatxt = toBS(len(metatxt))+metatxt
        flags |= F_HASMETA
    else:
      metatxt = ""

    name = self.name
    if noname:
      name = ""
    elif name is not None and len(name) > 0:
      name = toBS(len(name))+name
      flags |= F_HASNAME
    else:
      name = ""

    block = self.getBlock()
    return toBS(self.type) \
         + toBS(flags) \
         + metatxt \
         + name \
         + block.encode()

  def textEncode(self):
    ''' Serialise the dirent as text.
        Output format: bs(type)bs(flags)[bs(metalen)meta][bs(namelen)name]block
    '''
    flags = 0

    meta = self.meta
    if meta:
      assert isinstance(meta, Meta)
      metatxt = meta.encode()
      if len(metatxt) > 0:
        metatxt = hexify(toBS(len(metatxt))) + totext(metatxt)
        flags |= F_HASMETA
    else:
      metatxt = ""

    name = self.name
    if name is None or len(name) == 0:
      nametxt = ""
    else:
      nametxt = hexify(toBS(len(name))) + totext(name)
      flags |= F_HASNAME

    block = self.getBlock()
    return ( hexify(toBS(self.type))
           + hexify(toBS(flags))
           + metatxt
           + nametxt
           + block.textEncode()
           )

  # TODO: make size a property?
  def size(self):
    return len(self.getBlock())

  @property
  def mtime(self):
    return self.meta.mtime
  @mtime.setter
  def mtime(self, newtime):
    self.meta.mtime = newtime

  def stat(self):
    from pwd import getpwnam
    meta = self.meta
    user, group, unixmode = meta.unixPerms()
    if user is None:
      uid = uid_nobody
    else:
      try:
        uid = getpwnam(user)[2]
      except KeyError:
        uid = uid_nobody

    if group is None:
      gid = gid_nogroup
    else:
      try:
        gid = getpwnam(user)[2]
      except KeyError:
        gid = gid_nogroup

    if self.type == D_DIR_T:
      unixmode |= stat.S_IFDIR
    else:
      unixmode |= stat.S_IFREG

    if self.d_ino is None:
      self.d_ino = seq()
    ino = self.d_ino

    dev = 0       # FIXME: we're not hooked to a FS?
    nlink = 1
    size = self.size()
    atime = 0
    mtime = self.mtime
    ctime = 0

    return (unixmode, ino, dev, nlink, uid, gid, size, atime, mtime, ctime)

class _BasicDirent(Dirent):
  ''' A _BasicDirent represents a file or directory in the store.
  '''
  def __init__(self, type_, name, meta, block):
    Dirent.__init__(self, type_, name, meta)
    self.__block = block

  def getBlock(self):
    return self.__block

  def __getitem__(self, name):
    if self.isdir():
      return self.asdir()[name]
    raise KeyError, "\"%s\" not in %s" % (name, self)

def FileDirent(name, meta, block):
  ''' Factory function to return a Dirent for a file.
      Parameters:
        `name`: the file name to store in the Dirent.
        `meta`: the file meta data.
        `block`: the top block of the file content.
  '''
  return _BasicDirent(D_FILE_T, name, meta, block)

def decodeDirent(s, justone=False):
  ''' Unserialise a dirent, return object.
      Input format: bs(type)bs(flags)[bs(metalen)meta][bs(namelen)name]block
  '''
  s0 = s
  type_, s = fromBS(s)
  flags, s = fromBS(s)
  meta = None
  if flags & F_HASMETA:
    metalen, s = fromBS(s)
    assert metalen < len(s)
    meta = s[:metalen]
    s = s[metalen:]
  meta = Meta(meta)
  if flags & F_HASNAME:
    namelen, s = fromBS(s)
    assert namelen < len(s)
    name = s[:namelen]
    s = s[namelen:]
  else:
    name = ""
  block, s = decodeBlock(s)
  if type_ == D_DIR_T:
    E = Dir(name, meta=meta, parent=None, content=block)
  else:
    E = _BasicDirent(type_, name, meta, block)
  if justone:
    assert len(s) == 0, \
           "unparsed stuff after decoding %s: %s" % (totext(s0), totext(s))
    return E
  return E, s

def resolve(path, domkdir=False):
  ''' Take a path composed of a Direct text representation with an optional
      "/sub/path/..." suffix.
      Decode the Direct and walk down the remaining path, except for the last
      component. Return the final Dirent and the last path componenet, or
      None if there was no final path component.
  '''
  slashpos = path.find('/')
  if slashpos < 0:
    D = decodeDirent(fromtext(path), justone=True)
    subpath = []
  else:
    Dtext, subpath = path.split('/', 1)
    D = decodeDirent(fromtext(Dtext), justone=True)
    subpath = [s for s in subpath.split('/') if len(s) > 0]
  if len(subpath) == 0:
    return D, None
  while len(subpath) > 1:
    s = subpath.pop(0)
    if domkdir:
      D = D.mkdir(s)
    else:
      D = D.chdir1(s)
  return D, subpath[0]

class Dir(Dirent):
  ''' A directory.
  '''

  def __init__(self, name, meta=None, parent=None, content=None):
    ''' Initialise this directory.
        `meta`: meta information
        `parent`: parent Dir
        `content`: pre-existing Block with initial Dir content
    '''
    self._lock = allocate_lock()
    if meta is None:
      meta = Meta()
    Dirent.__init__(self, D_DIR_T, name, meta)
    self.parent = parent
    self._precontent = content
    self._entries = None
    self._entries_lock = allocate_lock()

  @property
  def entries(self):
    with self._entries_lock:
      entries = self._entries
      if entries is None:
        entries = self._entries = {}
        precontent = self._precontent
        if precontent is not None:
          self._precontent = None
          self._loadDir(precontent.data)
    return entries

  def isdir(self, name=None):
    if name is None:
      return Dirent.isdir(self)
    return self[name].isdir()

  def dirs(self):
    return [ name for name in self.keys() if self[name].isdir() ]

  def files(self):
    return [ name for name in self.keys() if self[name].isfile() ]

  def _loadDir(self, dirdata):
    ''' Load Dirents from the supplied file-like object `fp`,
        incorporate all the dirents into our mapping.
    '''
    while len(dirdata) > 0:
      odirdata = dirdata
      E, dirdata = decodeDirent(dirdata)
      assert len(dirdata) < len(odirdata) and odirdata.endswith(dirdata)
      if E.name is None or len(E.name) == 0:
        # FIXME: skip unnamed dirent
        continue
      if E.name == '.' or E.name == '..':
        # FIXME: skip E.name
        continue
      if E.isdir():
        E.parent = self
      self._entries[E.name] = E

  def __validname(self, name):
    return len(name) > 0 and name.find('/') < 0

  def get(self, name, dflt=None):
    if name not in self:
      return dflt
    return self[name]

  def keys(self):
    return self.entries.keys()

  def __contains__(self, name):
    if name == '.':
      return True
    if name == '..':
      return self.parent is not None
    return name in self.entries

  def __iter__(self):
    return self.keys()

  def __getitem__(self, name):
    if name == '.':
      return self
    if name == '..':
      return self.parent
    return self.entries[name]

  def __setitem__(self, name, E):
    ''' Store a Dirent in the specified name slot.
    '''
    ##debug("<%s>[%s]=%s" % (self.name, name, E))
    assert self.__validname(name)
    assert name not in self
    assert isinstance(E, Dirent)
    self.entries[name] = E

  def __delitem__(self, name):
    assert self.__validname(name)
    assert name != '.' and name != '..'
    del self.entries[name]

  def getBlock(self):
    ''' Return the top Block referring to an encoding of this Dir.
    '''
    names = self.keys()
    names.sort()
    return blockFromString(
            "".join( self[name].encode()
                     for name in names
                     if name != '.' and name != '..'
                   ))

  def rename(self, oldname, newname):
    E = self[oldname]
    del E[oldname]
    E.name = newname
    self[newname] = E

  def open(self, name):
    from cs.venti.file import ReadFile
    return ReadFile(self[name].getBlock())

  def mkdir(self, name):
    debug("<%s>.mkdir(%s)..." % (self.name, name))
    D = self[name] = Dir(name, parent=self)
    return D

  def chdir1(self, name):
    D = self[name]
    assert D.isdir()
    if not isinstance(D, Dir):
      D = self[name] = Dir(D.name, parent=self)
    return D

  def chdir(self, path):
    D = self
    for name in path.split('/'):
      if len(name) == 0:
        continue
      D = D.chdir1(name)
    return D

  def makedirs(self, path):
    ''' Like os.makedirs(), create a directory path at need.
        Returns the bottom directory.
    '''
    D = self
    for name in path.split('/'):
      if len(name) == 0:
        continue
      if name == '.':
        continue
      if name == '..':
        D = D.parent
        continue
      E = D.get(name)
      if E is None:
        E = D.mkdir(name)
      else:
        assert E.isdir
      D = E
    return D

  def updateFrom(self,
                 osdir,
                 trust_size_mtime=False,
                 keep_missing=False,
                 ignore_existing=False):
    ''' Update this Dir from the real file tree at `osdir`.
        Return True if no errors occurred.
    '''
    ok = True
    with Pfx("updateFrom(%s,...)" % (osdir,)):
      assert os.path.isdir(osdir), "not a directory"
      osdirpfx = os.path.join(osdir, '')
      for dirpath, dirnames, filenames in os.walk(osdir, topdown=False):
        with Pfx(dirpath):
          if dirpath == osdir:
            D = self
          else:
            assert dirpath.startswith(osdirpfx), \
                    "dirpath=%s, osdirpfx=%s" % (dirpath, osdirpfx)
            subdirpath = dirpath[len(osdirpfx):]
            D = self.makedirs(subdirpath)

          if not keep_missing:
            allnames = set(dirnames)
            allnames.update(filenames)
            Dnames = list(D.keys())
            for name in Dnames:
              if name not in allnames:
                info("delete %s" % (name,))
                del D[name]

          for dirname in dirnames:
            if dirname in D:
              if not D[dirname].isdir():
                # old name is not a dir - toss it and make a dir
                del D[dirname]
                D.mkdir(dirname)

          for filename in filenames:
            with Pfx(filename):
              filepath = os.path.join(dirpath, filename)
              try:
                self.storeFile(filename, filepath,
                               trust_size_mtime=trust_size_mtime,
                               ignore_existing=ignore_existing)
              except OSError, e:
                error("stat: %s" % (e,))
                ok = False
                continue
              except IOError, e:
                error("stat: %s" % (e,))
                ok = False
                continue
    return ok

  def storeFile(self, filename, filepath,
                trust_size_mtime=False, ignore_existing=False):
    ''' Store as `filename` to file named by `filepath`.
    '''
    import  cs.venti.file
    with Pfx("%s.storeFile(%s, %s, trust_size_mtime=%s, ignore_existing=%s"
             % (self, filename, filepath, trust_size_mtime, ignore_existing)):
      if ignore_existing and filename in self:
        info("already exists, skipping")
        return
      st = os.stat(filepath)
      E = self.get(filename)
      if E is not None:
        if not E.isfile():
          # not a file, no blocks to match
          E = None
        else:
          if trust_size_mtime \
             and st.st_size == E.size() \
             and int(st.st_mtime) == int(E.mtime):
            info("same size and mtime, skipping")
            return
          info("differing size(%s:%s)/mtime(%s:%s)"
                % (st.st_size, E.size(),
                   int(st.st_mtime), int(E.mtime)))

      info("storing")
      # TODO: M.updateFromStat(st)
      M = Meta()
      M.mtime = st.st_mtime
      if E is None:
        matchBlocks = None
      else:
        matchBlocks = E.getBlock().leaves()
      with open(filepath) as ifp:
        stored = cs.venti.file.storeFile(ifp, name=filename, meta=M,
                                         matchBlocks=matchBlocks)
      if filename in self:
        del self[filename]
      self[filename] = stored
