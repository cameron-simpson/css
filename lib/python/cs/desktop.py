#!/usr/bin/env python3

''' Utilities for managing my desktop - wallpapers, etc.
'''

from dataclasses import dataclass, field
import os
from os.path import expanduser

from cs.debug import trace

@dataclass
class Desktop:
  ''' Information about the desktop.
  '''

  SSV_DEFAULT = '~/im/screencaps'
  SSV_ENVVAR = 'SSV_DIR'
  WPDIR_DEFAULT = '~/im/wp'
  WPDIR_ENVVAR = 'WPDIR'
  WPLINKDIR_DEFAULT = '~/var/im/wp'
  WPLINKDIR_ENVVAR = 'WPLINKDIR'
  WPPATH_ENVVAR = 'WPPATH'

  # directory containing screenshot collections
  ssvdirpath: str = field(
      default_factory=lambda: os.environ.
      get(Desktop.SSV_ENVVAR, expanduser(Desktop.SSV_DEFAULT))
  )
  # directory containing wallpaper collections
  wpdirpath: str = field(
      default_factory=lambda: os.environ.
      get(Desktop.WPDIR_ENVVAR, expanduser(Desktop.WPDIR_DEFAULT))
  )
  # directory where wallpaper selection subdirectories are made
  wplinkdirpath: str = field(
      default_factory=lambda: os.environ.
      get(Desktop.WPLINKDIR_ENVVAR, expanduser(Desktop.WPLINKDIR_DEFAULT))
  )
  # the wallpaper search path
  wppath: str = None

  def __post_init__(self):
    if self.wppath is None:
      try:
        wppath = os.environ[Desktop.WPPATH_ENVVAR]
      except KeyError:
        self.wppath = [self.wpdirpath, self.ssvdirpath]
      else:
        self.wppath = wppath.split(os.pathsep)
    print("Desktop", self)
