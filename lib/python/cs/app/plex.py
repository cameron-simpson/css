#!/usr/bin/env python3
#

''' Stuff for Plex media libraries.
'''

import builtins
from contextlib import contextmanager
from getopt import GetoptError
import os
from os.path import (
    basename,
    dirname,
    exists as existspath,
    expanduser,
    isdir as isdirpath,
    isfile as isfilepath,
    join as joinpath,
    relpath,
    samefile,
    splitext,
)
import sys
from cs.cmdutils import BaseCommand
from cs.fstags import FSTags, rfilepaths
from cs.logutils import warning
from cs.pfx import Pfx, pfx_call

DISTINFO = {
    'keywords': ["python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
    ],
    'entry_points': {
        'console_scripts': ['plex = cs.app.plex:main'],
    },
    'install_requires': ['cs.cmdutils', 'cs.fstags', 'cs.logutils'],
}

def main(argv=None):
  ''' Command line mode.
  '''
  if argv is None:
    argv = sys.argv
  return PlexCommand(argv).run()

class PlexCommand(BaseCommand):
  ''' `plex` main command line class.
  '''

  GETOPT_SPEC = 'd:'
  USAGE_FORMAT = r'''Usage: {cmd} [-d linktree] subcommand ...
      -d linktree   Specify the Plex link tree location,
                    default from \$PLEX_LINKTREE or ~/var/plextree'''

  def apply_defaults(self):
    ''' Set up the default values in `options`.
    '''
    self.options.fstags = FSTags()
    self.options.plextree = os.environ.get(
        'PLEX_LINKTREE', expanduser('~/var/plextree')
    )

  def apply_opt(self, opt, val):
    ''' Apply an option.
    '''
    if opt == '-d':
      self.options.plextree = val
    else:
      return super().apply_opt(opt, val)

  @contextmanager
  def run_context(self):
    ''' Use the FSTags context.
    '''
    with self.options.fstags:
      yield

  def cmd_linktree(self, argv):
    ''' Usage: {cmd} srctrees... dsttree
          Link media files from the srctrees into the dsttree
          using the Plex naming conventions.
    '''
    if len(argv) < 2:
      raise GetoptError("missing srctrees or dsttree")
    dstroot = argv.pop()
    srcroots = argv
    options = self.options
    fstags = options.fstags
    if not isdirpath(dstroot):
      raise GetoptError("dstroot does not exist: %s" % (dstroot,))
    for srcroot in srcroots:
      with Pfx(srcroot):
        for filepath in srcroot if isfilepath(srcroot) else sorted(
            rfilepaths(srcroot)):
          with Pfx(filepath):
            plex_linkpath(fstags, filepath, dstroot)

def plex_subpath(tagged_path):
  ''' Compute a Plex filesystem subpath based on the tags of `filepath`.
  '''
  base, ext = splitext(basename(tagged_path.fspath))
  itags = tagged_path.infer_tags()
  t = itags.auto
  tv = t.tv
  title = tv.series_title or t.title or base
  season = tv.season and int(tv.season)
  episode = isinstance(tv.episode, (int, str)) and int(tv.episode)
  episode_title = tv.episode_title
  extra = isinstance(tv.extra, (int, str)) and int(tv.extra)
  extra_title = tv.extra_title
  part = tv.part and int(tv.part)
  dstbase = title
  if tv.series_title:
    # TV Series
    dstpath = ['TV Shows', tv.series_title, f'Season {season:02d}']
    if episode:
      dstbase += f' - s{season:02d}e{episode:02d}'
    else:
      dstbase += f' - s{season:02d}x{extra:02d}'
  else:
    # Movie
    dstpath = ['Movies']
    if episode:
      dstbase += f' - {episode:d}'
  if episode_title and episode_title != title:
    dstbase += f' - {episode_title}'
  elif extra_title and extra_title != title:
    dstbase += f' - {extra_title}'
  if part:
    dstbase += f' - pt{part:d}'
  dstbase = dstbase.replace('/', '::')
  dstpath.append(dstbase)
  return joinpath(*dstpath) + ext

# pylint: disable=redefined-builtin
def plex_linkpath(
    fstags, filepath, plex_topdirpath, do_hardlink=False, print=None
):
  ''' Link `filepath` into `plex_topdirpath`.

      Parameters:
      * `fstags`: the `FSTags` instance
      * `filepath`: filesystem pathname of file to link into Plex tree
      * `plex_topdirpath`: filesystem pathname of the Plex tree
      * `do_hardlink`: use a hard link if true, otherwise a softlink;
        default `False`
      * `print`: print function for the link action,
        default from `builtins.print`
  '''
  if print is None:
    print = builtins.print
  tagged_path = fstags[filepath]
  subpath = plex_subpath(tagged_path)
  plexpath = joinpath(plex_topdirpath, subpath)
  plexdirpath = dirname(plexpath)
  if do_hardlink:
    if existspath(plexpath):
      if samefile(filepath, plexpath):
        return
      pfx_call(os.unlink, plexpath)
    print(subpath, "<=", basename(filepath))
    if not isdirpath(plexdirpath):
      pfx_call(os.makedirs, plexdirpath)
    pfx_call(os.link, filepath, plexpath)
  else:
    rfilepath = relpath(filepath, plexdirpath)
    if existspath(plexpath):
      try:
        sympath = os.readlink(plexpath)
      except OSError as e:
        warning("readlink(%r): %s", plexpath, e)
      else:
        if rfilepath == sympath:
          return
      pfx_call(os.unlink, plexpath)
    print(subpath, "<=", basename(filepath))
    if not isdirpath(plexdirpath):
      pfx_call(os.makedirs, plexdirpath)
    pfx_call(os.symlink, rfilepath, plexpath)

if __name__ == '__main__':
  sys.exit(main(sys.argv))
