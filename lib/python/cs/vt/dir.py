#!/usr/bin/env python3
#

''' Implementation of directories (Dir) and their entries (FileDirent, etc).
'''

import os
import os.path
from cmd import Cmd
from collections import OrderedDict
import errno
from getopt import GetoptError
import grp
import pwd
import shlex
import stat
import sys
from threading import RLock
import time
from uuid import UUID, uuid4
from cs.cmdutils import docmd
from cs.logutils import debug, error, warning
from cs.pfx import Pfx
from cs.lex import texthexify
from cs.py.func import prop
from cs.queues import MultiOpenMixin
from cs.serialise import get_bs, get_bsdata, get_bss, put_bs, put_bsdata, put_bss
from cs.threads import locked, locked_property
from cs.x import X
from . import totext, PATHSEP, defaults
from .block import Block, decodeBlock, encodeBlock, _Block
from .file import File
from .meta import Meta, rwx
from .paths import path_split, resolve
from .transcribe import Transcriber, \
                        register as register_transcriber

uid_nobody = -1
gid_nogroup = -1

# Directories (Dir, a subclass of dict) and directory entries (_Dirent).

D_INVALID_T = -1
D_FILE_T = 0
D_DIR_T = 1
D_SYM_T = 2
D_HARD_T = 3
def D_type2str(type_):
  ''' Convert a numeric Dirent type value to a string.
  '''
  if type_ == D_FILE_T:
    return "D_FILE_T"
  if type_ == D_DIR_T:
    return "D_DIR_T"
  if type_ == D_SYM_T:
    return "D_SYM_T"
  if type_ == D_HARD_T:
    return "D_HARD_T"
  return str(type_)

F_HASMETA = 0x01        # has metadata
F_HASNAME = 0x02        # has a name
F_NOBLOCK = 0x04        # has no Block reference
F_HASUUID = 0x08        # has a UUID
F_PREVDIRENT = 0x10     # has reference to serialised previous Dirent

def Dirents_from_data(data, offset=0):
  ''' Decode Dirents from `data`, yield each in turn.
      `data`: the data to decode.
      `offset`: the starting offset within the data, default 0.
  '''
  while offset < len(data):
    E, offset = _Dirent.from_bytes(data, offset)
    yield E

class _Dirent(Transcriber):
  ''' Incomplete base class for Dirent objects.
  '''

  def __init__(
      self,
      type_, name,
      meta=None,
      uuid=None,
      parent=None,
      prevblock=None
  ):
    if not isinstance(type_, int):
      raise TypeError("type_ is not an int: <%s>%r" % (type(type_), type_))
    if name is not None and not isinstance(name, str):
      raise TypeError("name is neither None nor str: <%s>%r" % (type(name), name))
    self.type = type_
    self.name = name
    self._uuid = uuid
    assert prevblock is None or isinstance(prevblock, _Block), \
        "not _Block: prevblock=%r" % (prevblock,)
    self._prev_dirent_blockref = prevblock
    if isinstance(meta, Meta):
      if meta.E is not None and meta.E is not self:
        warning("meta.E is %r, replacing with self %r", meta.E, self)
      meta.E = self
    else:
      M = Meta(self)
      if meta is None:
        pass
      elif isinstance(meta, str):
        M.update_from_text(meta)
      else:
        raise ValueError("unsupported meta value: %r" % (meta,))
      meta = M
    self.meta = meta
    self.parent = parent

  def __repr__(self):
    return "%s:%d(%s,%s,%s)" % (
        self.__class__.__name__,
        id(self),
        D_type2str(self.type),
        self.name,
        self.meta
    )

  @classmethod
  def from_bytes(cls, data, offset=0):
    ''' Unserialise a serialised Dirent, return (Dirent, offset).
        Input format: bs(type)bs(flags)[bs(namelen)name][bs(metalen)meta][uuid:16]blockref[blockref(pref_dirent)]
    '''
    offset0 = offset
    type_, offset = get_bs(data, offset)
    flags, offset = get_bs(data, offset)
    if flags & F_HASNAME:
      namedata, offset = get_bsdata(data, offset)
      name = bytes(namedata).decode()
    else:
      name = ""
    if flags & F_HASMETA:
      metatext, offset = get_bss(data, offset)
    else:
      metatext = None
    uu = None
    if flags & F_HASUUID:
      uubs = data[:16]
      offset += 16
      if offset > len(data):
        raise ValueError(
            "needed 16 bytes for UUID, only got %d bytes (%r)"
            % (len(uubs), uubs))
      uu = UUID(bytes=uubs)
    if flags & F_NOBLOCK:
      block = None
    else:
      block, offset = decodeBlock(data, offset)
    if flags & F_PREVDIRENT:
      prev_dirent_blockref, offset = decodeBlock(data, offset)
    else:
      prev_dirent_blockref = None
    try:
      E = cls.from_components(type_, name, meta=metatext, uuid=uu, block=block)
    except ValueError as e:
      warning("%r: invalid Dirent components, marking Dirent as invalid: %s",
              name, e)
      E = InvalidDirent(
          type_, name,
          chunk=data[offset0:offset],
          meta=metatext, uuid=uu, block=block)
    E._prev_dirent_blockref = prev_dirent_blockref
    return E, offset

  @staticmethod
  def from_components(type_, name, **kw):
    ''' Factory returning a _Dirent instance.
    '''
    if type_ == D_DIR_T:
      cls = Dir
    elif type_ == D_FILE_T:
      cls = FileDirent
    elif type_ == D_SYM_T:
      cls = SymlinkDirent
    elif type_ == D_HARD_T:
      cls = HardlinkDirent
    else:
      cls = InvalidDirent
    return cls(name, **kw)

  def encode(self):
    ''' Serialise to binary format.
        Output format: bs(type)bs(flags)[bsdata(name)][bsdata(metadata)][uuid]blockref
    '''
    flags = 0
    name = self.name
    if name:
      flags |= F_HASNAME
      namedata = put_bsdata(name.encode())
    else:
      namedata = b''
    meta = self.meta
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
      blockref = encodeBlock(block)
    uu = self._uuid
    if uu is None:
      uubs = b''
    else:
      flags |= F_HASUUID
      uubs = uu.bytes
    prev_dirent_blockref = self._prev_dirent_blockref
    if prev_dirent_blockref is None:
      prev_dirent_bs = b''
    else:
      assert isinstance(prev_dirent_blockref, _Block)
      flags |= F_PREVDIRENT
      prev_dirent_bs = encodeBlock(prev_dirent_blockref)
    return (
        put_bs(self.type)
        + put_bs(flags)
        + namedata
        + metadata
        + uubs
        + blockref
        + prev_dirent_bs
    )

  def __hash__(self):
    ''' Allows collecting _Dirents in a set.
    '''
    return id(self)

  def transcribe_inner(self, T, fp, attrs):
    if self.name:
      T.transcribe(self.name, fp=fp)
      fp.write(':')
    if self._uuid:
      attrs['uuid'] = self._uuid
    if self.meta:
      attrs['meta'] = self.meta
    if self.block:
      attrs['block'] = self.block
    prev_blockref = self._prev_dirent_blockref
    if prev_blockref is not None:
      attrs['prevblock'] = prev_blockref
    T.transcribe_mapping(attrs, fp)

  @classmethod
  def parse_inner(cls, T, s, offset, stopchar, prefix):
    ''' Parse [name:]attrs from `s` at offset `offset`. Return _Dirent instance and new offset.
    '''
    name, offset2 = T.parse_qs(s, offset, optional=True)
    if name is None:
      name = ''
    else:
      if s[offset2] != ':':
        raise ValueError("offset %d: missing colon after name" % (offset2,))
      offset = offset2 + 1
    attrs, offset = T.parse_mapping(s, offset, stopchar)
    type_ = {
        'F': D_FILE_T,
        'D': D_DIR_T,
        'SymLink': D_SYM_T,
        'HardLink': D_HARD_T,
    }.get(prefix)
    return cls.from_components(type_, name, **attrs), offset

  @prop
  def uuid(self):
    ''' Return this Dirent's UUID, creating it if necessary.
    '''
    u = self._uuid
    if u is None:
      u = self._uuid = uuid4()
    return u

  def pathto(self, R=None):
    ''' Return the path to this element if known.
        `R`: optional root Dirent, default None.
    '''
    E = self
    path = []
    while E is not R:
      path.append(E.name)
      E = E.parent
    return os.path.join(*reversed(path))

  # TODO: support .block=None
  def __eq__(self, other):
    return (
        self.name == other.name
        and self.type == other.type
        and self.meta == other.meta
        and self.block == other.block
    )

  @locked_property
  def prev_dirent(self):
    ''' Return the previous Dirent.
        If not None, during encoding or transcription, if self !=
        prev_dirent, include it in the encoding or transcription.
    '''
    prev_blockref = self._prev_dirent_blockref
    if prev_blockref is None:
      return None
    data = prev_blockref.data
    E, offset = _Dirent.from_bytes(data)
    if offset < len(data):
      warning(
          "prev_dirent: _prev_dirent_blockref=%s: unparsed bytes after dirent at offset %d: %r",
          prev_blockref, offset, data[offset:])
    return E

  @prev_dirent.setter
  @locked
  def prev_dirent(self, E):
    ''' Set the previous Dirent.
    '''
    assert isinstance(E, _Dirent), "set .prev_dirent: not a _Dirent: %s" % (E,)
    self._prev_dirent = None
    Ebs = E.encode()
    self._prev_dirent_blockref = Block(data=Ebs)
    self.changed = True

  def snapshot(self):
    ''' Update the Dirent's previous block state if missing or changed.
    '''
    E = self.prev_dirent
    if E is None or E != self:
      self.prev_dirent = self

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

  def textencode(self):
    ''' Serialise the dirent as text.
    '''
    return totext(self.encode())

  @property
  def size(self):
    ''' Return this Dirent's length: its Block's span length.
    '''
    block = self.block
    return None if block is None else len(block)

  @property
  def mtime(self):
    ''' Return this Dirent's modification time (meta.mtime).
    '''
    return self.meta.mtime

  @mtime.setter
  def mtime(self, newtime):
    ''' Set this Dirent's modification time (meta.mtime).
    '''
    self.meta.mtime = newtime

  def touch(self, when=None):
    ''' Set the mtime for this file to `when` (default now: time.time()).
    '''
    if when is None:
      when = time.time()
    self.mtime = when

  def stat(self):
    ''' Return this Dirent's meta.stat().
    '''
    return self.meta.stat()

  def complete(self, S2, recurse=False):
    ''' Complete this Dirent from alternative Store `S2`.
        TODO: parallelise like _Block.complete.
    '''
    self.block.complete(S2)
    if self.isdir and recurse:
      for name, entry in self.entries.items():
        if name != '.' and name != '..':
          entry.complete(S2, True)

register_transcriber(_Dirent, (
    'INVALIDDirent',
    'SymLink',
    'HardLink',
    'D',
    'F',
))

class InvalidDirent(_Dirent):
  ''' Encapsulation for an invalid Dirent data chunk.
  '''

  transcribe_prefix = 'INVALIDDirent'

  def __init__(self, name, *, chunk=None, **kw):
    ''' An invalid Dirent. Record the original data chunk for regurgitation later.
    '''
    _Dirent.__init__(
        self,
        D_INVALID_T,
        name,
        block=None,
        **kw)
    self.chunk = chunk

  def __str__(self):
    return '<InvalidDirent:%s:%s>' % (self.components, texthexify(self.chunk))

  def encode(self):
    ''' Return the original data chunk.
    '''
    return self.chunk

  def transcribe_inner(self, T, fp):
    ''' Transcribe the inner components of this InvalidDirent's transcription.
    '''
    attrs = OrderedDict()
    attrs['block'] = self.block     # data block if any
    attrs['chunk'] = self.chunk     # original encoded data
    return super().transcribe_inner(T, fp, attrs)

class SymlinkDirent(_Dirent):
  ''' A symbolic link.
  '''

  transcribe_prefix = 'SymLink'

  def __init__(self, name, *, block=None, **kw):
    super().__init__(D_SYM_T, name, **kw)
    if block is not None:
      raise ValueError("block must be None, received: %s" % (block,))
    self.block = None
    if self.meta.pathref is None:
      raise ValueError("meta.pathref required")

  @property
  def pathref(self):
    ''' The symbolic link's path reference.
    '''
    return self.meta.pathref

  def transcribe_inner(self, T, fp):
    return super().transcribe_inner(T, fp, {})

class HardlinkDirent(_Dirent):
  ''' A hard link.
      Unlike the regular UNIX filesystem, in a vt filesystem a
      hard link is a wrapper for an ordinary Dirent; this wrapper references
      a persistent inode number and the source Dirent. Most attributes
      are proxied from the wrapped Dirent.
      In a normal Dirent .inum is a local attribute and not preserved;
      in a HardlinkDirent it is a proxy for the local .meta.inum.
  '''

  transcribe_prefix = 'HardLink'

  def __init__(self, name, meta, block=None):
    _Dirent.__init__(self, D_HARD_T, name, meta=meta)
    if block is not None:
      raise ValueError("block must be None, received: %s" % (block,))
    self.block = None
    if not hasattr(self.meta, 'inum'):
      raise ValueError("meta.inum required (no iref in meta=%r)" % (meta,))

  @property
  def inum(self):
    ''' On a HardlinkDirent the .inum accesses the meta['iref'] field.
        It is set at initialisation, so there is no .setter.
    '''
    return self.meta.inum

  @classmethod
  def to_inum(cls, inum, name):
    return cls(name, {'iref': str(inum)})

  def transcribe_inner(self, T, fp):
    return super().transcribe_inner(T, fp, {})

class FileDirent(_Dirent, MultiOpenMixin):
  ''' A _Dirent subclass referring to a file.
      If closed, ._block refers to the file content.
      If open, ._open_file refers to the content.
      NOTE: multiple opens return the _same_ backing file, with a
      shared read/write offset. File systems must share this, and
      maintain their own offsets in their file handle objects.
  '''

  transcribe_prefix = 'F'

  def __init__(self, name, block=None, **kw):
    _Dirent.__init__(self, D_FILE_T, name, **kw)
    MultiOpenMixin.__init__(self)
    if block is None:
      block = Block(data=b'')
    self._open_file = None
    self._block = block
    self._check()

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
    self._check()
    if self._block is not None:
      error("final close, but ._block is not None; replacing with self._open_file.close(), was: %s", self._block)
    Eopen = self._open_file
    Eopen.filename = self.name
    self._block = Eopen.close(enforce_final_close=True)
    self._open_file = None
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
    ##X("access FileDirent.block from:")
    ##stack_dump(indent=2)
    if self._open_file is None:
      return self._block
    return self._open_file.sync()

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
    if self._open_file is None:
      sz = len(self.block)
    else:
      sz = len(self._open_file)
    return sz

  def flush(self, scanner=None):
    ''' Flush the contents of the file.
    '''
    return self._open_file.flush(scanner)

  def truncate(self, length):
    ''' Truncate this FileDirent to the specified size.
    '''
    Esize = self.size
    if Esize != length:
      with self:
        return self._open_file.truncate(length)
    return None

  # TODO: move into distinctfile utilities class with rsync-like stuff etc
  def restore(self, path, makedirs=False, verbosefp=None):
    ''' Restore this _Dirent's file content to the name `path`.
    '''
    with Pfx("FileDirent.restore(%s)", path):
      if verbosefp is not None:
        verbosefp.write(path)
        verbosefp.write('\n')
      dirpath = os.path.dirname(path)
      if dirpath and not os.path.isdir(dirpath):
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

  def transcribe_inner(self, T, fp):
    ''' Transcribe the inner components of this FileDirent's transcription.
    '''
    return _Dirent.transcribe_inner(self, T, fp, {})

  def pushto(self, S2, Q=None):
    ''' Push the Block with the file contents to the Store `S2`.
        `S2`: the secondary Store to receive Blocks
        `Q`: optional preexisting Queue, which itself should have
          come from a .pushto targetting the Store `S2`.
        Semantics are as for cs.vt.block.Block.pushto.
    '''
    return self.block.pushto(S2, Q=Q)

class Dir(_Dirent):
  ''' A directory.

      .changed  Starts False, becomes true if this or any subdirectory
                gets changed or has a file opened; stays True from then on.
                This accepts an ongoing compute cost for .block to avoid
                setting the flag on every file.write etc.
  '''

  transcribe_prefix = 'D'

  def __init__(self, name, block=None, **kw):
    ''' Initialise this directory.
        `meta`: meta information
        `parent`: parent Dir
        `block`: pre-existing Block with initial Dir content
    '''
    super().__init__(D_DIR_T, name, **kw)
    if block is None:
      self._block = None
      self._entries = {}
    else:
      self._block = block
      self._entries = None
    self._unhandled_dirent_chunks = None
    self._changed = False
    self._lock = RLock()

  @prop
  def changed(self):
    ''' Whether this Dir has been changed.
    '''
    return self._changed

  @changed.setter
  @locked
  def changed(self, status):
    ''' Mark this dirent as changed; propagate to parent Dir if present.
    '''
    if not status:
      raise ValueError("cannot clear .changed")
    E = self
    while E is not None:
      E._changed = True
      E._notify_change()
      E = E.parent

  @locked
  def on_change(self, notifier):
    ''' Record `notifier` to fire when .changed is set.
    '''
    # This is so that unmonitored Dirs have no additional cost.
    notifiers = getattr(self, 'notify_change', None)
    if notifiers is None:
      self.notify_change = notifiers = set()
    notifiers.add(notifier)

  def _notify_change(self):
    ''' Call each recorded notifier with this Dir.
    '''
    notifiers = getattr(self, 'notify_change', None)
    if notifiers is None:
      return
    for notifier in notifiers:
      notifier(self)

  @prop
  def path(self):
    parts = [self.name]
    D = self
    while D.parent is not None:
      D = D.parent
      parts.append(D.name)
    return os.sep.join(reversed(parts))

  @property
  @locked
  def entries(self):
    ''' Property containing the live dictionary holding the Dir entries.
    '''
    emap = self._entries
    if emap is None:
      # compute the dictionary holding the live Dir entries
      emap = {}
      try:
        data = self._block.data
      except Exception as e:
        warning("Dir.entries: self._block.data: %s", e)
      else:
        for E in Dirents_from_data(data):
          E.parent = self
          emap[E.name] = E
      self._entries = emap
    return emap

  @property
  def size(self):
    ''' The length of a Dir is the number of entries it contains.
        This property is used mostly for stat calls.
    '''
    return len(self.entries)

  @property
  @locked
  def block(self):
    ''' Return the top Block referring to an encoding of this Dir.
        TODO: blockify the encoding? Probably desirable for big Dirs.
    '''
    if self._block is None or self.changed:
      X("Dir(%r): recompute block (_block=%s,changed=%s) ...", self.name, self._block, self.changed)
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
      # TODO: if len(data) >= 16384 blockify?
      B = self._block = Block(data=data)
      self._changed = False
    else:
      B = self._block
    return B

  def dirs(self):
    ''' Return a list of the names of subdirectories in this Dir.
    '''
    return [ name for name in self.keys() if self[name].isdir ]

  def files(self):
    ''' Return a list of the names of files in this Dir.
    '''
    return [ name for name in self.keys() if self[name].isfile ]

  @staticmethod
  def _validname(name):
    ''' Test if a name is valid: not empty and not containing the path separator.
    '''
    return len(name) > 0 and name.find(PATHSEP) < 0

  def get(self, name, dflt=None):
    ''' Fetch the Dirent named `name` or `dflt`.
    '''
    if name not in self:
      return dflt
    return self[name]

  def keys(self):
    ''' Return the Dirent names contained in this Dir. (Mapping method.)
    '''
    return self.entries.keys()

  def items(self):
    ''' Return the Dirents contained in this Dir. (Mapping method.)
    '''
    return self.entries.items()

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
    self.entries[name] = E
    self.touch()
    self.changed = True
    E.name = name
    Eparent = E.parent
    if Eparent is None:
      E.parent = self
    elif Eparent is not self:
      warning("%s: changing %r.parent to self, was %s", self, name, Eparent)
      E.parent = self

  def __delitem__(self, name):
    if not self._validname(name):
      raise KeyError("invalid name: %s" % (name,))
    if name == '.' or name == '..':
      raise KeyError("refusing to delete . or ..: name=%s" % (name,))
    E = self.entries[name]
    del self.entries[name]
    E.parent = None
    self.touch()
    self.changed = True

  def add(self, E):
    ''' Add a Dirent to this Dir.
        If the name is already taken, raise KeyError.
    '''
    name = E.name
    if name in self:
      raise KeyError("name already exists: %r" % (name,))
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
    for name in path.split(PATHSEP):
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

  def new_name(self, prefix, n=1):
    ''' Allocate a new unused name with the supplied `prefix`.
    '''
    while True:
      name2 = '.'.join(prefix, str(n))
      if name2 not in self:
        return name2
      n += 1

  def absorb(self, D2):
    ''' Absorb `D2` into this Dir.
        Note: this literally attaches nodes from `D2` into this
        Dir's tree where possible.
    '''
    for name in D2:
      E2 = D2[name]
      if name in self:
        # conflict
        # TODO: support S_IFWHT whiteout entries
        E1 = self[name]
        if E1.uuid == E2.uuid:
          # same file
          assert E1.type == E2.type
          if E2.meta.ctime > E1.meta.ctime:
            E1.meta.update(E2.meta.items())
          if E1.block != E2.block:
            if E2.mtime > E1.mtime:
              # TODO: E1.flush _after_ backend update? or before?
              E1.block = E2.block
            E1.meta.mtime = E2.mtime
        else:
          # distinct objects, resolve l
          if E1.isdir and E2.isdir:
            # merge subtrees
            E1.absorb(E2)
          elif E1.isfile and E2.isfile and E1.block == E2.block:
            # file with same content, fold
            # TODO: use Block.compare_content if different blocks
            if E2.meta.ctime > E1.meta.ctime:
              E1.meta.update(E2.meta.items())
          else:
            # add other object under a different name
            self[self.new_name(name)] = E2
      else:
        # new item
        # NB: we don't recurse into new Dirs, not needed
        self[name] = E2

  def transcribe_inner(self, T, fp):
    return _Dirent.transcribe_inner(self, T, fp, {})

  def pushto(self, S2, Q=None):
    ''' Push the Dir Blocks to the Store `S2`.
        `S2`: the secondary Store to receive Blocks
        `Q`: optional preexisting Queue, which itself should have
          come from a .pushto targetting the Store `S2`.
        This pushes the Dir's Block encoding to `S2` and then
        recursively pushes each Dirent's Block data to `S2`.
    '''
    if Q is None:
      # create a Queue and a worker Thread
      Q, T = defaults.S.pushto(S2)
    else:
      # use an existing Queue, no Thread to wait for
      T = None
    B = self.block
    # push the Dir block data
    B.pushto(S2, Q=Q)
    # and recurse into contents
    for E in Dirents_from_data(B.data):
      E.pushto(S2, Q=Q)
    if T:
      Q.close()
      T.join()

class DirFTP(Cmd):
  ''' Class for FTP-like access to a Dir.
  '''

  def __init__(self, D, prompt=None):
    Cmd.__init__(self)
    self._prompt = prompt
    self.root = D
    self.cwd = D

  @property
  def prompt(self):
    prompt = self._prompt
    pwd = PATHSEP + self.op_pwd()
    return ( pwd if prompt is None else ":".join( (prompt, pwd) ) ) + '> '

  def emptyline(self):
    pass

  def do_EOF(self, args):
    ''' Quit on end of input.
    '''
    return True

  @docmd
  def do_quit(self, args):
    ''' Usage: quit
    '''
    return True

  @docmd
  def do_cd(self, args):
    ''' Usage: cd pathname
        Change working directory.
    '''
    argv = shlex.split(args)
    if len(argv) != 1:
      raise GetoptError("exactly one argument expected, received: %r" % (argv,))
    self.op_cd(argv[0])
    print(self.op_pwd())

  def op_cd(self, path):
    ''' Change working directory.
    '''
    if path.startswith(PATHSEP):
      D = self.root
    else:
      D = self.cwd
    for base in path.split(PATHSEP):
      if base == '' or base == '.':
        pass
      elif base == '..':
        if D is not self.root:
          D = D.parent
      else:
        D = D.chdir1(base)
    self.cwd = D

  @docmd
  def do_inspect(self, args):
    ''' Usage: inspect name
        Print VT level details about name.
    '''
    argv = shlex.split(args)
    if len(argv) != 1:
      raise GetoptError("invalid arguments: %r" % (argv,))
    name, = argv
    E, P, tail = resolve(self.cwd, name)
    if tail:
      raise OSError(errno.ENOENT)
    print("%s: %s" % (name, E))
    M = E.meta
    print(M.textencode())
    print("size=%d" % (len(E.block),))

  @docmd
  def do_pwd(self, args):
    ''' Usage: pwd
        Print the current working directory path.
    '''
    argv = shlex.split(args)
    if argv:
      raise GetoptError("extra arguments: %r" % (args,))
    print(self.op_pwd())

  def op_pwd(self):
    ''' Return the path to the current working directory.
    '''
    E = self.cwd
    names = []
    seen = set()
    while E is not self.root:
      seen.add(E)
      P = E.parent
      if P is None:
        raise ValueError("no parent: names=%r, E=%s" % (names, E))
      if P in seen:
        raise ValueError("loop detected: names=%r, E=%s" % (names, E))
      name = E.name
      if P[name] is not E:
        name = None
        for Pname, PE in sorted(P.entries.items()):
          if PE is E:
            name = Pname
            break
        if name is None:
          raise ValueError("detached: E not present in P: E=%s, P=%s" % (E, P))
      names.append(name)
      E = P
    return PATHSEP.join(reversed(names))

  @docmd
  def do_ls(self, args):
    ''' Usage: ls [paths...]
    '''
    argv = shlex.split(args)
    if not argv:
      argv = sorted(self.cwd.entries.keys())
    for name in argv:
      with Pfx(name):
        E, P, tail = resolve(self.cwd, name)
        if tail:
          error("not found: unresolved path elements: %r", tail)
        else:
          M = E.meta
          u, g, perms = M.unix_perms
          typemode = M.unix_typemode
          typechar = (
              '-' if typemode == stat.S_IFREG
              else 'd' if typemode == stat.S_IFDIR
              else 's' if typemode == stat.S_IFLNK
              else '?'
          )
          print("%s%s%s%s %s" % (
              typechar,
              rwx((typemode>>6)&7),
              rwx((typemode>>3)&7),
              rwx((typemode)&7),
              name
          ))

  def op_ls(self):
    ''' Return a dict mapping current directories names to Dirents.
    '''
    return dict(self.cwd.entries)

  @docmd
  def do_mkdir(self, args):
    argv = shlex.split(args)
    if not argv:
      raise GetoptError("missing arguments")
    for arg in argv:
      with Pfx(arg):
        E, _, tail = resolve(self.cwd, arg)
        if not tail:
          error("path exists")
        elif len(tail) > 1:
          error("missing superdirectory")
        elif not E.isdir:
          error("superpath is not a directory")
        else:
          subname = tail[0]
          if subname in E:
            error("%r exists", subname)
          else:
            E.mkdir(subname)
        self.cwd

if __name__ == '__main__':
  from .dir_tests import selftest
  selftest(sys.argv)
