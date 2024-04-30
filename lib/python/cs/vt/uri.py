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
from cs.logutils import warning
from cs.pfx import Pfx, pfx_call, pfx_method
from cs.progress import progressbar

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
        Note that its meaning depends on `self.isdirent`.
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
      bs = trace(bytes)(self.block)
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
  @uses_Store
  def from_fspath(cls, fspath: str, *, S: Store):

    def save_uri(fspath, cachepath):
      print("VTURI.from_fspath: import", fspath)
      uri = S.block_for(fspath).uri
      uri.filename = basename(fspath)
      with open(cachepath, 'w') as cachef:
        print(uri, file=cachef)

    uri_path = convof(fspath, 'vt-uri', save_uri)
    with open(uri_path) as cachef:
      return cls.from_uri(cachef.readline().strip())

  def saveas(self, fspath):
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
      ):
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
