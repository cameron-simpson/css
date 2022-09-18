#!/usr/bin/env python3

''' Tagger utlity methods.
'''

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
from PIL import Image, UnidentifiedImageError
from cs.buffer import CornuCopyBuffer
from cs.fstags import FSTags
from cs.lex import hexify
from cs.logutils import warning
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

_fstags = FSTags()
_conv_cache = defaultdict(dict)

def image_size(path):
  ''' Return the pixel size of the image file at `path`
      as an `(dx,dy)` tuple, or `None` if the contents cannot be parsed.
  '''
  tagged = _fstags[path]
  try:
    size = tagged['pil.size']
  except KeyError:
    try:
      with Image.open(path) as im:
        tagged['pil.format'] = im.format
        size = tagged['pil.size'] = im.size
        tagged['mime_type'] = 'image/' + im.format.lower()
    except (UnidentifiedImageError, IsADirectoryError) as e:
      warning("unhandled image: %s", e)
      size = tagged['pil.size'] = None
  if size is not None:
    size = tuple(size)
  return size

@pfx
def pngfor(path, max_size=None, *, min_size=None, cached=None, force=False):
  ''' Create a PNG version of the image at `path`,
      scaled to fit within some size constraints.
      return the pathname of the PNG file.

      Parameters:
      * `max_size`: optional `(width,height)` tuple, default `(1920,1800)`
      * `min_size`: optional `(width,height)` tuple, default half of `max_size`
      * `cached`: optional mapping of `(path,'png',size)`->`pngof_path`
        where size is the chosen final size tuple
      * `force`: optional flag (default `False`)
        to force recreation of the PNG version and associated cache entry
  '''
  if max_size is None:
    max_size = 1920, 1080
  if min_size is None:
    min_size = max_size[0] // 2, max_size[1] // 2
  if cached is None:
    cached = _conv_cache
  tagged = _fstags[path]
  path = tagged.fspath
  size = image_size(path)
  if size is None:
    return None
  # choose a target size
  if size[0] > max_size[0] or size[1] > max_size[1]:
    scale = min(max_size[0] / size[0], max_size[1] / size[1])
    re_size = int(size[0] * scale), int(size[1] * scale)
    ##warning("too big, rescale by %s from %r to %r", scale, size, re_size)
    key = path, 'png', re_size
  elif size[0] < min_size[0] or size[1] < min_size[1]:
    scale = min(min_size[0] / size[0], min_size[1] / size[1])
    re_size = int(size[0] * scale), int(size[1] * scale)
    ##warning("too small, rescale by %s from %r to %r", scale, size, re_size)
    key = path, 'png', re_size
  else:
    re_size = None
    key = path, 'png', size
  cached_path = cached.get(key)
  if cached_path:
    return cached_path
  if tagged['pil.format'] == 'PNG' and re_size is None:
    # right format, same size - return ourself
    cached[key] = tagged.fspath
    return tagged.fspath
  # path to converted file
  hashcode = SHA256.from_pathname(path)
  pngbase = f'{hashcode}.png'
  if not isdirpath(CONVCACHE_ROOT):
    pfx_call(os.mkdir, CONVCACHE_ROOT)
  convsize = re_size or size
  convdirpath = joinpath(CONVCACHE_ROOT, f'png/{convsize[0]}x{convsize[1]}')
  if not isdirpath(convdirpath):
    pfx_call(os.makedirs, convdirpath)
  pngpath = joinpath(convdirpath, pngbase)
  if force or not isfilepath(pngpath):
    try:
      with Image.open(path) as im:
        if re_size is None:
          pfx_call(im.save, pngpath, 'PNG')
        else:
          im2 = im.resize(re_size)
          pfx_call(im2.save, pngpath, 'PNG')
    except UnidentifiedImageError as e:
      warning("unhandled image: %s", e)
      pngpath = None
  cached[key] = pngpath
  return pngpath

class _HashCode(bytes):

  __slots__ = ()

  def __str__(self):
    return f'{type(self).__name__.lower()}:{hexify(self)}'

  @classmethod
  def from_data(cls, bs):
    ''' Compute hashcode from the data `bs`.
    '''
    return cls(cls.hashfunc(bs).digest())

  @classmethod
  def from_buffer(cls, bfr):
    ''' Compute hashcode from the contents of the `CornuCopyBuffer` `bfr`.
    '''
    h = cls.hashfunc()
    for bs in bfr:
      h.update(bs)
    return cls(h.digest())

  @classmethod
  def from_pathname(cls, pathname, readsize=None, **kw):
    ''' Compute hashcode from the contents of the file `pathname`.
    '''
    if readsize is None:
      readsize = DEFAULT_READSIZE
    return cls.from_buffer(
        CornuCopyBuffer.from_filename(pathname, readsize=readsize, **kw)
    )

class SHA256(_HashCode):
  ''' SHA256 hashcode class.
  '''

  __slots__ = ()
  hashfunc = hashlib.sha256
