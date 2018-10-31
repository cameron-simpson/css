#!/usr/bin/env python3
#

''' Implementation of directories (Dir) and their entries (FileDirent, etc).
'''

from cmd import Cmd
from collections import OrderedDict
from enum import IntEnum, IntFlag
import errno
from functools import partial
from getopt import GetoptError
import grp
import os
import os.path
import pwd
import shlex
import stat
import sys
from threading import RLock
import time
from uuid import UUID, uuid4
from cs.binary import PacketField, BSUInt, BSString, BSData
from cs.buffer import CornuCopyBuffer
from cs.cmdutils import docmd
from cs.logutils import debug, error, warning, info, exception
from cs.pfx import Pfx
from cs.lex import texthexify
from cs.py.func import prop
from cs.py.stack import stack_dump
from cs.queues import MultiOpenMixin
from cs.threads import locked, locked_property
from cs.x import X
from . import totext, PATHSEP, defaults
from .block import Block, _Block, BlockRecord
from .file import RWBlockFile
from .meta import Meta, rwx, DEFAULT_DIR_ACL, DEFAULT_FILE_ACL
from .paths import path_split, resolve
from .transcribe import Transcriber, parse as parse_transcription, \
                        register as register_transcriber

uid_nobody = -1
gid_nogroup = -1

# Directories (Dir, a subclass of dict) and directory entries (_Dirent).

class DirentType(IntEnum):
  ''' Dirent type codes.
  '''
  INVALID = -1
  FILE = 0
  DIR = 1
  SYMBOLIC = 2
  INDIRECT = 3

class DirentFlags(IntFlag):
  ''' Flag values for the Dirent binary encoding.
  '''
  HASMETA = 0x01        # has metadata
  HASNAME = 0x02        # has a name
  NOBLOCK = 0x04        # has no Block reference
  HASUUID = 0x08        # has a UUID
  HASPREVDIRENT = 0x10  # has reference to serialised previous Dirent
  EXTENDED = 0x20       # extended BSData field

def Dirents_from_chunks(chunks):
  ''' Decode Dirents from `chunks`, yield each in turn.
  '''
  bfr = CornuCopyBuffer(chunks)
  while not bfr.at_eof():
    yield DirentRecord.value_from_buffer(bfr)

class DirentRecord(PacketField):
  ''' PacketField subclass to parsing and transcribing Dirents in binary form.

      The serialisation format is:

            BSUint(type)
            BSUint(flags)
            [BSString(name)]
            [BSString(str(meta))]
            [uuid:16]
            blockref
            [blockref(pref_dirent)]
            [BSData(extended_data)]

      Note that all additional future implementation detail needs
      to go in the metadata or the optional extended_data.
  '''

  @classmethod
  def value_from_buffer(cls, bfr):
    ''' Unserialise a serialised Dirent.
    '''
    type_ = BSUInt.value_from_buffer(bfr)
    flags = DirentFlags(BSUInt.value_from_buffer(bfr))
    if flags & DirentFlags.HASNAME:
      flags ^= DirentFlags.HASNAME
      name = BSString.value_from_buffer(bfr)
    else:
      name = ""
    if flags & DirentFlags.HASMETA:
      flags ^= DirentFlags.HASMETA
      metatext = BSString.value_from_buffer(bfr)
    else:
      metatext = None
    uu = None
    if flags & DirentFlags.HASUUID:
      flags ^= DirentFlags.HASUUID
      uu = UUID(bytes=bfr.take(16))
    if flags & DirentFlags.NOBLOCK:
      flags ^= DirentFlags.NOBLOCK
      block = None
    else:
      block = BlockRecord.value_from_buffer(bfr)
    if flags & DirentFlags.HASPREVDIRENT:
      flags ^= DirentFlags.HASPREVDIRENT
      prev_dirent_blockref = BlockRecord.value_from_buffer(bfr)
    else:
      prev_dirent_blockref = None
    if flags & DirentFlags.EXTENDED:
      flags ^= DirentFlags.EXTENDED
      extended_data = BSData.value_from_buffer(bfr)
    else:
      extended_data = None
    if flags:
      warning(
          "%s.value_from_buffer: unexpected extra flags: 0x%02x",
          cls.__name__, flags)
    E = _Dirent.from_components(type_, name, meta=metatext, uuid=uu, block=block)
    E._prev_dirent_blockref = prev_dirent_blockref
    E.ingest_extended_data(extended_data)
    return E

  @staticmethod
  def transcribe_value(E):
    ''' Serialise to binary format.
    '''
    flags = 0
    type_ = E.type
    if E.name:
      flags |= DirentFlags.HASNAME
    meta = None if E.isindirect else E.meta
    if meta:
      flags |= DirentFlags.HASMETA
    if E.uuid:
      flags |= DirentFlags.HASUUID
    block = None if type_ is DirentType.INDIRECT else E.block
    if block is None:
      flags |= DirentFlags.NOBLOCK
    if E._prev_dirent_blockref is not None:
      flags |= DirentFlags.HASPREVDIRENT
    extended_data = E.get_extended_data()
    if extended_data:
      flags |= DirentFlags.EXTENDED
    yield BSUInt.transcribe_value(type_)
    yield BSUInt.transcribe_value(flags)
    if flags & DirentFlags.HASNAME:
      yield BSString.transcribe_value(E.name)
    if flags & DirentFlags.HASMETA:
      yield BSString.transcribe_value(meta.textencode())
    if flags & DirentFlags.HASUUID:
      bs = E.uuid.bytes
      if len(bs) != 16:
        raise RuntimeError("len(E.uuid.bytes) != 16: %r" % (bs,))
      yield bs
    if not flags & DirentFlags.NOBLOCK:
      yield BlockRecord.transcribe_value(block)
    if flags & DirentFlags.HASPREVDIRENT:
      assert isinstance(E._prev_dirent_blockref, _Block)
      yield BlockRecord.transcribe_value(E._prev_dirent_blockref)
    if flags & DirentFlags.EXTENDED:
      yield extended_data

class _Dirent(Transcriber):
  ''' Incomplete base class for Dirent objects.
  '''

  transcribe_prefix = 'DIRENT'

  def __init__(
      self,
      type_, name,
      *,
      meta=None,
      uuid=None,
      parent=None,
      prevblock=None,
      block=None,
      **kw,
  ):
    ''' Initialise a _Dirent.

        Parameters:
        * `type_`: the DirentType enum
        * `name`: the Dirent's name
        * `meta`: optional metadata
        * `uuid`: optional identifying UUID;
          *note*: for IndirectBlocks this is a reference to another Dirent's
          UUID.
        * `parent`: optional parent Dirent
        * `prevblock`: optional Block whose contents are the binary
          transcription of this Dirent's previous state - another
          Dirent
    '''
    with Pfx("_Dirent(type_=%s,name=%r,...)", type_, name):
      if not isinstance(type_, int):
        raise TypeError("type_ is not an int: <%s>%r" % (type(type_), type_))
      if name is not None and not isinstance(name, str):
        raise TypeError("name is neither None nor str: <%s>%r" % (type(name), name))
      if kw:
        error("unsupported keyword arguments: %r", kw)
      if block is not None:
        raise ValueError("block is not None: %r", block)
      self.type = type_
      self.name = name
      self.uuid = uuid
      assert prevblock is None or isinstance(prevblock, _Block), \
          "not _Block: prevblock=%r" % (prevblock,)
      self._prev_dirent_blockref = prevblock
      if not isinstance(meta, Meta):
        M = Meta({'a': DEFAULT_DIR_ACL if self.isdir else DEFAULT_FILE_ACL})
        if meta is None:
          pass
        elif isinstance(meta, str):
          M.update_from_text(meta)
        else:
          raise ValueError("unsupported meta value: %r" % (meta,))
        if 'm' not in M:
          M['m'] = time.time()
        meta = M
      if type_ != DirentType.INDIRECT:
        self.meta = meta
      self.parent = parent

  def __repr__(self):
    return "%s:%s:%s(%s:%s,%s)" % (
        self.__class__.__name__,
        id(self),
        self.type,
        self.name,
        self.uuid,
        self.meta
    )

  @classmethod
  def from_str(cls, s, offset=0):
    ''' Parse a Dirent transcription from the str `s`.
    '''
    E, offset2 = parse_transcription(s, offset)
    if not isinstance(E, cls):
      raise ValueError(
          "expected instance of %s (got %s) at offset %d of %r"
          % (cls, type(E), offset, s))
    return E, offset2

  @staticmethod
  def from_components(type_, name, **kw):
    ''' Factory returning a _Dirent instance.
    '''
    if type_ == DirentType.DIR:
      cls = Dir
    elif type_ == DirentType.FILE:
      cls = FileDirent
    elif type_ == DirentType.SYMBOLIC:
      cls = SymlinkDirent
    elif type_ == DirentType.INDIRECT:
      cls = IndirectDirent
    else:
      warning("from_components: UNSUPPORTED TYPE %r, using _Dirent", type_)
      cls = partial(_Dirent, type_)
    return cls(name, **kw)

  @staticmethod
  def from_bytes(data, offset=0):
    ''' Factory to extract a Dirent from binary data at `offset` (default 0).
        Returns the Dirent and the new offset.
    '''
    return DirentRecord.value_from_bytes(data, offset=offset)

  def ingest_extended_data(self, extended_data):
    ''' The basic _Dirent subclasses do not use extended data.
    '''
    if extended_data:
      raise ValueError(
          "expected extended_data to be None or empty, got: %r",
          extended_data)

  def get_extended_data(self):
    ''' The basic _Dirent subclasses do not use extended data.
    '''
    return None

  def __bytes__(self):
    ''' Serialise this Dirent to bytes.
    '''
    return bytes(DirentRecord(self))

  encode = __bytes__

  def __hash__(self):
    ''' Allows collecting _Dirents in a set.
    '''
    return id(self)

  def transcribe_inner(self, T, fp, attrs={}):
    ''' Transcribe the inner components of the Dirent as text.
    '''
    if self.name and self.name != '.':
      T.transcribe(self.name, fp=fp)
      fp.write(':')
    if type(self) is _Dirent:
      attrs['type'] = self.type
    if self.uuid:
      attrs['uuid'] = self.uuid
    if self.type != DirentType.INDIRECT:
      if self.meta:
        attrs['meta'] = self.meta
      block = getattr(self, 'block', None)
      if block:
        attrs['block'] = self.block
    prev_blockref = self._prev_dirent_blockref
    if prev_blockref is not None:
      attrs['prevblock'] = prev_blockref
    T.transcribe_mapping(attrs, fp)

  @classmethod
  def parse_inner(cls, T, s, offset, stopchar, prefix):
    ''' Parse [name:]attrs from `s` at offset `offset`.
        Return _Dirent instance and new offset.
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
        'F': DirentType.FILE,
        'D': DirentType.DIR,
        'SymLink': DirentType.SYMBOLIC,
        'Indirect': DirentType.INDIRECT,
    }.get(prefix)
    return cls.from_components(type_, name, **attrs), offset

  def get_uuid(self):
    ''' Return this Dirent's UUID, creating it if necessary.
    '''
    u = self.uuid
    if u is None:
      u = self.uuid = uuid4()
      parent = self.parent
      if parent:
        parent.changed = True
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
    if E is None:
      if self._prev_dirent_blockref is not None:
        self.changed = True
    elif E == self:
      warning(
          "%r.prev_dirent=%s: ignore setting previous to our own state",
          self, E)
    else:
      Ebs = E.encode()
      self._prev_dirent_blockref = Block(data=Ebs)
      self.changed = True

  def snapshot(self):
    ''' Update the Dirent's previous block state if missing or changed.
    '''
    E = self.prev_dirent
    if E is None or E != self:
      self.prev_dirent = self

  def reconcile(self, other, force=False):
    ''' Reconcile 2 Dirents.

        It is expected that they are meant to be different revisions
        of the "same" object.
        If they both have UUIDs, they should match.
        They should be of the same type (both files, etc).
    '''
    with Pfx("%s.reconcile(%s)", self, other):
      if self is other:
        warning("tried to reconcile a _Dirent with itself")
        return
      uu1 = self.uuid
      uu2 = other.uuid
      if uu1 and uu2 and uu1 != uu2:
        warning("different UUIDs: %s vs %s", uu1, uu2)
        if not force:
          raise ValueError("not reconciling Dirents with different UUIDs")
      warning("NOT IMPLEMENTED YET")

  @property
  def isfile(self):
    ''' Is this a file _Dirent?
    '''
    return self.type == DirentType.FILE

  @property
  def isdir(self):
    ''' Is this a directory _Dirent?
    '''
    return self.type == DirentType.DIR

  @property
  def issym(self):
    ''' Is this a symbolic link _Dirent?
    '''
    return self.type == DirentType.SYMBOLIC

  @property
  def isindirect(self):
    ''' Is this an indirect _Dirent?
    '''
    return self.type == DirentType.INDIRECT

  def textencode(self):
    ''' Serialise the dirent as text.
    '''
    return totext(self.encode())

  @property
  def size(self):
    ''' Return this Dirent's length: its Block's span length.

        Note that Dirents with a None Block return None.
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

  def stat(self, fs=None):
    ''' Return this Dirent's POSIX stat structure.
    '''
    if fs is None:
      fs = defaults.fs
    M = self.meta
    I = fs.E2inode(self)
    perm_bits = M.unix_perm_bits
    if perm_bits is None:
      if self.isdir:
        perm_bits = 0o700
      else:
        perm_bits = 0o600
    st_mode = self.unix_typemode | perm_bits
    st_ino = I.inum
    # TODO: dev from FileSystem
    st_dev = fs.device_id
    if self.isdir:
      # TODO: should nlink for Dirs count its subdirs?
      st_nlink = 1
    else:
      st_nlink = I.refcount
    st_uid = M.uid
    st_gid = M.gid
    if self.issym:
      pathref = self.pathref
      if pathref is None:
        warning("no pathref for %s", self)
        st_size = 0
      else:
        st_size = len(pathref)
    else:
      st_size = self.size
    st_atime = 0
    st_mtime = M.mtime
    st_ctime = 0
    return os.stat_result( (
        st_mode,
        st_ino,
        st_dev,
        st_nlink,
        st_uid, st_gid,
        st_size,
        st_atime, st_mtime, st_ctime,
    ) )

  @property
  def unix_typemode(self):
    ''' The portion of the mode bits defining the inode type.
    '''
    E = self
    if E.isindirect:
      E = E.ref
      if E.isindirect:
        raise ValueError(
            "indirect %s refers to another indirect: %s"
            % (self, E))
    if E.isdir:
      typemode = stat.S_IFDIR
    elif E.isfile:
      typemode = stat.S_IFREG
    elif E.issym:
      typemode = stat.S_IFLNK
    else:
      warning(
          "%s.unix_typemode: unrecognised type %d, pretending S_IFREG"
          % (type(self), self.type))
      typemode = stat.S_IFREG
    return typemode

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
    'Indirect',
    'D',
    'F',
))

class InvalidDirent(_Dirent):
  ''' Encapsulation for an invalid Dirent data chunk.
  '''

  transcribe_prefix = 'INVALIDDirent'

  def __init__(self, name, *, chunk=None, **kw):
    ''' An invalid Dirent.
        Record the original data chunk for regurgitation later.
    '''
    _Dirent.__init__(
        self,
        DirentType.INVALID,
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

  def __init__(self, name, *, block=None, target=None, **kw):
    if block is not None:
      raise ValueError("block must be None, received: %s" % (block,))
    super().__init__(DirentType.SYMBOLIC, name, **kw)
    self.block = None
    if target is None:
      if self.meta.pathref is None:
        raise ValueError("missing target")
    else:
      self.meta.pathref = target

  @property
  def pathref(self):
    ''' The symbolic link's path reference.
    '''
    return self.meta.pathref

  def transcribe_inner(self, T, fp):
    ''' Transcribe the inner components for a SymlinkDirent.
    '''
    return super().transcribe_inner(T, fp, {})

class IndirectDirent(_Dirent):
  ''' An indirect Dirent, referring to another Dirent by UUID.

      This is how a feature like a hard link is implented in a vt filesystem.

      *Note*: unlike other Dirents, IndirectDirents are considered
      emphemeral, specificly in that their uuid attribute is a
      reference to another persistent Direct. Obtaining the target
      dirent requires dereferencing through a FileSystem.
  '''

  transcribe_prefix = 'Indirect'

  def __init__(self, name, uuid, meta=None, block=None):
    if block is not None:
      raise ValueError("IndirectDirent block should be None, got: %r" % (block,))
    if meta is not None:
      raise ValueError("IndirectDirent meta should be None, got: %r" % (meta,))
    _Dirent.__init__(self, DirentType.INDIRECT, name, uuid=uuid)

  def deref(self, fs=None):
    ''' Dereference this IndirectDirent's UUID via a FileSystem.
    '''
    if fs is None:
      fs = defaults.fs
      if not fs:
        exception("NO CURRENT FILESYSTEM")
        stack_dump()
        raise ValueError("no current FileSystem")
    try:
      I = fs[self.uuid]
    except KeyError as e:
      error("%s: no inode for UUID %s: %s", fs, self.uuid, e)
      stack_dump()
      raise
    return I.E

  @prop
  def ref(self):
    ''' The referenced Dirent via the default FileSystem.
    '''
    try:
      return self.deref()
    except KeyError as e:
      raise AttributeError('ref') from e

  @property
  def meta(self):
    ''' The metadata of the referenced Dirent.
    '''
    return self.ref.meta

  @meta.setter
  def meta(self, new_meta):
    ''' Setting the metadata acts on the referent.
    '''
    self.ref.meta = new_meta

  @prop
  def block(self):
    ''' The content block for the referenced Dirent.
    '''
    return self.ref.block

  def transcribe_inner(self, T, fp):
    ''' Transcribe the inner components of an IndirectDirent.
    '''
    return super().transcribe_inner(T, fp, {})

class FileDirent(_Dirent, MultiOpenMixin):
  ''' A _Dirent subclass referring to a file.

      If closed, ._block refers to the file content.
      If open, .open_file refers to the content.

      NOTE: multiple opens initialise the _same_ backing file, with a shared
      read/write offset. File systems must share this, and maintain their own
      offsets in their file handle objects.
  '''

  transcribe_prefix = 'F'

  def __init__(self, name, block=None, lock=None, **kw):
    _Dirent.__init__(self, DirentType.FILE, name, **kw)
    MultiOpenMixin.__init__(self, lock=lock)
    if block is None:
      block = Block(data=b'')
    self.open_file = None
    self._block = block
    self._check()

  @locked
  def startup(self):
    ''' Set up .open_file on first open.
    '''
    self._check()
    if self.open_file is not None:
      raise RuntimeError("first open, but .open_file is not None: %r" % (self.open_file,))
    if self._block is None:
      raise RuntimeError("first open, but ._block is None")
    self.open_file = RWBlockFile(self._block)
    self._block = None
    self._check()

  @locked
  def shutdown(self):
    ''' On final close, close .open_file and save result as ._block.
    '''
    self._check()
    if self._block is not None:
      error(
          "final close, but ._block is not None;"
          " replacing with self.open_file.close(), was: %s",
          self._block)
    f = self.open_file
    f.filename = self.name
    self._block = f.close(enforce_final_close=True)
    self.open_file = None
    self._check()

  def _check(self):
    ''' Internal consistency check.
    '''
    # TODO: check ._block and .open_file against MultiOpenMixin open count
    if self._block is None:
      if self.open_file is None:
        raise ValueError("both ._block and .open_file are None")
    ## both are allowed to be set
    ##elif self.open_file is not None:
    ##  raise ValueError("._block is %s and .open_file is %r" % (self._block, self.open_file))

  @property
  @locked
  def block(self):
    ''' Obtain the top level Block.
        If open, sync the file to update ._block.
    '''
    self._check()
    ##X("access FileDirent.block from:")
    ##stack_dump(indent=2)
    if self.open_file is None:
      return self._block
    return self.open_file.sync()

  @block.setter
  @locked
  def block(self, B):
    ''' Update the Block for this FileDirent.
        The Dirent is expected to be closed.
    '''
    if self.open_file is not None:
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
    if self.open_file is None:
      sz = len(self.block)
    else:
      sz = len(self.open_file)
    return sz

  def flush(self, scanner=None):
    ''' Flush the contents of the file.
        Presumes the Dirent is open.
    '''
    return self.open_file.flush(scanner)

  def truncate(self, length):
    ''' Truncate this FileDirent to the specified size.
    '''
    Esize = self.size
    if Esize != length:
      with self:
        return self.open_file.truncate(length)
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

  def pushto(self, S2, Q=None, runstate=None):
    ''' Push the Block with the file contents to the Store `S2`.

        Parameters:
        * `S2`: the secondary Store to receive Blocks
        * `Q`: optional preexisting Queue, which itself should have
          come from a .pushto targetting the Store `S2`.
        * `runstate`: optional RunState used to cancel operation

        Semantics are as for cs.vt.block.Block.pushto.
    '''
    return self.block.pushto(S2, Q=Q, runstate=runstate)

class Dir(_Dirent):
  ''' A directory.

      Special attributes:
      * `changed`:
        Starts False, becomes true if this or any subdirectory gets changed
        or has a file opened; stays True from then on.
        This accepts an ongoing compute cost for .block to avoid
        setting the flag on every file.write etc.
  '''

  transcribe_prefix = 'D'

  def __init__(self, name, block=None, **kw):
    ''' Initialise this directory.

        Parameters:
        * `meta`: meta information
        * `parent`: parent Dir
        * `block`: pre-existing Block with initial Dir content
    '''
    super().__init__(DirentType.DIR, name, **kw)
    if block is None:
      self._block = None
      self._entries = {}
    else:
      self._block = block
      self._entries = None
    self._unhandled_dirent_chunks = None
    self._changed = False
    self._change_notifiers = None
    self._lock = RLock()

  @prop
  def changed(self):
    ''' Whether this Dir has been changed.
    '''
    return self._changed

  @changed.setter
  @locked
  def changed(self, status):
    ''' Mark this dirent as changed or not changed;
        propagate truth to parent Dir if present.
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
    ''' Record the callable `notifier` to fire when .changed is set.
    '''
    notifiers = self._change_notifiers
    if notifiers is None:
      self._change_notifiers = notifiers = set()
    notifiers.add(notifier)

  def _notify_change(self):
    ''' Call each recorded notifier with this Dir.
    '''
    notifiers = self._change_notifiers
    if notifiers:
      for notifier in notifiers:
        notifier(self)

  @property
  @locked
  def entries(self):
    ''' Property containing the live dictionary holding the Dir entries.
    '''
    emap = self._entries
    if emap is None:
      # compute the dictionary holding the live Dir entries
      emap = {}
      for E in Dirents_from_chunks(self._block.datafrom()):
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
      X("Dir(%d:%r): recompute block: current _block=%s, changed=%s ...",
        id(self), self.name, self._block, self.changed)
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
    try:
      E = self[name]
    except KeyError:
      return dflt
    return E

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
    return iter(self.keys())

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

  def pop(self, name):
    ''' Delete `name` and return the Dirent.
    '''
    if not self._validname(name):
      raise KeyError("invalid name: %s" % (name,))
    if name == '.' or name == '..':
      raise KeyError("refusing to delete . or ..: name=%s" % (name,))
    E = self.entries[name]
    del self.entries[name]
    E.parent = None
    self.touch()
    self.changed = True
    return E

  __delitem__ = pop

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
      if name:
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

  def update(self, D2, path=None):
    ''' Update this Dir with changes from `D2` which is presumed to be new.
        Note: this literally attaches nodes from `D2` into this
        Dir's tree where possible.
    '''
    if path is None:
      path = self.pathto()
    with Pfx("update(%r)", path):
      for name in D2:
        with Pfx(name):
          E2 = D2[name]
          if name in self:
            # conflict
            # TODO: support S_IFWHT whiteout entries
            E1 = self[name]
            if E1.uuid == E2.uuid:
              # same file
              assert E1.type == E2.type
              if E2.meta.ctime > E1.meta.ctime:
                info("update meta")
                E1.meta.update(E2.meta.items())
              if E1.block != E2.block:
                if E2.mtime > E1.mtime:
                  # TODO: E1.flush _after_ backend update? or before?
                  info("update block => %s", E2.block)
                  E1.block = E2.block
                  E1.meta.mtime = E2.mtime
            else:
              # distinct objects
              if E1.isdir and E2.isdir:
                # merge subtrees
                E1.update(E2)
              elif E1.isfile and E2.isfile and E1.block == E2.block:
                # file with same content, fold
                # TODO: use Block.compare_content if different blocks
                if E2.meta.ctime > E1.meta.ctime:
                  info("update meta")
                  E1.meta.update(E2.meta.items())
              else:
                # different content
                # add other object under a different name
                new_name = self.new_name(name)
                info("add new entry as %r: %s", new_name, E2)
                self[new_name] = E2
          else:
            # new item
            # NB: we don't recurse into new Dirs, not needed
            info("add new entry: %s", E2)
            self[name] = E2

  def update_notification(self, newE, when, source):
    ''' Receptor for Archive.update() notifications.
    '''
    with Pfx("update_notification(%s,when=%s,source=%s)", newE, when, source):
      if newE is not self:
        self.update(newE)

  def transcribe_inner(self, T, fp):
    return _Dirent.transcribe_inner(self, T, fp, {})

  def pushto(self, S2, Q=None, runstate=None):
    ''' Push the Dir Blocks to the Store `S2`.

        Parameters:
        * `S2`: the secondary Store to receive Blocks
        * `Q`: optional preexisting Queue, which itself should have
          come from a .pushto targetting the Store `S2`.
        * `runstate`: optional RunState used to cancel operation

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
    B.pushto(S2, Q=Q, runstate=runstate)
    # and recurse into contents
    for E in Dirents_from_chunks(B.datafrom()):
      if runstate and runstate.cancelled:
        warning("pushto(%s) cancelled", self)
        break
      E.pushto(S2, Q=Q, runstate=runstate)
    if T:
      Q.close()
      T.join()

class DirFTP(Cmd):
  ''' Class for FTP-like interactive access to a Dir.

      TODO: move into some utility module.
  '''

  def __init__(self, D, prompt=None):
    Cmd.__init__(self)
    self._prompt = prompt
    self.root = D
    self.cwd = D

  @property
  def prompt(self):
    ''' The interactive prompt.
    '''
    prompt = self._prompt
    wd_path = PATHSEP + self.op_pwd()
    return ( wd_path if prompt is None else ":".join( (prompt, wd_path) ) ) + '> '

  def emptyline(self):
    ''' Empty line handler.
    '''
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
    ''' Make a directory.
    '''
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

if __name__ == '__main__':
  from .dir_tests import selftest
  selftest(sys.argv)
