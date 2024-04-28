#!/usr/bin/env python3

''' URI scheme for VT URIs.
'''

from dataclasses import dataclass
from os.path import basename
import re
from typing import Optional, Union

from cs.cache import convof
from cs.deco import Promotable

from . import Store, uses_Store
from .block import HashCodeBlock, IndirectBlock
from .blockify import block_for
from .hash import HashCode

@dataclass
class VTURI(Promotable):
  ''' A representation of a `HashCodeBlock` as a URI.
  '''

  DEFAULT_SCHEME = 'x-vt'
  _URI_RE = re.compile(
      r'(?P<scheme>[a-z][-a-z0-9]*):'
      r'//(?P<network>[^/]*)'
      r'/(?P<indirect>[hi])'
      r'/(?P<hashname>[a-z][a-z0-9]*):(?P<hashtext>([0-9a-f][0-9a-f])+)'
      r'(/(?P<filename>[^/?#]+))?'
      r'$', re.I
  )

  hashcode: HashCode
  indirect: bool
  scheme: str = DEFAULT_SCHEME
  network: Optional[str] = None
  span: Optional[int] = None
  filename: Optional[str] = None

  def __str__(self):
    return ''.join(
        (
            f'{self.scheme}:',
            f'//{self.network or ""}/{"i" if self.indirect else "h"}',
            f'/{self.hashcode.hashname}:{self.hashcode.hex()}',
            (f'/{self.filename}' if self.filename else ''),
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
    filename = uri.filename or f'{uri.hashcode.hex()}.{uri.hashcode.hashname}'
    with atomic_filename(fspath) as f:
      for B in progressbar(
          uri.block.leaves,
          filename,
          itemlenfunc=len,
          total=len(uri.block),
      ):
        assert not B.indirect
        f.write(bytes(B))

  @classmethod
  def promote(cls, obj) -> "VTURI":
    ''' Promote `obj` to a `VTURI`.

        `obj` may be:
        * `VTURI`: unchanged
        * `str`: use `VTURI.from_uri`
        * `HashCodeBlock` or `IndirectBlock`: use `obj.uri`
        * `bytes`:
    '''
    if isinstance(obj, cls):
      return obj
    if isinstance(obj, str):
      return cls.from_uri(obj)
    if isinstance(obj, (HashCodeBlock, IndirectBlock)):
      return obj.uri
    block = block_for(obj)
    if not isinstance(block, (HashCodeBlock, IndirectBlock)):
      block = HashCodeBlock(data=bytes(block))
    return block.uri
