#!/usr/bin/python
#

''' Various ad hoc image related utility functions and classes.
'''

from functools import cached_property, partial
from os.path import basename, splitext
from queue import Queue
import shutil
from subprocess import Popen, PIPE
from tempfile import NamedTemporaryFile
from threading import Thread
from typing import Generator

from PIL import Image

from cs.ascii_art import box_char
from cs.cache import ConvCache, convof
from cs.deco import ALL
from cs.gimmicks import warning
from cs.pfx import pfx_call, pfx_method
from cs.psutils import run
from cs.tty import ttysizepx, WinSizePX, rmoveto_bs

__all__ = []

__version__ = '20260531-post'

DISTINFO = {
    'keywords': ["python3"],
    'classifiers': [
        "Development Status :: 5 - Production/Stable",
        "Environment :: Console",
        "Intended Audience :: Developers",
        "Programming Language :: Python :: 3",
        'Topic :: Multimedia :: Graphics :: Graphics Conversion',
    ],
    'install_requires': [
        'cs.cache',
        'cs.deco',
        'cs.gimmicks',
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
  with NamedTemporaryFile(prefix='sixel_from_image_bytes-',
                          suffix='.sixel') as T:
    T.write(image_bs)
    T.flush()
    return sixel(T.name)

def as_sixel_bytes(img: Image.Image) -> Generator[bytes]:
  ''' A generator yielding `img` as SIXEL format `bytes` chunks.

      This tries to use `libsixel` but falls back to the external
      executable `img2sixel` if that is not available.
  '''
  try:
    from libsixel import (
        sixel_output_new, sixel_output_unref, sixel_dither_new,
        sixel_dither_unref, sixel_dither_initialize, sixel_encode,
        sixel_dither_get, sixel_dither_set_palette,
        sixel_dither_set_pixelformat, SIXEL_PIXELFORMAT_RGBA8888,
        SIXEL_PIXELFORMAT_RGB888, SIXEL_PIXELFORMAT_PAL8, SIXEL_BUILTIN_G8,
        SIXEL_PIXELFORMAT_G8, SIXEL_BUILTIN_G1, SIXEL_PIXELFORMAT_G1
    )
  except ImportError as e:
    # use the external img2sixel command
    warning(f'could not import libsixel, falling back to img2sixel: {e}')
    with NamedTemporaryFile(suffix='.png') as imgT:
      img.save(imgT.name)
      with open(imgT.name, 'rb') as imgf:
        P = Popen(['img2sixel'], stdin=imgf, stdout=PIPE, buf=0)
        buf = bytearray(128 * 1024)
        while True:
          nread = P.stdout.readinto1(buf)
          if nread == 0:
            break
          yield buf[:nread]
        returncode = P.wait()
        if returncode != 0:
          warning(f'nonzero exit from img2sixel: {returncode}')
  else:
    # use libsixel directly
    # code adapted shamelessly from the libsixel example at:
    # https://github.com/saitoha/libsixel/blob/a0151d940af8bb0733dac77248bd55ae86949e56/examples/python/converter.py
    width, height = img.size
    image_bs = img.tobytes()
    outq = Queue(1)
    sixout = sixel_output_new(lambda bs, q: q.put(bs), outq)
    try:
      if img.mode == 'RGBA':
        dither = sixel_dither_new(256)
        sixel_dither_initialize(
            dither, image_bs, width, height, SIXEL_PIXELFORMAT_RGBA8888
        )
      elif img.mode == 'RGB':
        dither = sixel_dither_new(256)
        sixel_dither_initialize(
            dither, image_bs, width, height, SIXEL_PIXELFORMAT_RGB888
        )
      elif img.mode == 'P':
        palette = img.getpalette()
        dither = sixel_dither_new(256)
        sixel_dither_set_palette(dither, palette)
        sixel_dither_set_pixelformat(dither, SIXEL_PIXELFORMAT_PAL8)
      elif img.mode == 'L':
        dither = sixel_dither_get(SIXEL_BUILTIN_G8)
        sixel_dither_set_pixelformat(dither, SIXEL_PIXELFORMAT_G8)
      elif img.mode == '1':
        dither = sixel_dither_get(SIXEL_BUILTIN_G1)
        sixel_dither_set_pixelformat(dither, SIXEL_PIXELFORMAT_G1)
      else:
        raise RuntimeError(f'unexpected Image mode {img.mode=}')
      try:

        def six_encode():
          sixel_encode(image_bs, width, height, 1, dither, sixout)
          outq.put(None)

        T = Thread(target=six_encode)
        T.start()
        while True:
          bs = outq.get()
          if bs is None:
            break
          yield bs
        T.join()
      finally:
        sixel_dither_unref(dither)
    finally:
      sixel_output_unref(sixout)

def sized_sixel_bytes(img: Image.Image,
                      tty=1) -> tuple[list[bytes], int, int, WinSizePX]:
  ''' Wrapper for `as_sixel_bytes()` which returns a 4 tuple of
      `(list[bytes],char_width,char_height,tty_size_info)` being:
      - a list of the `bytes` chunks yielded from `as_sixel_bytes()`
      - the width of the SIXEL image in characters
      - the height of the SIXEL image in characters
      - the tty information used to calculate the result as a `WinSizePX`

      Parameters:
      - `img`: a Pillow `Image`
      - `tty`: an optional file or file descriptor for the tty whose
        size will be measured; the default is `1` for the standard output

      Writing the SIXEL data will occupy a rectangle `char_width`
      wide by `char_high` high and move the cursor down `char_high-1`
      rows in the original column.
  '''
  if isinstance(tty, int):
    tty_fd = tty
  else:
    tty_fd = tty.fileno()
  ttysize = ttysizepx(tty_fd)
  #print('tty size ', ttysize.columns, 'cols x', ttysize.rows, 'rows')
  #print('         ', ttysize.widthpx, 'px wide x', ttysize.heightpx, 'high')
  char_wide = ttysize.char_width
  char_high = ttysize.char_height
  #print('char cell', char_wide, 'px wide x ', char_high, 'high')
  width, height = img.size
  #print(width, 'x', height, 'pixels')
  char_wide = (width + char_wide - 1) // char_wide
  char_high = (height + char_high - 1) // char_high
  #print(chars_wide, 'chars wide x', chars_high, 'high')
  bss = list(as_sixel_bytes(img))
  return bss, char_wide, char_high, ttysize

class SixelImageString(str):
  ''' A `str` subclass for printing a Pillow `Image`.
  '''

  def __init__(self, img: Image.Image, tty=1):
    super().__init__()
    self.tty = tty
    self.img = img
    self.img_bss, self.width, self.height, _ = sized_sixel_bytes(img, tty=tty)

  def copy(self, img=None, **updates):
    ''' Return a copy of this mage string, with modifications.
    '''
    if img is None:
      img = self.img
    copy = type(self)(img)
    copy.__dict__.update(updates)
    return copy

  @cached_property
  def grid(self):
    return tuple(
        "".join(
            box_char(
                arc=True,
                up=y > 0 and (x == 0 or x == self.width - 1),
                down=y < self.height - 1 and (x == 0 or x == self.width - 1),
                left=x > 0 and (y == 0 or y == self.height - 1),
                right=x < self.width - 1 and (y == 0 or y == self.height - 1),
            ) for x in range(self.width)
        ) for y in range(self.height)
    )

  @cached_property
  def _grid_str(self) -> str:
    return "\n".join(self.grid)

  @cached_property
  def _post_str(self) -> str:
    return b''.join(
        (
            rmoveto_bs(-self.width, 1 - self.height),
            b''.join(self.img_bss),
            rmoveto_bs(self.width, 0),
        )
    ).decode('ascii')

  @cached_property
  def _full_str(self) -> str:
    return self._grid_str + self._post_str

  def __str__(self):
    return self._full_str

  def __repr__(self):
    return repr(self._grid_str)

  def __format__(self, spec):
    return format(self._grid_str, spec) + self._post_str

  def __len__(self):
    return len(self._grid_str)

  def __getitem__(self, index):
    return self._grid_str[index]

  def __contains__(self, substr):
    return substr in self._grid_str

  def indent(self, line_indent="  "):
    return self.copy(
        grid=tuple(line_indent + grid_row for grid_row in self.grid)
    )

  def split(self, *a, **kw):
    ''' Splitting an image string returns an image string for the
        final component.

        This is a bit of a bodge, but should work with stuff like `printt()`.
    '''
    parts = self._grid_str.split(*a, **kw)
    if not parts:
      return parts
    *strs, last_part = parts
    # bodge up an image string for the final row
    img_s = self.copy(grid=(last_part,))
    return [*strs, img_s]

  def splitlines(self):
    return self.split("\n")

  def rstrip(self):
    stripped = self._grid_str.rstrip()
    grid = stripped.split("\n")
    copy = self.copy(grid=grid)  ## , height=len(grid))
    return copy

  def resized_within(
      self,
      new_width: int,
      new_height: int,
      *,
      size_up=False,
      tty=None,
      warp_aspect=False,
  ):
    ''' Resize the image to fit within `new_width` x `new_height` characters.
    '''
    if tty is None:
      tty = self.tty
    if isinstance(tty, int):
      tty_fd = tty
    else:
      tty_fd = tty.fileno()
    tsize = ttysizepx(tty_fd)
    img_max_width = new_width * tsize.char_width
    img_max_height = new_height * tsize.char_height
    image = self.img
    # if image too big or size_up and image too small
    if (image.width > img_max_width or image.height > img_max_height
        or (size_up and self.width < new_width and self.height < new_height)):
      # return a new image string with the resized image
      xscale = img_max_width / image.width
      yscale = img_max_height / image.height
      if not warp_aspect:
        xscale = yscale = min(xscale, yscale)
      return type(self)(
          image.resize(
              (int(image.width * xscale), int(image.height * yscale))
          ),
          tty=tty
      )
    # otherwise return the original image string
    return self
