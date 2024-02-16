#!/usr/bin/env python3
#

''' Stuff for Plex media libraries.
'''

import builtins
from contextlib import contextmanager
from dataclasses import dataclass, field
from getopt import GetoptError
import os
from os.path import (
    abspath,
    basename,
    dirname,
    exists as existspath,
    isdir as isdirpath,
    isfile as isfilepath,
    join as joinpath,
    relpath,
    samefile,
    splitext,
)
import sys

from typeguard import typechecked

from cs.cmdutils import BaseCommand
from cs.fs import needdir
from cs.fstags import FSTags, rfilepaths, uses_fstags
from cs.hashindex import merge, DEFAULT_HASHNAME
from cs.logutils import warning
from cs.pfx import Pfx, pfx_call

DISTINFO = {
    'keywords': ["python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
    ],
    'entry_points': {
        'console_scripts': {
            'plex': 'cs.app.plex:main'
        },
    },
    'install_requires': [
        'cs.cmdutils',
        'cs.fstags',
        'cs.logutils',
        'typeguard',
    ],
}

def main(argv=None):
  ''' Command line mode.
  '''
  return PlexCommand(argv).run()

class PlexCommand(BaseCommand):
  ''' `plex` main command line class.
  '''

  GETOPT_SPEC = ''

  @dataclass
  class Options(BaseCommand.Options):
    ''' Options for `PlexCommand`.
    '''
    fstags: FSTags = field(default_factory=FSTags)

  @contextmanager
  def run_context(self, **kw):
    ''' Use the FSTags context.
    '''
    with super().run_context(**kw):
      with self.options.fstags:
        yield

  def cmd_linktree(self, argv):
    ''' Usage: {cmd} [-n] srctrees... dsttree
          Link media files from the srctrees into a Plex media tree.
          -n  No action, dry run. Print the expected actions.
    '''
    options = self.options
    options.popopts(
        argv,
        n='dry_run',
    )
    doit = options.doit
    runstate = options.runstate
    if len(argv) < 2:
      raise GetoptError("missing srctrees or dsttree")
    dstroot = argv.pop()
    srcroots = argv
    if not isdirpath(dstroot):
      raise GetoptError("dstroot does not exist: %s" % (dstroot,))
    for srcroot in srcroots:
      runstate.raiseif()
      with Pfx(srcroot):
        for srcpath in srcroot if isfilepath(srcroot) else sorted(
            rfilepaths(srcroot)):
          runstate.raiseif()
          with Pfx(srcpath):
            try:
              plex_linkpath(
                  srcpath, dstroot, symlink_mode=True, doit=doit, quiet=False
              )
            except ValueError as e:
              warning("skipping: %s", e)
              continue

@uses_fstags
@typechecked
def plex_subpath(fspath: str, fstags: FSTags):
  ''' Compute a Plex filesystem subpath based on the tags of `filepath`.
  '''
  base, ext = splitext(basename(fspath))
  itags = fstags[fspath].infer_tags()
  t = itags.auto
  tv = t.tv
  title = tv.series_title or t.title or base
  season = tv.season and int(tv.season)
  episode = tv.episode and int(tv.episode)
  episode_title = tv.episode_title
  extra = tv.extra and int(tv.extra)
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
  if part:
    dstbase += f' - pt{part:d}'
  dstbase = dstbase.replace('/', '::')
  dstpath.append(dstbase)
  return joinpath(*dstpath) + ext

# pylint: disable=redefined-builtin
def plex_linkpath(
    srcpath: str,
    plex_topdirpath,
    *,
    doit=True,
    quiet=False,
    hashname=DEFAULT_HASHNAME,
    symlink_mode=True
):
  ''' Symlink `filepath` into `plex_topdirpath`.

      Parameters:
      * `srcpath`: filesystem pathname of file to link into Plex tree
      * `plex_topdirpath`: filesystem pathname of the Plex tree
      * `symlink_mode`: if true (default) make a symbolic link,
        otherwise a hard link
      * `doit`: default `True`: if false do not make the link
      * `quiet`: default `False`; if false print the planned link
      * `hashname`: the file content hash algorithm name
  '''
  subpath = plex_subpath(srcpath)
  plexpath = joinpath(plex_topdirpath, subpath)
  if doit and not existspath(plexpath):
    needdir(dirname(plexpath), use_makedirs=True, log=warning)
  merge(
      abspath(srcpath),
      plexpath,
      hashname=hashname,
      symlink_mode=symlink_mode,
      quiet=False,
      doit=doit
  )

if __name__ == '__main__':
  sys.exit(main(sys.argv))
