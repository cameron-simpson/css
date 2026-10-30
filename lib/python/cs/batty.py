#!/usr/bin/env python3

''' Utility functions for working with `batgrl`, the BadAss Terminal GRaphics Library.
'''

from functools import cached_property
from pathlib import Path

from batgrl.app import App
from batgrl.gadgets.gadget import SizeHint
from batgrl.gadgets.image import Image as ImageGadget
from PIL.Image import Image, open as open_image

from cs.fs import HasFSPath

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
  def image(self) -> Image:
    return open_image(self.fspath)

class Preview(App):

  def __init__(self, obj, inline=True, inline_height=20, **app_kw):
    print("Preview init")
    super().__init__(inline=inline, inline_height=inline_height, **app_kw)
    self.obj = obj

  async def on_start(self):
    print("Preview on_start")
    self.img_view = ImageGadget(
        path=Path(self.obj.fspath),
        size_hint=SizeHint(width_hint=0.3, height_hint=0.8),
    )
    self.add_gadget(self.img_view)

if __name__ == '__main__':
  import sys
  cmd, imagepath = sys.argv
  app = Preview(Previewable(imagepath))
  print("app.run()...")
  app.run()
