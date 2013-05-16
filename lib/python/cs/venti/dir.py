import os
import os.path
import pwd
import grp
import stat
import sys
from threading import Lock
from cs.logutils import D, Pfx, debug, error, info, warning
from cs.lex import hexify
from cs.seq import seq
from cs.serialise import get_bs, get_bsdata, put_bs, put_bsdata
from cs.threads import locked_property
from . import totext, fromtext
from .block import Block, decodeBlock
from .blockify import blockFromString
from .meta import Meta

uid_nobody = -1
gid_nogroup = -1

# Directories (Dir, a subclass of dict) and directory entries (Dirent).

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

def decode_Dirent_text(text):
  ''' Accept `text`, a text transcription of a Direct, such as from
      Dirent.textencode(), and return the correspnding Dirent.
  '''
  data = fromtext(text)
  E, offset = decodeDirent(data, 0)
  if offset < len(data):
    raise ValueError("%r: not all text decoded: got %r with unparsed data %r"
                     % (text, E, data[offset:]))
  return E

def decodeDirent(data, offset):
  ''' Unserialise a Dirent, return (dirent, offset).
      Input format: bs(type)bs(flags)[bs(namelen)name][bs(metalen)meta]block
  '''
  type_, offset = get_bs(data, offset)
  flags, offset = get_bs(data, offset)
  if flags & F_HASNAME:
    namedata, offset = get_bsdata(data, offset)
    name = namedata.decode()
  else:
    name = ""
  meta = None
  if flags & F_HASMETA:
    metadata, offset = get_bsdata(data, offset)
    meta = Meta(metadata.decode())
  else:
    meta = Meta()
  block, offset = decodeBlock(data, offset)
  if type_ == D_DIR_T:
    E = Dir(name, meta=meta, parent=None, dirblock=block)
  elif type_ == D_FILE_T:
    E = FileDirent(name, meta=meta, block=block)
  else:
    E = _BasicDirent(type_, name, meta, block)
  return E, offset

def decodeDirents(dirdata, offset=0):
  ''' Yield Dirents from the supplied bytes `dirdata`.
  '''
  while offset < len(dirdata):
    E, offset = decodeDirent(dirdata, offset)
    if E.name is None or len(E.name) == 0:
      # FIXME: skip unnamed dirent
      warning("skip unnamed Dirent")
      continue
    if E.name == '.' or E.name == '..':
      continue
    yield E

class Dirent(object):
  ''' Incomplete base class for Dirent objects.
  '''

  def __init__(self, type_, name, meta=None):
    if not isinstance(type_, int):
      raise TypeError("type_ is not an int: <%s>%r" % (type(type_), type_))
    if name is not None and not isinstance(name, str):
      raise TypeError("name is neither None nor str: <%s>%r" % (type(name), name))
    if meta is None:
      meta = Meta()
    else:
      if not isinstance(meta, Meta):
        raise TypeError("meta is not a Meta: <%s>%r" % (type(meta), meta))
    self.type = type_
    self.name = name
    self.meta = meta
    self.d_ino = None

  def __str__(self):
    return self.textencode()

  def __repr__(self):
    return "Dirent(%s, %s, %s)" % (D_type2str, self.name, self.meta)

  @property
  def isfile(self):
    ''' Is this a file Dirent?
    '''
    return self.type == D_FILE_T

  @property
  def isdir(self):
    ''' Is this a directory Dirent?
    '''
    return self.type == D_DIR_T

  def updateFromStat(self, st):
    self.meta.updateFromStat(st)

  def encode(self, no_name=False):
    ''' Serialise the dirent.
        Output format: bs(type)bs(flags)[bsdata(metadata)][bsdata(name)]block
    '''
    flags = 0

    if no_name:
      name = ""
    name = self.name
    if name is None:
      name = ""
    if name:
      namedata = put_bsdata(name.encode())
      flags |= F_HASNAME
    else:
      namedata = b''

    meta = self.meta
    if meta:
      if not isinstance(meta, Meta):
        raise TypeError("self.meta is not a Meta: <%s>%r" % (type(meta), meta))
      metadata = put_bsdata(meta.encode())
      if len(metadata) > 0:
        flags |= F_HASMETA
    else:
      metadata = b''

    block = self.getBlock()
    return put_bs(self.type) \
         + put_bs(flags) \
         + namedata \
         + metadata \
         + block.encode()

  def textencode(self):
    ''' Serialise the dirent as text.
        Output format: bs(type)bs(flags)[bs(metalen)meta][bs(namelen)name]block
    '''
    flags = 0

    meta = self.meta
    if meta:
      if not isinstance(meta, Meta):
        raise TypeError("self.meta is not a Meta: <%s>%r" % (type(meta), meta))
      metatxt = meta.textencode()
      if len(metatxt) > 0:
        metatxt = totext(put_bsdata(metatxt.encode()))
        flags |= F_HASMETA
    else:
      metatxt = ""

    name = self.name
    if name is None or len(name) == 0:
      nametxt = ""
    else:
      nametxt = totext(put_bsdata(name.encode()))
      flags |= F_HASNAME

    block = self.getBlock()
    return ( hexify(put_bs(self.type))
           + hexify(put_bs(flags))
           + metatxt
           + nametxt
           + block.textencode()
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
    if self.isdir:
      return self.asdir()[name]
    raise KeyError("\"%s\" not in %s" % (name, self))

class FileDirent(_BasicDirent):

  def __init__(self, name, meta, block):
    _BasicDirent.__init__(self, D_FILE_T, name, meta, block)

  def restore(self, path, makedirs=False, verbosefp=None):
    ''' Restore this Dirent's file content to the name `path`.
    '''
    with Pfx("FileDirent.restore(%s)", path):
      if verbosefp is not None:
        verbosefp.write(path)
        verbosefp.write('\n')
      dirpath = os.path.dirname(path)
      if len(dirpath) and not os.path.isdir(dirpath):
        if makedirs:
          os.makedirs(dirpath)
      with open(path, "wb") as ofp:
        for B in self.getBlock().leaves():
          ofp.write(B.blockdata())
        fd = ofp.fileno()
        st = os.fstat(fd)
        user, group, perms = self.meta.unixPerms()
        if user is not None or group is not None:
          os.fchmod(fd, perms)
        if user is None:
          uid = -1
        else:
          uid = pwd.getpwnam(user)[2]
          if uid == st.st_uid:
            uid = -1
        if group is None:
          gid = -1
        else:
          gid = grp.getgrnam(group)[2]
          if gid == st.st_gid:
            gid = -1
        if uid != -1 or gid != -1:
          os.fchown(fd, uid, gid)
      if self.meta.mtime is not None:
        os.utime(path, (st.st_atime, self.meta.mtime))

class Dir(Dirent):
  ''' A directory.
  '''

  def __init__(self, name, meta=None, parent=None, dirblock=None):
    ''' Initialise this directory.
        `meta`: meta information
        `parent`: parent Dir
        `dirblock`: pre-existing Block with initial Dir content
    '''
    self._lock = Lock()
    if meta is None:
      meta = Meta()
    Dirent.__init__(self, D_DIR_T, name, meta)
    self.parent = parent
    self.entries = {}
    if dirblock:
      for E in decodeDirents(dirblock.data):
        E.parent = self
        self[E.name] = E
    self._lock = Lock()

  def dirs(self):
    return [ name for name in self.keys() if self[name].isdir ]

  def files(self):
    return [ name for name in self.keys() if self[name].isfile ]

  def _validname(self, name):
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
    if not self._validname(name):
      raise KeyError("invalid name: %s" % (name,))
    if not isinstance(E, Dirent):
      raise ValueError("E is not a Dirent: <%s>%r" % (type(E), E))
    self.entries[name] = E

  def __delitem__(self, name):
    if not self._validname(name):
      raise KeyError("invalid name: %s" % (name,))
    if name == '.' or name == '..':
      raise KeyError("refusing to delete . or ..: name=%s" % (name,))
    del self.entries[name]

  def getBlock(self):
    ''' Return the top Block referring to an encoding of this Dir.
    '''
    names = sorted(self.keys())
    data = b''.join( self[name].encode()
                     for name in names
                     if name != '.' and name != '..'
                   )
    return Block(data=data)

  def rename(self, oldname, newname):
    ''' Rename entry `oldname` to entry `newname`.
    '''
    E = self[oldname]
    del E[oldname]
    E.name = newname
    self[newname] = E

  def open(self, name):
    ''' Open the entry named `name` as a readable file-like object.
    '''
    from .file import ReadFile
    return ReadFile(self[name].getBlock())

  def mkdir(self, name):
    ''' Create a subdirectory named `name`, return the Dirent.
    '''
    debug("<%s>.mkdir(%s)..." % (self.name, name))
    D = self[name] = Dir(name, parent=self)
    return D

  def chdir1(self, name):
    ''' Change directory to the immediate entry `name`.
        Return the entry.
    '''
    D = self[name]
    if not D.isdir:
      raise ValueError("%s[name=%s]: not a directory" % (self, name))
    return D

  def chdir(self, path):
    ''' Change directory to `path`, return the ending directory.
    '''
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
        if not E.isdir:
          raise ValueError("%s[name=%s] is not a directory" % (D, name))
      D = E

    return D
