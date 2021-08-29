#!/usr/bin/env python3

from collections import defaultdict
import hashlib
import os
from os.path import (
    basename,
    expanduser,
    isdir as isdirpath,
    isfile as isfilepath,
    join as joinpath,
    splitext,
)
from PIL import Image
from cs.buffer import CornuCopyBuffer
from cs.lex import hexify
from cs.pfx import pfx, pfx_call

from cs.x import X

CONVCACHE_ROOT_ENVVAR = 'CONVCACHE_ROOT'
CONVCACHE_ROOT = os.environ.get(
    CONVCACHE_ROOT_ENVVAR, expanduser('~/var/cache/convof')
)

DEFAULT_READSIZE = 1024 * 1024  # 1MiB

def ispng(pathname):
  ''' Is `pathname` that of a PNG image?
      Just tests the filename extension at present.
  '''
  return splitext(basename(pathname))[1].lower() == '.png'

_conv_cache = defaultdict(dict)

@pfx
def pngfor(pathname, cached=None, force=False):
  ''' Create a PNG version of the image at `pathname`,
      return the pathname of the PNG file.

      Parameters:
      * `cached`: optional mapping of `'png'`->`pathname`->pngof_path
      * `force`: optional flag (default `False`)
        to force recreation of the PNG version and associated cache entry
  '''
  if cached is None:
    cached = _conv_cache
  pngpath = None if False else cached['png'].get(pathname)
  if pngpath is None:
    hashcode = SHA256.from_pathname(pathname)
    pngbase = f'{hashcode}.png'
    convdirpath = joinpath(CONVCACHE_ROOT, 'png')
    if not isdirpath(convdirpath):
      pfx_call(os.mkdir, convdirpath)
    pngpath = joinpath(convdirpath, pngbase)
    if force or not isfilepath(pngpath):
      X("create %r from %r", pngpath, pathname)
      with Image.open(pathname) as im:
        pfx_call(im.save, pngpath, 'PNG')
    cached['png'][pathname] = pngpath
  return pngpath

class _HashCode(bytes):

  __slots__ = ()

  def __str__(self):
    return f'{type(self).__name__.lower()}:{hexify(self)}'

  @classmethod
  def from_data(cls, bs):
    return cls(cls.hashfunc(bs).digest())

  @classmethod
  def from_buffer(cls, bfr):
    h = cls.hashfunc()
    for bs in bfr:
      h.update(bs)
    return cls(h.digest())

  @classmethod
  def from_pathname(cls, pathname, readsize=None, **kw):
    if readsize is None:
      readsize = DEFAULT_READSIZE
    return cls.from_buffer(
        CornuCopyBuffer.from_filename(pathname, readsize=readsize, **kw)
    )

class SHA256(_HashCode):

  __slots__ = ()
  hashfunc = hashlib.sha256
