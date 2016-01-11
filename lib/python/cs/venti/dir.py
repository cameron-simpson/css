import os
import os.path
from collections import namedtuple
import pwd
import grp
import stat
import sys
from threading import Lock, RLock
import time
from cs.logutils import D, Pfx, debug, error, info, warning, X, XP
from cs.lex import hexify, texthexify
from cs.py.stack import stack_dump
from cs.queues import MultiOpenMixin
from cs.seq import seq
from cs.serialise import get_bs, get_bsdata, get_bss, put_bs, put_bsdata, put_bss
from cs.threads import locked, locked_property
from . import totext, fromtext
from .block import Block, decodeBlock
from .file import File
from .meta import Meta

uid_nobody = -1
gid_nogroup = -1

# Directories (Dir, a subclass of dict) and directory entries (_Dirent).

D_FILE_T = 0
D_DIR_T = 1
D_SYM_T = 2
D_HARD_T = 3
def D_type2str(type_):
  if type_ == D_FILE_T:
    return "D_FILE_T"
  if type_ == D_DIR_T:
    return "D_DIR_T"
  if type_ == D_SYM_T:
    return "D_SYM_T"
  if type_ == D_HARD_T:
    return "D_HARD_T"
  return str(type_)

F_HASMETA = 0x01
F_HASNAME = 0x02
F_NOBLOCK = 0x04

def decode_Dirent_text(text):
  ''' Accept `text`, a text transcription of a Direct, such as from
      Dirent.textencode(), and return the corresponding Dirent.
  '''
  data = fromtext(text)
  E, offset = decode_Dirent(data, 0)
  if offset < len(data):
    raise ValueError("%r: not all text decoded: got %r with unparsed data %r"
                     % (text, E, data[offset:]))
  return E

class DirentComponents(namedtuple('DirentComponents', 'type name metatext block')):

  @classmethod
  def from_data(cls, data, offset=0):
    ''' Unserialise a serialised Dirent, return (DirentComponents, offset).
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
      metatext, offset = get_bss(data, offset)
    else:
      metatext = None
    if flags & F_NOBLOCK:
      block = None
    else:
      block, offset = decodeBlock(data, offset)
    return cls(type_, name, metatext, block), offset

  def encode(self):
    ''' Serialise the components.
        Output format: bs(type)bs(flags)[bsdata(name)][bsdata(metadata)]block
    '''
    flags = 0
    name = self.name
    if name:
      flags |= F_HASNAME
      namedata = put_bsdata(name.encode())
    else:
      namedata = b''
    meta = self.metatext
    if meta:
      flags |= F_HASMETA
      if isinstance(meta, str):
        metadata = put_bss(meta)
      else:
        metadata = put_bss(meta.textencode())
    else:
      metadata = b''
    block = self.block
    if block is None:
      flags |= F_NOBLOCK
      blockref = b''
    else:
      blockref = block.encode()
    return put_bs(self.type) \
         + put_bs(flags) \
         + namedata \
         + metadata \
         + blockref

def decode_Dirent(data, offset):
  ''' Unserialise a Dirent, return (Dirent, offset).
  '''
  offset0 = offset
  components, offset = DirentComponents.from_data(data, offset)
  type_, name, metatext, block = components
  try:
    if type_ == D_DIR_T:
      E = Dir(name, metatext=metatext, parent=None, block=block)
    elif type_ == D_FILE_T:
      E = FileDirent(name, metatext=metatext, block=block)
    elif type_ == D_SYM_T:
      E = SymlinkDirent(name, metatext=metatext)
    elif type_ == D_HARD_T:
      E = HardlinkDirent(name, metatext=metatext)
    else:
      E = _Dirent(type_, name, metatext=metatext, block=block)
  except ValueError as e:
    warning("%r: invalid DirentComponents, marking Dirent as invalid: %s: %s",
            name, e, components)
    E = InvalidDirent(components, data[offset0:offset])
  return E, offset

def decode_Dirents(dirdata, offset=0):
  ''' Decode and yield all the Dirents from the supplied bytes `dirdata`.
      Return invalid Dirent data chunks and valid Dirents.
  '''
  while offset < len(dirdata):
    E, offset = decode_Dirent(dirdata, offset)
    yield E

class _Dirent(object):
  ''' Incomplete base class for Dirent objects.
  '''

  def __init__(self, type_, name, metatext=None):
    if not isinstance(type_, int):
      raise TypeError("type_ is not an int: <%s>%r" % (type(type_), type_))
    if name is not None and not isinstance(name, str):
      raise TypeError("name is neither None nor str: <%s>%r" % (type(name), name))
    self.type = type_
    self.name = name
    self.meta = Meta(self)
    if metatext is not None:
      if isinstance(metatext, str):
        self.meta.update_from_text(metatext)
      else:
        self.meta.update_from_items(metatext.items())

  def __str__(self):
    return self.textencode()

  def __repr__(self):
    return "%s(%s, %s, %s)" % (self.__class__.__name__, D_type2str, self.name, self.meta)

  # TODO: support .block=None
  def __eq__(self, other):
    return ( self.name == other.name
         and self.type == other.type
         and self.meta == other.meta
         and self.block.hashcode == other.block.hashcode
           )

  @property
  def isfile(self):
    ''' Is this a file _Dirent?
    '''
    return self.type == D_FILE_T

  @property
  def isdir(self):
    ''' Is this a directory _Dirent?
    '''
    return self.type == D_DIR_T

  @property
  def issym(self):
    ''' Is this a symbolic link _Dirent?
    '''
    return self.type == D_SYM_T

  @property
  def ishardlink(self):
    ''' Is this a hard link _Dirent?
    '''
    return self.type == D_HARD_T

  def encode(self):
    ''' Serialise this dirent.
    '''
    type_ = self.type
    name = self.name
    meta = self.meta
    if self.issym or self.ishardlink:
      block = None
    else:
      block = self.block
    return DirentComponents(type_, name, meta, block).encode()

  def textencode(self):
    ''' Serialise the dirent as text.
        Output format: bs(type)bs(flags)[bs(namelen)name][bs(metalen)meta]block
    '''
    flags = 0

    name = self.name
    if name is None or len(name) == 0:
      nametxt = ""
    else:
      nametxt = totext(put_bsdata(name.encode()))
      flags |= F_HASNAME

    meta = self.meta
    if meta:
      if not isinstance(meta, Meta):
        raise TypeError("self.meta is not a Meta: <%s>%r" % (type(meta), meta))
      metatxt = meta.textencode()
      if metatxt == meta.dflt_acl_text:
        metatxt = ''
      if len(metatxt) > 0:
        metatxt = totext(put_bsdata(metatxt.encode()))
        flags |= F_HASMETA
    else:
      metatxt = ""

    if self.issym or self.ishardlink:
      blocktxt = ''
    else:
      blocktxt = self.block.textencode()
    return ( hexify(put_bs(self.type))
           + hexify(put_bs(flags))
           + nametxt
           + metatxt
           + blocktxt
           )

  @property
  def size(self):
    block = self.block
    return None if block is None else len(block)

  @property
  def mtime(self):
    return self.meta.mtime

  @mtime.setter
  def mtime(self, newtime):
    self.meta.mtime = newtime

  def touch(self, when=None):
    ''' Set the mtime for this file to `when` (default now: time.time()).
    '''
    if when is None:
      when = time.time()
    self.mtime = when

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

    dev = None          # make it clear that we have no associated filesystem
    nlink = 1
    ino = None          # also needs a filesystem
    size = self.size
    atime = 0
    mtime = self.mtime
    ctime = 0

    return (unixmode, ino, dev, nlink, uid, gid, size, atime, mtime, ctime)

class InvalidDirent(_Dirent):

  def __init__(self, components, chunk):
    ''' An invalid Dirent. Record the original data chunk for regurgitation later.
    '''
    self.components = components
    self.chunk = chunk
    self.type = components.type
    self.name = components.name
    self.metatext = components.metatext
    self.block = components.block
    self._meta = None

  def __str__(self):
    return '<InvalidDirent:%s:%s>' % (self.components, texthexify(self.chunk))

  def encode(self):
    ''' Return the original data chunk.
    '''
    return self.chunk

  @property
  def meta(self):
    M = self._meta
    if M is None:
      M = self.metatext
      if M is None:
        M = Meta(self)
      elif isinstance(M, str):
        M = Meta.from_text(meta, self)
      self._meta = M
    return M

class SymlinkDirent(_Dirent):

  def __init__(self, name, metatext, block=None):
    if block is not None:
      raise ValueError("SymlinkDirent: block must be None, received: %s", block)
    _Dirent.__init__(self, D_SYM_T, name, metatext=metatext)
    if self.meta.pathref is None:
      raise ValueError("SymlinkDirent: meta.pathref required")

  @property
  def pathref(self):
    return self.meta.pathref

class HardlinkDirent(_Dirent):
  ''' A hard link.
      Unlike the regular UNIX filesystem, in a venti filesystem a
      hard link is a wrapper for an ordinary Dirent; this wrapper references
      a persistent inode number and the source Dirent. Most attributes
      are proxied from the wrapped Dirent.
      In a normal Dirent .inum is a local attribute and not preserved;
      in a HardlinkDirent it is a proxy for the local .meta.inum.
  '''

  def __init__(self, name, metatext, block=None):
    if block is not None:
      raise ValueError("HardlinkDirent: block must be None, received: %s", block)
    _Dirent.__init__(self, D_HARD_T, name, metatext=metatext)
    if not hasattr(self.meta, 'inum'):
      raise ValueError("HardlinkDirent: meta.inum required (no iref in metatext=%r)" % (metatext,))

  @property
  def inum(self):
    ''' On a HardlinkDirent the .inum accesses the meta['iref'] field.
        It is set at initialisation, so there is no .setter.
    '''
    return self.meta.inum

  @classmethod
  def to_inum(cls, inum, name):
    return cls(name, {'iref': str(inum)})

class FileDirent(_Dirent, MultiOpenMixin):
  ''' A _Dirent subclass referring to a file.
      If closed, ._block refers to the file content.
      If open, ._open_file refers to the content.
      NOTE: multiple opens return the _same_ backing file, with a
      shared read/write offset. File systems must share this, and
      keep their own offsets in their file handles.
  '''

  def __init__(self, name, metatext=None, block=None):
    if block is None:
      block = Block(data=b'')
    MultiOpenMixin.__init__(self)
    _Dirent.__init__(self, D_FILE_T, name, metatext=metatext)
    self._open_file = None
    self._block = block
    self._check()

  def _check(self):
    # TODO: check ._block and ._open_file against MultiOpenMixin open count
    if self._block is None:
      if self._open_file is None:
        raise ValueError("both ._block and ._open_file are None")
    ## both are allowed to be set
    ##elif self._open_file is not None:
    ##  raise ValueError("._block is %s and ._open_file is %r" % (self._block, self._open_file))

  @property
  @locked
  def block(self):
    ''' Obtain the top level Block.
        If open, sync the file to update ._block.
    '''
    self._check()
    if self._open_file is not None:
      self._block = self._open_file.flush()
      warning("FileDirent.block: updated to %s", self._block)
      ##stack_dump(indent=2)
    return self._block

  @block.setter
  @locked
  def block(self, B):
    if self._open_file is not None:
      raise RuntimeError("tried to set .block directly while open")
    self._block = B

  @property
  @locked
  def size(self):
    ''' Return the size of this file.
        If open, use the open file's size.
        Otherwise get the length of the top Block.
    '''
    self._check()
    if self._open_file is not None:
      return len(self._open_file)
    return len(self.block)

  @locked
  def startup(self):
    ''' Set up ._open_file on first open.
    '''
    self._check()
    if self._open_file is not None:
      raise RuntimeError("first open, but ._open_file is not None: %r" % (self._open_file,))
    if self._block is None:
      raise RuntimeError("first open, but ._block is None")
    self._open_file = File(self._block)
    self._block = None
    self._check()

  @locked
  def shutdown(self):
    ''' On final close, close ._open_file and save result as ._block.
    '''
    X("CLOSE %s ...", self)
    self._check()
    if self._block is not None:
      error("final close, but ._block is not None; replacing with self._open_file.close(), was: %r", self._block)
    self._block = self._open_file.close()
    X("CLOSE %s: _block=%s", self, self._block)
    self._open_file = None
    self._check()

  def truncate(self, length):
    ''' Truncate this FileDirent to the specified size.
    '''
    Esize = self.size
    if Esize != length:
      with self:
        return self._open_file.truncate(length)

  def restore(self, path, makedirs=False, verbosefp=None):
    ''' Restore this _Dirent's file content to the name `path`.
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
        for B in self.block.leaves():
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

class Dir(_Dirent):
  ''' A directory.

      .changed  Starts False, becomes true if this or any subdirectory
                gets changed or has a file opened; stays True from then on.
                This accepts an ongoing compute cost for .block to avoid
                setting the flag on every file.write etc.
  '''

  def __init__(self, name, metatext=None, parent=None, block=None):
    ''' Initialise this directory.
        `metatext`: meta information
        `parent`: parent Dir
        `block`: pre-existing Block with initial Dir content
    '''
    if block is None:
      self._block = None
      self._entries = {}
    else:
      self._block = block
      self._entries = None
    _Dirent.__init__(self, D_DIR_T, name, metatext=metatext)
    self._unhandled_dirent_chunks = None
    self.parent = parent
    self.changed = False
    self._lock = RLock()

  @locked
  def change(self):
    ''' Mark this Dir as changed; propagate to parent Dir if present.
    '''
    XP("Dir %r: changed=True", self.name)
    ##stack_dump(indent=2)
    self.changed = True
    if self.parent:
      self.parent.change()

  @property
  @locked
  def entries(self):
    ''' Property containing the live dictionary holding the Dir entries.
    '''
    emap = self._entries
    if emap is None:
      # compute the dictionary holding the live Dir entries
      emap = {}
      for E in decode_Dirents(self._block.all_data()):
        E.parent = self
        emap[E.name] = E
      self._entries = emap
    return emap

  @property
  @locked
  def block(self):
    ''' Return the top Block referring to an encoding of this Dir.
        TODO: blockify the encoding? Probably desirable for big Dirs.
    '''
    if self._block is None or self.changed:
      # recompute in case of change
      # restore the unparsed Dirents from initial load
      if self._unhandled_dirent_chunks is None:
        data = b''
      else:
        data = b''.join(self._unhandled_dirent_chunks)
      # append the valid or new Dirents
      names = sorted(self.keys())
      data += b''.join( self[name].encode()
                        for name in names
                        if name != '.' and name != '..'
                      )
      # TODO: if len(data) >= 16384
      B = self._block = Block(data=data)
      self.changed = False
      ##warning("Dir.block: computed Block %s", B)
      ##XP("Dir %r: RECOMPUTED BLOCK: %s", self.name, B)
    else:
      B = self._block
      ##XP("Dir %r: REUSE ._block: %s", self.name, B)
    return B

  def dirs(self):
    ''' Return a list of the names of subdirectories in this Dir.
    '''
    return [ name for name in self.keys() if self[name].isdir ]

  def files(self):
    ''' Return a list of the names of files in this Dir.
    '''
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
    ''' Store a _Dirent in the specified name slot.
    '''
    if not self._validname(name):
      raise KeyError("invalid name: %s" % (name,))
    if not isinstance(E, _Dirent):
      raise ValueError("E is not a _Dirent: <%s>%r" % (type(E), E))
    self.change()
    self.entries[name] = E
    E.name = name
    if E.isdir:
      Eparent = E.parent
      if Eparent is None:
        E.parent = D
      elif Eparent is not self:
        warning("%s: changing %r.parent to self, was %s", self, name, Eparent)
        E.parent = self

  def __delitem__(self, name):
    if not self._validname(name):
      raise KeyError("invalid name: %s" % (name,))
    if name == '.' or name == '..':
      raise KeyError("refusing to delete . or ..: name=%s" % (name,))
    self.change()
    del self.entries[name]

  def add(self, E):
    ''' Add a Dirent to this Dir.
        If the name is already taken, raise KeyError.
    '''
    name = E.name
    if name in self:
      raise KeyError("name already exists: %r", name)
    self[name] = E

  @locked
  def rename(self, oldname, newname):
    ''' Rename entry `oldname` to entry `newname`.
    '''
    E = self[oldname]
    del E[oldname]
    E.name = newname
    self[newname] = E

  def mkdir(self, name):
    ''' Create a subdirectory named `name`, return the _Dirent.
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

  def makedirs(self, path, force=False):
    ''' Like os.makedirs(), create a directory path at need; return the bottom Dir.
        If `force`, replace any non-Dir encountered with an empty Dir.
    '''
    E = self
    if isinstance(path, str):
      subpaths = path_split(path)
    else:
      subpaths = path
    for name in subpaths:
      if name == '' or name == '.':
        continue
      if name == '..':
        E = E.parent
        continue
      subE = E.get(name)
      if subE is None:
        subE = E.mkdir(name)
      else:
        if not subE.isdir:
          if force:
            subE = E.mkdir(name)
          else:
            raise ValueError("%s[name=%s] is not a directory" % (subE, name))
      E = subE

    return E

if __name__ == '__main__':
  import cs.venti.dir_tests
  cs.venti.dir_tests.selftest(sys.argv)
