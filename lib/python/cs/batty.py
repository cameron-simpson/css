#!/usr/bin/env python3

''' Utility functions for working with `batgrl`, the BadAss Terminal GRaphics Library.
'''

from functools import cached_property
from os import PathLike
from pathlib import Path

from batgrl.app import App
from batgrl.gadgets.gadget import SizeHint
import batgrl.gadgets.image
from PIL.Image import Image as PILImage, open as open_image

from cs.fs import HasFSPath
from cs.gimmicks import warning
from cs.pfx import pfx_method

# TODO: also accept a PIL.Image.Image
class ImageGadget(batgrl.gadgets.image.Image):
  ''' A shim subclass for `batgrl.gadgets.image.Image` which also accepts
      `path` as a string, optionally as the first positional parameter.
  '''

  @pfx_method
  def __init__(self, path: PathLike | str | None = None, **image_kw):
    if path is not None and not isinstance(path, Path):
      if isinstance(path, str):
        path = Path(path)
      elif isinstance(path, PathLike):
        path = Path(path.__fspath__())
      else:
        warning(f'unexpected type for path: {type(path)}:{path=}')
    super().__init__(path=path, **image_kw)

class Previewable(HasFSPath):

  @property
  def fspath(self):
    return getattr(self, '_fspath', None)

  @fspath.setter
  def fspath(self, new_fspath):
    self._fspath = new_fspath
    for attr in 'image', :
      try:
        delattr(self, attr)
      except AttributeError:
        pass

  @cached_property
  def image(self) -> PILImage:
    return open_image(self.fspath)

class Preview(App):

  def __init__(self, obj, inline=True, inline_height=20, **app_kw):
    print("Preview init")
    super().__init__(inline=inline, inline_height=inline_height, **app_kw)
    self.obj = obj

  async def on_start(self):
    self.img_view = ImageGadget(
        path=self.obj.fspath,
        size_hint=SizeHint(width_hint=0.3, height_hint=0.8),
    )
    self.add_gadget(self.img_view)

if __name__ == '__main__':
  import sys
  from cs.logutils import setup_logging
  cmd, imagepath = sys.argv
  setup_logging(cmd)
  app = Preview(Previewable(imagepath))
  app.run()
