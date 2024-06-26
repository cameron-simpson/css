#!/usr/bin/env python3

''' URI scheme for VT URIs.
'''

from dataclasses import dataclass
import os
from os.path import basename, isdir as isdirpath, isfile as isfilepath
import re
from stat import S_ISDIR, S_ISREG
from typing import Optional, Union

from cs.cache import convof
from cs.deco import Promotable
from cs.fileutils import atomic_filename
from cs.fstags import FSTags, uses_fstags
from cs.logutils import warning
from cs.pfx import Pfx, pfx_call, pfx_method
from cs.progress import progressbar
from cs.resources import RunState, uses_runstate
from cs.units import BINARY_BYTES_SCALE

from . import Store, uses_Store
from .block import HashCodeBlock, IndirectBlock
from .blockify import block_for
from .dir import _Dirent, Dir, FileDirent
from .hash import HashCode

@dataclass
class VTURI(Promotable):
  ''' A representation of a `HashCodeBlock` as a URI.

      Most URIs commence with `x-vt://` with no network component.

      This may then be followed by a `/f` is the Block encodes a `FileDirent`
      or `/d` if the Block encodes a `Dir`, otherwise the Block
      should be the direct file content.

      The Block itself then follows as `/[ih]/hashname:hashhex`
      with `i` for an `IndirectBlock` or `h` for a `HashCodeBlock`
      then the hash function name eg `sha1` and the hash digest in hexadecimal.

      This may be followed with `/filename` to provide a default filename for the content.

      Then a final `/` follows if the URI is for a directory,
      in which case the Block will encode a `Dir`.
  '''

  DEFAULT_SCHEME = 'x-vt'
  _URI_RE = re.compile(
      r'(?P<scheme>[a-z][-a-z0-9]*):'  # scheme, normally x-vt
      r'//(?P<network>[^/]*)'  # network part, empty by default
      r'(/(?P<dirent_type>[fd]))?'  # /f for FileDirent, /d for Dir
      r'/(?P<indirect>[hi])'  # (i)ndirect blockref or direct (h)
      # hashname:hashcodehex
      r'/(?P<hashname>[a-z][a-z0-9]*):(?P<hashtext>([0-9a-f][0-9a-f])+)'
      r'(/(?P<filename>[^/?#]+))?'  # informational path basename
      r'(?P<dir_indicator>/?)'  # "/" if a directory Dirent, "" otherwise
      r'$',
      re.I,
  )

  # TODO: validate the various field values against each other?

  hashcode: HashCode  # content hashcode
  indirect: bool  # hashcode is indirect -> HashCodeBlock vs IndirectBlock
  isdirent: bool = False  # the content is a Dirent
  isdir: bool = False  # require isdirent, says that the dirent is a Dir
  span: Optional[int] = None
  filename: Optional[str] = None
  scheme: str = DEFAULT_SCHEME
  network: Optional[str] = None

  def __str__(self):
    return ''.join(
        (
            f'{self.scheme}:',
            f'//{self.network or ""}',
            (('/d' if self.isdir else '/f') if self.isdirent else ''),
            f'/{"i" if self.indirect else "h"}',
            f'/{self.hashcode.hashname}:{self.hashcode.hex()}',
            (f'/{self.filename}' if self.filename else ''),
            ('/' if self.isdir else ''),
        )
    )

  @property
  def block(self) -> Union[HashCodeBlock, IndirectBlock]:
    ''' A `Block` for this URI.
    '''
    return (
        IndirectBlock(HashCodeBlock(self.hashcode), span=self.span)
        if self.indirect else HashCodeBlock(self.hashcode, span=self.span)
    )

  @property
  @pfx_method
  def content_block(self):
    ''' Return a `Block` holding the file content for this URI.
    '''
    if self.isdir:
      raise AttributeError(
          f'{self.__class__.__name__}.content_block: not available for directories'
      )
    if not self.isdirent:
      # the block is the content
      return self.block
    # the block encodes a dirent - decode and return the dirent block
    E = self.as_Dirent()
    return E.block

  def as_Dirent(self, filename=None):
    ''' Return a Dirent for this VTURI.
    '''
    if filename is None:
      filename = self.filename
    if self.isdirent:
      bs = bytes(self.block)
      E, offset = _Dirent.from_bytes(bs)
      if offset < len(bs):
        raise ValueError(
            f'unparsed data after Dirent {E}: {bs[offset:offset+16]}...'
        )
    else:
      E = FileDirent(filename, self.block)
    return E

  @classmethod
  def from_uri(cls, uri_s):
    ''' Make a `VTURI` from a URI string.
    '''
    m = cls._URI_RE.match(uri_s)
    if m is None:
      raise ValueError(f'unrecognised URI: {uri_s!r}')
    return cls(
        scheme=m['scheme'],
        network=m['network'] or None,
        isdir=m['dirent_type'] == 'd',
        isdirent=bool(m['dirent_type']),
        indirect=m['indirect'] == 'i',
        hashcode=HashCode.from_named_hashbytes_hex(
            m['hashname'], m['hashtext']
        ),
        filename=m['filename'],
    )

  @classmethod
  @pfx_method
  def from_fspath(
      cls,
      fspath: str,
      *,
      as_dirent=None,
      filename=None,
      follow_symlinks=False,
      force=False,
  ):
    ''' Return a URI for the filesystem path `fspath`.
    '''
    st = pfx_call((os.stat if follow_symlinks else os.lstat), fspath)
    if S_ISDIR(st.st_mode):
      if as_dirent is not None and not as_dirent:
        raise ValueError(
            f'a directory path requires as_dirent=True, got {as_dirent!r}'
        )
      return cls.from_dirpath(
          fspath,
          filename=filename,
          follow_symlinks=follow_symlinks,
          force=force
      )
    if S_ISREG(st.st_mode):
      return cls.from_filepath(
          fspath, as_dirent=as_dirent, filename=filename, force=force
      )
    raise ValueError('neither file nor directory')

  @classmethod
  @pfx_method
  @uses_Store
  def from_filepath(
      cls,
      fspath: str,
      *,
      S: Store,
      as_dirent=False,
      filename=None,
      force=False,
  ):
    ''' Store the file `fspath` unless we have already cached its content based URI.
        Return the `VTURI`.

        This call believes it is storing a file and so it _will_ follow a symlink.
        Use `from_fspath` for a `follow_symlinks` parameter (default `False`).
    '''
    if not isfilepath(fspath):
      raise ValueError(f'not a regular file: {fspath!r}')
    if filename is None:
      filename = basename(fspath)
    uri = S.block_for(fspath).uri
    uri.filename = filename
    return uri

  @classmethod
  @pfx_method
  @uses_fstags
  @uses_Store
  @uses_runstate
  def from_dirpath(
      cls,
      dirpath: str,
      *,
      fstags: FSTags,
      S: Store,
      runstate: RunState,
      filename=None,
      follow_symlinks=False,
      force=False,
  ):
    if not isdirpath(dirpath):
      raise ValueError(f'not a directory path: {dirpath!r}')
    if filename is None:
      filename = basename(dirpath)
    D = Dir(filename)
    # we use an FSTags based cache for the file checksums
    # so take hold now so that we only update at the end
    with fstags:
      for entry in pfx_call(os.scandir, dirpath):
        with Pfx("scandir(%s)/%s", dirpath, entry.name):
          runstate.raiseif()
          assert entry.name not in D
          if entry.is_dir(follow_symlinks=follow_symlinks):
            uri = cls.from_dirpath(
                entry.path,
                follow_symlinks=follow_symlinks,
                force=force,
            )
          elif entry.is_file(follow_symlinks=follow_symlinks):
            uri = cls.from_filepath(
                entry.path,
                as_dirent=True,
                force=force,
            )
          else:
            warning("skipping, neither file nor directory")
            continue
          D[entry.name] = uri.as_Dirent(entry.name)
    return cls.from_Dirent(D, filename=filename)

  # TODO: unpack a directory if self.isdir
  @uses_runstate
  def saveas(self, fspath, *, runstate: RunState):
    ''' Save the contents of this `VTURI` to the filesystem path `fspath`.
    '''
    top_block = self.content_block
    filename = self.filename or f'{self.hashcode.hex()}.{self.hashcode.hashname}'
    with atomic_filename(fspath) as f:
      for B in progressbar(
          top_block.leaves,
          filename,
          itemlenfunc=len,
          total=len(top_block),
          units_scale=BINARY_BYTES_SCALE,
      ):
        runstate.raiseif()
        assert not B.indirect
        f.write(bytes(B))

  @classmethod
  def from_Dirent(
      cls,
      E: "_Dirent",
      filename=None,
  ):
    ''' Return a URI for the supplied Dirent `E`.
    '''
    block = HashCodeBlock(data=bytes(E))
    return cls(
        isdirent=True,
        isdir=E.isdir,
        hashcode=block.hashcode,
        indirect=block.indirect,
        filename=filename,
    )

  @classmethod
  def promote(cls, obj) -> "VTURI":
    ''' Promote `obj` to a `VTURI`.
        Note that all of these are essentially "free" conversions;
        this will never accept a filesystem path which might need
        Storing in order to make a URI.

        `obj` may be:
        * `VTURI`: unchanged
        * `str`: use `VTURI.from_uri`
        * `HashCodeBlock` or `IndirectBlock`: use `obj.uri`
        * `Dir`,`FileDirent`: use `cls.from_Dirent(obj)`
    '''
    if isinstance(obj, cls):
      return obj
    if isinstance(obj, str):
      return cls.from_uri(obj)
    if isinstance(obj, (HashCodeBlock, IndirectBlock)):
      return obj.uri
    if isinstance(obj, (Dir, FileDirent)):
      return cls.from_Dirent(obj)
    block = block_for(obj)
    if not isinstance(block, (HashCodeBlock, IndirectBlock)):
      block = HashCodeBlock(data=bytes(block))
    return block.uri
