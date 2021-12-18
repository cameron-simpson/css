#!/usr/bin/env python3
#

''' Stuff for Plex media libraries.
'''

from contextlib import contextmanager
from getopt import GetoptError
import os
from os.path import (
    abspath, exists as existspath, basename, dirname, join as joinpath,
    splitext, isdir as isdirpath
)
import sys
from cs.cmdutils import BaseCommand
from cs.fstags import FSTags, rfilepaths, TaggedPath
from cs.logutils import Pfx, warning, error
from cs.pfx import pfx, pfx_method, XP

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

  GETOPT_SPEC = ''

  def apply_defaults(self):
    ''' Set up the default values in `options`.
    '''
    self.options.fstags = FSTags()

  @contextmanager
  def run_context(self):
    ''' Use the FSTags context.
    '''
    with self.options.fstags:
      yield

  def cmd_linktree(self, argv):
    ''' Usage: {cmd} [-u] srctrees... dsttree
          Link media files from the srctrees into the dsttree
          using the Plex naming conventions.
          -u  Update mode: require dsttree to already exist,
              replace conflicting paths.
    '''
    update_mode = False
    if argv and argv[0] == '-u':
      argv.pop(0)
      update_mode = True
    if len(argv) < 2:
      raise GetoptError("missing srctrees or dsttree")
    dstroot = argv.pop()
    srcroots = argv
    options = self.options
    fstags = options.fstags
    if update_mode:
      if not isdirpath(dstroot):
        raise GetoptError("dstroot does not exist: %s" % (dstroot,))
    else:
      with Pfx("mkdir(%r)", dstroot):
        os.mkdir(dstroot)
    for srcroot in srcroots:
      with Pfx(srcroot):
        for filepath in sorted(rfilepaths(srcroot)):
          with Pfx(filepath):
            tagged_path = fstags[filepath]
            subpath = plex_subpath(tagged_path)
            print(subpath, "<=", basename(filepath))

def plex_subpath(tagged_path):
  ''' Compute a Plex filesystem subpath based on the tags of `filepath`.
  '''
  base, ext = splitext(basename(tagged_path.filepath))
  title = tagged_path.series_title or tagged_path.title or base
  season = tagged_path.season and int(tagged_path.season)
  episode = tagged_path.episode and int(tagged_path.episode)
  episode_title = tagged_path.episode_title
  extra = tagged_path.extra and int(tagged_path.extra)
  part = tagged_path.part and int(tagged_path.part)
  is_tv_episode = bool(season and (episode or extra))
  dstbase = title
  if is_tv_episode:
    # TV Series
    dstpath = ['TV Shows', title, f'Season {season:02d}']
    if tagged_path.episode:
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
  if part:
    dstbase += f' - pt{part:d}'
  dstbase = dstbase.replace('/', '::')
  dstpath.append(dstbase)
  return joinpath(*dstpath) + ext

def linkpath(srcpath, dstroot, tags, update_mode=False):
  ''' Symlink `srcpath` to the approriate name under `dstroot` based on `tags`.
  '''
  dstbase = subpath(tags)
  _, srcext = splitext(basename(srcpath))
  linkpath = abspath(srcpath)
  dstpath = joinpath(dstroot, dstbase + srcext)
  with Pfx(dstpath):
    if existspath(dstpath):
      if update_mode:
        try:
          existing_link = os.readlink(dstpath)
        except OSError as e:
          raise ValueError("existing path is not a symlink: %s", e)
        else:
          if existing_link == linkpath:
            return dstpath
          else:
            warning("replace -> %s", linkpath)
            os.remove(dstpath)
    else:
      dstdir = dirname(dstpath)
      if not existspath(dstdir):
        with Pfx("makedirs(%r)", dstdir):
          os.makedirs(dstdir)
    with Pfx("symlink"):
      os.symlink(linkpath, dstpath)
  return dstpath

if __name__ == '__main__':
  sys.exit(main(sys.argv))
