#!/usr/bin/env python3

''' Access to the MacOS X display spaces.
'''

from contextlib import contextmanager
from dataclasses import dataclass
from getopt import GetoptError
import os
from os.path import (
    abspath,
    exists as existspath,
    isdir as isdirpath,
    join as joinpath,
)
from pprint import pprint
from random import choice as random_choice
import sys
from typing import Optional

from CoreFoundation import CFUUIDCreateFromString

from typeguard import typechecked

from cs.cmdutils import BaseCommand
from cs.context import stackattrs
from cs.delta import monitor
from cs.logutils import warning
from cs.pfx import Pfx, pfx_call

from .misc import macos_version

from .objc import apple, cg

__version__ = '20250108-post'

DISTINFO = {
    'keywords': ["python3"],
    'classifiers': [
        "Development Status :: 4 - Beta",
        "Environment :: MacOS X",
        "Intended Audience :: End Users/Desktop",
        "Programming Language :: Python :: 3",
        "Topic :: Desktop Environment",
    ],
    'install_requires': [
        'cs.app.osx.misc',
        'cs.app.osx.objc',
        'cs.cmdutils',
        'cs.context',
        'cs.delta',
        'cs.logutils',
        'cs.pfx',
        'pyobjc[allbindings]',
        'typeguard',
    ],
    'entry_points': {
        'console_scripts': {
            'spaces': 'cs.app.osx.spaces:main',
        },
    },
}

CG = apple.CoreGraphics
HI = apple.HIServices

DEFAULT_BACKGROUND_RGB = 0, 0, 0  # black background
def main(argv=None):
  ''' cs.app.osx.spaces command line mode.
  '''
  return SpacesCommand(argv).run()

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

  def popindices(self, argv):
    ''' Pop a leading spaces specification from `argv` if present,
        return a list of the indices it represents.
        If there is no spaces specification, return `None`.

        Note that space indices count from `0`, and space numbers count from `1`.

        The following spaces specifications are recognised:
        * `.`: the current space index
        * `*`: all the space indices
        * a positive integer `spn`: `spn-1`
    '''
    space_indices = None
    if argv:
      arg0 = argv[0]
      with Pfx("space# %r:", arg0):
        if arg0 == '.':
          argv.pop(0)
          space_indices = [self.current_index]
        elif arg0 == '*':
          argv.pop(0)
          space_indices = list(range(len(self)))
        else:
          try:
            space_num = int(arg0)
          except ValueError:
            pass
          else:
            argv.pop(0)
            if space_num < 1:
              raise GetoptError("space# counts from 1")
            if space_num > len(self):
              raise GetoptError(f'only {len(self)} spaces')
            space_indices = (space_num - 1,)
    return space_indices

  @property
  def current(self):
    ''' The current space.
    '''
    return self._spaces["Current Space"]

  @property
  def current_uuid(self):
    ''' The UUID of the current space.
    '''
    return self.current["uuid"]

  @property
  def display_uuid(self):
    ''' The UUID of the display.
    '''
    return self._spaces["Display Identifier"]

  @property
  def display_id(self):
    ''' The display identifier of the display.
    '''
    cfuuid = CFUUIDCreateFromString(None, self.display_uuid)
    return CG.CGSGetDisplayForUUID(cfuuid)

  def get_wp_config(self, space_index: int):
    ''' Get the desktop picture configuration of the space at `space_index`.
    '''
    space = self[space_index]
    return HI.DesktopPictureCopyDisplayForSpace(
        self.display_id, 0, space["uuid"]
    )

  @typechecked
  def set_wp_config(self, space_index: int, wp_config: dict):
    ''' Set the desktop picture configuration of the space at
        `space_index` using the `dict` `wp_config`.
    '''
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

  def set_wp_dirpath(
  @staticmethod
  def spaces_pathfor(fspath: str):
    ''' Return `fspath` adjusted for use in a spaces configuration.

        Prior to MacOS Sonoma (14.5), this just returns the absolute path.

        In MacOS Sonoma there's some hideous bug in the
        DesktopPictureSetDisplayForSpace library where it seems to
        see a leading home directory path and replace it with `/~`
        (instead of something plausible like '~'), perhaps intended
        for making paths track homedir moves.  It turns out that
        providing a _relative_ path from '/' does The Right Thing.
        Ugh.
    '''
    fspath = abspath(fspath)
    if macos_version < (14, 5):
      spaces_path = fspath
    else:
      spaces_path = fspath[1:]
      # a cut at seeing if the ~ _is_ meaningful, but it doesn't work
      ##home = os.environ.get('HOME')
      ##if home and isdirpath(home):
      ##  home_prefix = f'{home}/'
      ##  if fspath.startswith(home_prefix):
      ##    spaces_path = f'~/{cutprefix(fspath,home_prefix)}'
    return spaces_path

      self,
      space_index: int,
      dirpath: str,
      *,
      background_color=DEFAULT_BACKGROUND_RGB,
      random=True,
      change_duration=5.0,
      placement='SizeToFit',
  ):
    print("spaces set_wp_dirpath", space_index, dirpath)
    images = [
        filename for filename in os.listdir(dirpath)
        if not filename.startswith('.') and '.' in filename
    ]
    if not images:
      warning("no *.* files in %r", dirpath)
      return 1
    lastname = random_choice(images)
    imagepath = abspath(joinpath(dirpath, lastname))
    if macos_version < (14, 5):
      # This worked before I upgraded to Sonoma, MacOS 14.5.
      # pylint: disable=use-dict-literal
      wp_config = dict(
          BackgroundColor=background_color,
          Change='TimeInterval',
          ChangePath=abspath(dirpath),
          NewChangePath=abspath(dirpath),
          ChangeTime=change_duration,
          DynamicStyle=0,
          ImageFilePath=imagepath,
          NewImageFilePath=imagepath,
          LastName=lastname,
          Placement=placement,
          Random=random,
      )  # pylint: disable=use-dict-literal
    else:
      # MacOS Sonoma onward
      # There's some hideous bug in the DesktopPictureSetDisplayForSpace
      # library where it seems to see a leading home directory path
      # and replace it with '/~' (instead of something plausible like '~'),
      # perhaps intended for making paths track homedir moves.
      # It turns out that providing a _relative_ path from '/'
      # does The Right Thing. Ugh.
      dirpath = abspath(dirpath)
      dirpath = dirpath[1:]
      ##rdirpath = relpath(dirpath, os.environ['HOME'])
      ##if not rdirpath.startswith('../'):
      ##  dirpath = rdirpath
      wp_config = dict(
          BackgroundColor=(0, 0, 0),
          Change='TimeInterval',
          ChangePath=dirpath,
          NewChangePath=dirpath,
          ChangeDuration=change_duration,
          DynamicStyle=0,
          ##ImageFilePath=imagepath,
          ##NewImageFilePath=imagepath,
          LastName=lastname,
          Placement=placement,
          Random=random,
      )  # pylint: disable=use-dict-literal
    self.set_wp_config(space_index, wp_config)

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
  ''' A command line implementation for manipulating spaces.
  '''

  @dataclass
  class Options(BaseCommand.Options):
    ''' Options for SpacesCommand.
    '''
    spaces: Optional[Spaces] = None

  @contextmanager
  def run_context(self, **kw):
    ''' Set `options.spaces` to a `Spaces` instnace during a command run.
    '''
    with super().run_context(**kw):
      options = self.options
      with stackattrs(options, spaces=Spaces()):
        yield

  def cmd_current(self, argv):
    ''' Usage: {cmd}
          Print the current space number.
    '''
    if argv:
      raise GetoptError(f'extra arguments: {argv!r}')
    print(self.options.spaces.current_index + 1)

  def cmd_monitor(self, argv):
    ''' Usage: {cmd}
          Monitor space switches.
    '''
    if argv:
      raise GetoptError(f'extra arguments: {argv!r}')
    spaces = self.options.spaces
    for old, new, changes in monitor(
        lambda: (spaces.forget(), {'index': spaces.current_index})[-1],
        interval=0.1,
    ):
      print(old['index'] + 1, '->', new['index'] + 1, flush=True)

  def cmd_wp(self, argv):
    ''' Usage: {cmd} [{{.|space#|*}} [wp-path]]
          Set or query the wallpaper for a space.
          The optional space number may be "." to indicate the
          current space or "*" to indicate all spaces.
    '''
    options = self.options
    spaces = options.spaces
    wp_path = None
    space_indices = spaces.popindices(argv)
    if argv:
      wp_path = argv.pop(0)
      with Pfx("wp-path %r", wp_path):
        if not existspath(wp_path):
          raise GetoptError("not a file")
    if argv:
      raise GetoptError(f'extra aguments: {argv!r}')
    if wp_path is None:
      if space_indices is None:
        space_indices = list(range(len(spaces)))
      for space_index in space_indices:
        space_num = space_index + 1
        print("Space", space_num)
        for k, v in sorted(spaces.get_wp_config(space_index).items()):
          print(" ", k, "=", str(v).replace("\n", ""))
    else:
      if space_indices is None:
        space_indices = [spaces.current_index]
      for space_index in space_indices:
        with Pfx("%d <- %r", space_index + 1, wp_path):
          if isdirpath(wp_path):
            spaces.set_wp_dir(wp_path)
          else:
            # pylint: disable=use-dict-literal
            wp_config = dict(ImageFilePath=abspath(wp_path),)
          spaces.set_wp_config(space_index, wp_config)
    return 0

  def cmd_wpm(self, argv):
    ''' Usage: {cmd} [{{.|space#}}]
          Monitor the wallpaper settings of a particular space.
    '''
    options = self.options
    spaces = options.spaces
    space_indices = spaces.popindices(argv)
    if space_indices is None:
      space_index = spaces.current_index
    else:
      try:
        space_index, = space_indices
      except ValueError:
        # pylint: disable=raise-missing-from
        raise GetoptError(
            f'expected exactly one space index, got: {space_indices!r}'
        )
    for old, new, changes in spaces.monitor_wp_config(
        space_index=space_index,
        runstate=options.runstate,
    ):
      print(changes, flush=True)

if __name__ == '__main__':
  sys.exit(main(sys.argv))
