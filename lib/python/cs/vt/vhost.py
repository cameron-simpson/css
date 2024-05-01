#!/usr/bin/env python3
#
# Mapping of URL subpaths to block content.
# - Cameron Simpson <cs@cskk.id.au> 19sep2017
#

from binascii import unhexlify
from urllib.parse import unquote as url_unquote

from .uri import VTURI

def fetch(path, start=0, end=None):
  ''' Generator yielding block data from `path`.
      `path`: path to data indicator
      `start`: offset of first data yielded, default 0
      `end`: ending offset, default None
  '''
  uri = VTURI.from_uri(f'{VTURI.DEFAULT_SCHEME}://{path}')
  if uri.isdir:
    raise ValueError('Dirs not yet supported')
  B = uri.content_block
  # yield the block data
  for chunk in B.slices(start, end):
    yield chunk
