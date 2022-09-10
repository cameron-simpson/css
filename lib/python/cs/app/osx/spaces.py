#!/usr/bin/env python3

''' Access to the display spaces.
'''

from contextlib import contextmanager
from getopt import GetoptError
import os
from os.path import (
    abspath,
    exists as existspath,
    isdir as isdirpath,
    join as joinpath,
    realpath,
)
from pprint import pprint
import random
import sys

from .objc import apple, cg

from CoreFoundation import CFUUIDCreateFromString
from typeguard import typechecked

from cs.cmdutils import BaseCommand
from cs.context import stackattrs
from cs.delta import monitor
from cs.fs import shortpath
from cs.logutils import warning
from cs.pfx import Pfx, pfx_call

CG = apple.CoreGraphics
HI = apple.HIServices

class Spaces:
  ''' The spaces for a particular display.
  '''

  @cg
  def __init__(self, display_index=0, *, cg_conn):
    ''' Initialise.

        Parameters:
        * `display_index`: optional display index, default `0`
        * `cg_conn`: optional CoreGraphics connection,
          default from `CoreGraphics._CGSDefaultConnection()`
    '''
    self.cg_conn = cg_conn
    self.display_index = display_index

  def forget(self):
    ''' Forget the current spaces information.
        This will cause it to be obtained anew.
    '''
    self.__dict__.pop('_spaces', None)

  @property
  def x(self):
    return "X"

  def __getattr__(self, attr):
    if attr == '_spaces':
      obj = self._load_spaces()
    else:
      raise AttributeError(f'{self.__class__.__name__}.{attr}')
    self.__dict__[attr] = obj
    return obj

  def __len__(self):
    return len(self._spaces["Spaces"])

  def __getitem__(self, space_index):
    ''' Return the space at index `space_index`.
        Note that the index counts from `0`, while the desktop space
        number counts from `1`.
    '''
    space_list = self._spaces["Spaces"]
    if space_index < 0 or space_index >= len(space_list):
      raise IndexError(space_index)
    return self._spaces["Spaces"][space_index]

  def _load_spaces(self):
    display_spaces = CG.CGSCopyManagedDisplaySpaces(self.cg_conn)
    return display_spaces[self.display_index]

  @property
  def current_index(self):
    ''' The index of the current space, found by scanning the spaces
        for the current space UUID.
        Returns `None` if not found.
    '''
    uuid = self.current["uuid"]
    for i, space in enumerate(self._spaces["Spaces"]):
      if space["uuid"] == uuid:
        return i
    return None

  @property
  def current(self):
    return self._spaces["Current Space"]

  @property
  def current_uuid(self):
    return self.current["uuid"]

  @property
  def display_uuid(self):
    return self._spaces["Display Identifier"]

  @property
  def display_id(self):
    cfuuid = CFUUIDCreateFromString(None, self.display_uuid)
    return CG.CGSGetDisplayForUUID(cfuuid)

  def get_wp_config(self, space_index: int):
    space = self[space_index]
    return HI.DesktopPictureCopyDisplayForSpace(
        self.display_id, 0, space["uuid"]
    )

  @typechecked
  def set_wp_config(self, space_index: int, wp_config: dict):
    pprint(wp_config)
    space = self[space_index]
    pfx_call(
        HI.DesktopPictureSetDisplayForSpace,
        self.display_id,
        wp_config,
        0,
        0,
        space["uuid"],
    )

  def monitor_current(self, **kw):
    ''' Return a `cs.delta.monitor` generator for changes to the
        "current" space i.e. changes representing a desktop space switch.
    '''
    return monitor(lambda: (self.forget(), self.current)[-1], **kw)

  def monitor_wp_config(self, space_index=None, **kw):
    ''' Return a `cs.delta.monitor` generator for the wallpaper
        configuration of a specific space (default `self.current_index`
        at the time of call).
    '''
    if space_index is None:
      space_index = self.current_index
    return monitor(
        lambda: (self.forget(), self.get_wp_config(space_index))[-1], **kw
    )

class SpacesCommand(BaseCommand):

  @contextmanager
  def run_context(self):
    options = self.options
    with stackattrs(options, spaces=Spaces()):
      yield

  def cmd_monitor(self, argv):
    ''' Usage: {cmd}
          Monitor space switches.
    '''
    if argv:
      raise GetoptError("extra arguments: %r" % (argv,))
    runstate = self.options.runstate
    spaces = self.options.spaces
    for old, new, changes in monitor(
        lambda: (spaces.forget(), dict(index=spaces.current_index))[-1],
        interval=0.1,
        runstate=runstate,
    ):
      print(old['index'] + 1, '->', new['index'] + 1)

  def cmd_wp(self, argv):
    ''' Usage: {cmd} [{{.|space#}} [wp-path]]
          Set or query the wallpaper for a space.
    '''
    options = self.options
    spaces = options.spaces
    space_num = None
    wp_path = None
    if argv:
      with Pfx("space# %r:", argv[0]):
        if argv[0] == '.':
          argv.pop(0)
          space_num = spaces.current_index + 1
          assert 1 <= space_num <= len(spaces)
        else:
          try:
            space_num = int(argv[0])
          except ValueError:
            pass
          else:
            argv.pop(0)
            if space_num < 1:
              raise GetoptError("space# counts from 1")
            if space_num > len(spaces):
              raise GetoptError("only %d spaces" % (len(spaces),))
      if argv:
        with Pfx("wp-path %r", argv[0]):
          wp_path = argv.pop(0)
          if not existspath(wp_path):
            raise GetoptError("not a file")
    if argv:
      raise GetoptError("extra aguments: %r" % (argv,))
    space_indices = range(len(spaces)
                          ) if space_num is None else (space_num - 1,)
    if wp_path is None:
      for space_index in space_indices:
        space_num = space_index + 1
        print("Space", space_num)
        for k, v in sorted(spaces.get_wp_config(space_index).items()):
          print(" ", k, "=", str(v).replace("\n", ""))
    else:
      space_index = space_num - 1
      if isdirpath(wp_path):
        wp_path = realpath(wp_path)
        images = [
            filename for filename in os.listdir(wp_path)
            if not filename.startswith('.') and '.' in filename
        ]
        if not images:
          warning("no *.* files in %r", wp_path)
          return 1
        lastname = random.choice(images)
        imagepath = joinpath(wp_path, lastname)
        wp_config = dict(
            BackgroundColor=(0, 0, 0),
            Change='TimeInterval',
            ChangePath=shortpath(wp_path),
            NewChangePath=shortpath(wp_path),
            ChangeTime=5,
            DynamicStyle=0,
            ImageFilePath=shortpath(imagepath),
            NewImageFilePath=shortpath(imagepath),
            LastName=lastname,
            Placement='SizeToFit',
            Random=True,
        )
      else:
        wp_config = dict(ImageFilePath=abspath(wp_path),)
      spaces.set_wp_config(space_index, wp_config)

  def cmd_wpm(self, argv):
    ''' Usage: {cmd} [{{.|space#}}]
          Monitor the wallpaper settings of a particular space.
    '''
    options = self.options
    spaces = options.spaces
    if not argv:
      space_index = spaces.current_index
    else:
      space_spec = argv.pop(0)
      if space_spec == '.':
        space_index = spaces.current_index
      else:
        space_num = int(space_spec)
        space_index = space_num - 1
    for old, new, changes in spaces.monitor_wp_config(
        space_index=space_index,
        runstate=options.runstate,
    ):
      print(changes)

if __name__ == '__main__':
  sys.exit(SpacesCommand(sys.argv).run())
