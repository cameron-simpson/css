#!/usr/bin/python
#

''' Various ad hoc image related utility functions and classes.
'''

from functools import lru_cache, partial
import hashlib
import os
from os.path import (
    basename,
    join as joinpath,
    splitext,
)
import shutil

from PIL import Image

from cs.convcache import ConvCache
from cs.deco import ALL
from cs.pfx import pfx_call, pfx_method

class ThumbnailCache(ConvCache):
  ''' A class to manage a collection of thumbnail images.
  '''

  DEFAULT_CACHE_BASEPATH = '~/var/cache/im/thumbnails'
  DEFAULT_MIN_SIZE = 16
  DEFAULT_SCALE_STEP = 2.0

  def __init__(
      self,
      cachedir=None,
      *,
      min_size=None,
      scale_step=None,
  ):
    super().__init__(cachedir)
    if min_size is None:
      min_size = self.DEFAULT_MIN_SIZE
    if min_size < 8:
      raise ValueError("min_size must be >= 8, got: %d" % (min_size,))
    if scale_step is None:
      scale_step = self.DEFAULT_SCALE_STEP
    if scale_step < 1.1:
      raise ValueError("scale_step must be >= 1.1, got: %s" % (scale_step,))
    self.cachedir = cachedir
    self.min_size = min_size
    self.scale_step = scale_step

  def thumb_scale(self, dx, dy):
    ''' Compute thumbnail size from target dimensions.
    '''
    target = max(dx, dy)
    scale = float(self.min_size)
    while int(scale) < target:
      scale *= self.scale_step
    return int(scale)

  @pfx_method
  def thumb_for_path(self, dx, dy, imagepath):
    ''' Return the path to the thumbnail of at least `(dx,dy)` size for `imagepath`.
        Creates the thumbnail if necessary.

        Parameters:
        * `dx`, `dy`: the target display size for the thumbnail.
        * `image`: the source image, an image file pathname or a
          PIL Image instance.

        The generated thumbnail will have at least these dimensions
        unless either exceeds the size of the source image.
        In that case the original source image will be returned;
        this result can be recognised with an identity check.

        Thumbnail paths are named after the SHA1 digest of their file content.
    '''
    max_edge = self.thumb_scale(dx, dy)
    _, ext = splitext(basename(imagepath))
    ext = ext[1:] if ext else None
    return self.convof(
        imagepath,
        str(max_edge),
        partial(self.create_thumbnail, max_edge=max_edge),
        ext=ext,
    )

  @trace
  def create_thumbnail(self, imagepath: str, thumbpath: str, max_edge: int):
    ''' Write a thumbnail image no larger than `max_edge`x`max_edge`
        of `imagepath` to `thumbpath`.
    '''
    with Image.open(imagepath) as image:
      im_dx, im_dy = image.size
      if max_edge >= im_dx and max_edge >= im_dy:
        # thumbnail better served by original image
        pfx_call(shutil.copyfile, imagepath, thumbpath)
      else:
        # create the thumbnail
        scale_down = max(im_dx / max_edge, im_dy / max_edge)
        thumb_size = int(im_dx / scale_down), int(im_dy / scale_down)
        thumbnail = image.resize(thumb_size)
        thumbnail.save(thumbpath)

def iminfo(image_path):
  ''' Return a cached ImInfo instance for the specified `image_path`.
  '''
  st = os.stat(image_path)
  return ImInfo(image_path, st.st_size, st.st_mtime)

@lru_cache(maxsize=131072)
class ImInfo(object):
  ''' A cache image information object.

      We access this via the iminfo function which keys the cache on
      `(image_path, st_size, st_mtime)`.
  '''

  def __init__(self, image_path, st_size, st_mtime):
    self.image_path = image_path
    self.st_size = st_size
    self.st_mtime = st_mtime
    self._hexdigest = None
    self._thumbpaths = {}   # thumb_scale => path

  @property
  def hexdigest(self):
    ''' Return the hexdigest of this imagefile.
    '''
    digits = self._hexdigest
    if not digits:
      # read the file and compute the hash
      h = hashdigest(self.image_path, 'sha1')
      digits = self._hexdigest = h.hexdigest()
    return digits

  def thumbpath(self, max_edge):
    ''' Return the _relative_ pathname for the thumbnail file to cover a
        rectangle with maximum edge `max_edge`.
    '''
    path = self._thumbpaths.get(max_edge)
    if not path:
      digits = self.hexdigest
      path = self._thumbpaths[max_edge] = joinpath(
          'sha1', digits[:2], digits[2:] + '-' + str(max_edge) + '.jpg')
    return path

def hashdigest(path, hashclassname):
  ''' Return a hash of the contents of the file at `path`.
  '''
  h = hashlib.new(hashclassname)
  with open(path, 'rb') as f:
    while True:
      bs = f.read(131072)
      if bs:
        h.update(bs)
      else:
        break
  return h
