#!/usr/bin/env python3

''' URI scheme for VT URIs.
'''

from dataclasses import dataclass
import re
from typing import Optional, Union

from cs.deco import Promotable

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
      r'$', re.I
  )

  hashcode: HashCode
  indirect: bool
  scheme: str = DEFAULT_SCHEME
  network: Optional[str] = None
  span: Optional[int] = None

  def __str__(self):
    return (
        f'{self.scheme}:'
        f'//{self.network or ""}/{"i" if self.indirect else "h"}'
        f'/{self.hashcode!r}'
    )

  def block(self) -> Union[HashCodeBlock, IndirectBlock]:
    ''' Return a Block for this URI.
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
    )

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
