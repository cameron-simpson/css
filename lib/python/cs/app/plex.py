#!/usr/bin/env python3
#

''' Stuff for Plex media libraries.
'''

from getopt import GetoptError
import os
from os.path import (
    abspath, exists as existspath, basename, dirname, join as joinpath,
    splitext
)
import sys
from cs.cmdutils import BaseCommand
from cs.fstags import FSTags, loadrc as fstags_loadrc, rfilepaths, TaggedPath
from cs.logutils import setup_logging, Pfx, warning, error

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
  return PlexCommand().run(argv)

class PlexCommand(BaseCommand):
  ''' `plex` main command line class.
  '''

  GETOPT_SPEC = ''
  USAGE_FORMAT = '''Usage:
    {cmd} linktree srctrees... dsttree
        Link media files from the srctrees into the dsttree
        using the Plex naming conventions.
  '''

  def apply_defaults(self, options):
    ''' Set up the default values in `options`.
    '''
    setup_logging(options.cmd)
    options.fstags = FSTags()

  @staticmethod
  def cmd_linktree(argv, options, *, cmd):
    ''' Produce a symlink tree for Plex from source trees.
    '''
    if len(argv) < 2:
      raise GetoptError("missing srctrees or dsttree")
    dstroot = argv.pop()
    srcroots = argv
    fstags = options.fstags
    rules = fstags_loadrc()
    with Pfx("mkdir(%r)", dstroot):
      os.mkdir(dstroot)
    for srcroot in srcroots:
      with Pfx(srcroot):
        for filepath in sorted(rfilepaths(srcroot)):
          with Pfx(filepath):
            tagged_path = TaggedPath(filepath, fstags=fstags)
            tagged_path.autotag(rules=rules, no_save=True)
            tags = tagged_path.merged_tags()
            linkpath(filepath, dstroot, tags)

PlexCommand.add_usage_to_docstring()

def linkpath(srcpath, dstroot, tags):
  ''' Symlink `srcpath` to the appropriate place in `dstroot` based on `tags`.
  '''
  _, srcext = splitext(basename(srcpath))
  dstpath = [dstroot]
  dstbase = tags.title
  if dstbase is None:
    warning("no title")
    return None
  season = tags.get('season')
  episode = tags.get('episode')
  episode_title = tags.episode_title
  extra = tags.get('extra')
  part = tags.get('part')
  is_tv_episode = bool(season and (episode or extra))
  if is_tv_episode:
    # TV Series
    dstpath.extend(('TV Shows', dstbase, 'Season %02d' % (season,)))
    if episode:
      dstbase += ' - s%02de%02d' % (season, episode)
    else:
      dstbase += ' - s%02dx%02d' % (season, extra)
  else:
    # Movie
    dstpath.append('Movies')
    if episode:
      dstbase += ' - %d' % (episode,)
  if episode_title:
    dstbase += ' - %s' % (episode_title,)
  if part:
    dstbase += ' - pt%02d' % (part,)
  dstbase += srcext
  dstbase = dstbase.replace('/', '::')
  dstpath.append(dstbase)
  dstpath = joinpath(*dstpath)
  with Pfx(dstpath):
    if existspath(dstpath):
      error("already exists")
      return None
    dstdir = dirname(dstpath)
    if not existspath(dstdir):
      with Pfx("makedirs(%r)", dstdir):
        os.makedirs(dstdir)
    with Pfx("symlink"):
      os.symlink(abspath(srcpath), dstpath)
  return dstpath

if __name__ == '__main__':
  sys.exit(main(sys.argv))
