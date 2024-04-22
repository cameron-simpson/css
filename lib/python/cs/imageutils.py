#!/usr/bin/python
#

''' Various ad hoc image related utility functions and classes.
'''

from functools import partial
from os.path import basename, splitext
import shutil
from tempfile import NamedTemporaryFile

from PIL import Image

from cs.cache import ConvCache, convof
from cs.deco import ALL
from cs.pfx import pfx_call, pfx_method
from cs.psutils import run

__all__ = []

__version__ = '20240422'

DISTINFO = {
    'keywords': ["python3"],
    'classifiers': [
        "Development Status :: 2 - Beta",
        "Environment :: Console",
        "Intended Audience :: Developers",
        "Programming Language :: Python :: 3",
        'Topic :: Multimedia :: Graphics :: Graphics Conversion',
    ],
    'install_requires': [
        'cs.cache',
        'cs.deco',
        'cs.pfx',
        'cs.psutils',
        'Pillow',
    ],
}

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

def create_sixel(imagepath: str, sixelpath: str):
  ''' Use the `img2sixel` command to create a SIXEL image of `imagepath`
      at `sixelpath`.
  '''
  with open(imagepath, 'rb') as imagef:
    with open(sixelpath, 'wb') as sixelf:
      run(['img2sixel'], check=True, stdin=imagef, stdout=sixelf)

@ALL
def sixel(imagepath: str) -> str:
  ''' Return the filesystem path of a cached SIXEL version of the
      image at `imagepath`.
  '''
  return convof(imagepath, 'im/sixel', create_sixel, ext='sixel')

@ALL
def sixel_from_image_bytes(image_bs: bytes) -> str:
  ''' Return the filesystem path of a cached SIXEL version of the
      image data in `image_bs`.
  '''
  with NamedTemporaryFile(prefix='sixel_from_bytes-', suffix='.sixel') as T:
    T.write(image_bs)
    T.flush()
    return sixel(T.name)
