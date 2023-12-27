#!/usr/bin/python
#

''' Various ad hoc image related utility functions and classes.
'''

from functools import lru_cache
import hashlib
import os
from os.path import dirname, join as joinpath, isdir, isfile, expanduser
from PIL import Image
from cs.pfx import Pfx
from cs.x import X

class ThumbnailCache(object):
  ''' A class to manage a collection of thumbnail images.
  '''

  DEFAULT_CACHEDIR = '~/var/cache/im/thumbnails'
  DEFAULT_HASHTYPE = 'sha1'
  DEFAULT_MIN_SIZE = 16
  DEFAULT_SCALE_STEP = 2.0

  def __init__(
      self,
      cachedir=None, hashtype=None,
      min_size=None, scale_step=None,
  ):
    if cachedir is None:
      cachedir = expanduser(self.DEFAULT_CACHEDIR)
    if not isdir(cachedir):
      with Pfx("mkdir(%r)", cachedir):
        os.mkdir(cachedir, 0o777)
    if hashtype is None:
      hashtype = self.DEFAULT_HASHTYPE
    if min_size is None:
      min_size = self.DEFAULT_MIN_SIZE
    if min_size < 8:
      raise ValueError("min_size must be >= 8, got: %d" % (min_size,))
    if scale_step is None:
      scale_step = self.DEFAULT_SCALE_STEP
    if scale_step < 1.1:
      raise ValueError("scale_step must be >= 1.1, got: %s" % (scale_step,))
    self.cachedir = cachedir
    self.hashtype = hashtype
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

  def thumb_for_path(self, dx, dy, image_path):
    ''' Return the path to the thumbnail of at least `(dx, dy)` size for `image`.
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
    with Pfx("thumb_for_path(%d,%d,%r)", dx, dy, image_path):
      image_info = iminfo(image_path)
      max_edge = self.thumb_scale(dx, dy)
      thumb_path = joinpath(self.cachedir, image_info.thumbpath(max_edge))
      if isfile(thumb_path):
        return thumb_path
      X("create thumbnail %r", thumb_path)
      # create the thumbnail
      image = Image.open(image_path)
      im_dx, im_dy = image.size
      if max_edge >= im_dx and max_edge >= im_dy:
        # thumbnail better served by original image
        return image_path
      # create the thumbnail
      scale_down = max(im_dx / max_edge, im_dy / max_edge)
      thumb_size = int(im_dx / scale_down), int(im_dy / scale_down)
      thumbnail = image.resize(thumb_size)
      thumbdir = dirname(thumb_path)
      if not isdir(thumbdir):
        os.makedirs(thumbdir)
      thumbnail.save(thumb_path)
      return thumb_path

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
