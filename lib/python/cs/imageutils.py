#!/usr/bin/python
#

''' Various ad hoc image related utility functions and classes.
'''

from functools import partial
import hashlib
from os.path import basename, splitext
import shutil

from PIL import Image

from cs.convcache import ConvCache
from cs.deco import ALL
from cs.pfx import pfx_call, pfx_method

__all__ = []

@ALL
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
