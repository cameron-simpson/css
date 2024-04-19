#!/usr/bin/env python3
#

''' Stuff for Plex media libraries.
'''

from contextlib import contextmanager
from dataclasses import dataclass, field
from getopt import GetoptError
import os
from os.path import (
    abspath,
    basename,
    dirname,
    exists as existspath,
    expanduser,
    isdir as isdirpath,
    isfile as isfilepath,
    join as joinpath,
    normpath,
    splitext,
)
import sys
from typing import Optional, Sequence

from typeguard import typechecked

from cs.cmdutils import BaseCommand
from cs.fs import needdir, shortpath
from cs.fstags import FSTags, rfilepaths, uses_fstags
from cs.hashindex import merge, DEFAULT_HASHNAME
from cs.lex import get_prefix_n
from cs.logutils import warning
from cs.mediainfo import scrub_title
from cs.pfx import Pfx, pfx_call
from cs.resources import RunState, uses_runstate
from cs.upd import run_task, print

DISTINFO = {
    'keywords': ["python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
    ],
    'entry_points': {
        'console_scripts': {
            'plextool': 'cs.app.plex:main'
        },
    },
    'install_requires': [
        'cs.cmdutils',
        'cs.fstags',
        'cs.logutils',
        'typeguard',
    ],
}

PLEXTREE_DEFAULT = '~/var/plextree'
PLEXTREE_ENVVAR = 'PLEX_LINKTREE'
PLEXMODES = "movie,tv"  # "music"?

def main(argv=None):
  ''' Command line mode.
  '''
  return PlexCommand(argv).run()

class UnsupportedPlexModeError(ValueError):
  ''' Plex path does not match the active modes.
  '''
  pass

class PlexCommand(BaseCommand):
  ''' `plex` main command line class.
  '''

  GETOPT_SPEC = 'd:'
  USAGE_FORMAT = r'''Usage: {cmd} [-d plextree] subcommand ...
      -d plextree   Specify the Plex link tree location,
                    default from \${PLEXTREE_ENVVAR} or {PLEXTREE_DEFAULT}.
  '''
  USAGE_KEYWORDS = {
      'PLEXTREE_DEFAULT': PLEXTREE_DEFAULT,
      'PLEXTREE_ENVVAR': PLEXTREE_ENVVAR,
  }

  @dataclass
  class Options(BaseCommand.Options):
    ''' Options for `PlexCommand`.
    '''
    modes: str = PLEXMODES
    plextree: str = field(
        default_factory=lambda:
        (os.environ.get(PLEXTREE_ENVVAR, expanduser(PLEXTREE_DEFAULT)))
    )
    symlink_mode: bool = False

  def apply_opt(self, opt, val):
    ''' Apply an option.
    '''
    if opt == '-d':
      self.options.plextree = val
    else:
      super().apply_opt(opt, val)

  @contextmanager
  @uses_fstags
  def run_context(self, fstags: FSTags, **kw):
    ''' Use the FSTags context.
    '''
    with super().run_context(**kw):
      with fstags:
        yield

  @uses_runstate
  def cmd_linktree(self, argv, *, runstate: RunState):
    ''' Usage: {cmd} [-d plextree] [-n] [-m mode,...] [--sym] srctrees...
          Link media files from the srctrees into a Plex media tree.
          -d plextree Specify the Plex link tree location.
          -n          No action, dry run. Print the expected actions.
          -m modes    Allowed modes, comma separated list of \"movie\", \"tv\".
          --sym       Symlink mode: link media files using symbolic links
                      instead of hard links. The default is hard links
                      because that lets you bind mount the plex media tree,
                      which would make the symlinkpaths invalid in the
                      bound mount.
          -v          Verbose.
    '''
    options = self.options
    options.symlink_mode = False
    options.popopts(
        argv,
        d_='plextree',
        n='dry_run',
        m_='modes',
        sym='symlink_mode',
        v='verbose',
    )
    doit = options.doit
    modes = options.modes.split(',')
    plextree = options.plextree
    symlink_mode = options.symlink_mode
    verbose = options.verbose
    if not argv:
      raise GetoptError("missing srctrees")
    srcroots = argv
    if not isdirpath(plextree):
      raise GetoptError(f'plextree does not exist: {plextree!r}')
    with run_task('linktree') as proxy:
      seen = set()
      for srcroot in srcroots:
        runstate.raiseif()
        osrcdir = None
        for srcpath in srcroot if isfilepath(srcroot) else sorted(
            (joinpath(srcroot, normpath(subpath))
             for subpath in rfilepaths(srcroot))):
          runstate.raiseif()
          with Pfx(srcpath):
            srcdir = dirname(srcpath)
            if srcdir != osrcdir:
              ##print(srcdir)
              proxy.text = shortpath(srcdir)
              osrcdir = srcdir
            _, ext = splitext(basename(srcpath))
            if ext.lower() not in ('.mp4', '.mkv', '.avi'):
              verbose and warning("unsupported extension: %s", ext)
              continue
            try:
              os.stat(srcpath)
            except FileNotFoundError as e:
              warning("%s", e)
              continue
            try:
              plex_linkpath(
                  srcpath,
                  plextree,
                  modes=modes,
                  symlink_mode=symlink_mode,
                  doit=doit,
                  quiet=False,
                  seen=seen,
              )
            except UnsupportedPlexModeError as e:
              verbose and warning("skipping, unsupported plex mode: %s", e)
              continue
            except ValueError as e:
              warning("skipping: %s", e)
              continue
            except OSError as e:
              warning("failed: %s", e)

@uses_fstags
@typechecked
def plex_subpath(
    fspath: str, *, modes: Optional[Sequence[str]] = None, fstags: FSTags
):
  ''' Compute a Plex filesystem subpath based on the tags of `fspath`.

      See: https://support.plex.tv/articles/naming-and-organizing-your-tv-show-files/
  '''
  if modes is None:
    modes = "movie", "tv"
  assert tuple(modes) == ("tv",), "expected just [tv], got %r" % (modes,)
  filename = basename(fspath)
  # infer from filename?
  base, ext = splitext(filename)
  itags = fstags[fspath].infer_tags()
  t = itags.auto
  tv = t.tv
  title = tv.series_title or t.title or base
  season = tv.season and int(tv.season)
  episode = isinstance(tv.episode, (int, str)) and int(tv.episode)
  episode_title = tv.episode_title or ''
  episode_title = scrub_title(episode_title, season=season, episode=episode)
  extra = isinstance(tv.extra, (int, str)) and int(tv.extra)
  extra_title = tv.extra_title
  part = tv.part and int(tv.part)
  # compose the filesystem path
  dstpath = []
  if tv.series_title and season and episode:
    # TV Series
    if "tv" not in modes:
      raise UnsupportedPlexModeError("tv not in modes %r" % (modes,))
    dstpath.append('TV Shows')
    series_part = tv.series_title
    if tv.series_year:
      series_part = f'{series_part} ({tv.series_year})'
    dstpath.append(series_part)
    dstpath.append(f'Season {season:02d}')
    dstfilename = series_part
    if episode:
      dstfilename += f' - s{season:02d}e{episode:02d}'
    else:
      dstfilename += f' - s{season:02d}x{extra:02d}'
  else:
    # Movie
    if "movie" not in modes:
      raise UnsupportedPlexModeError("movie not in modes %r" % (modes,))
    dstpath.append('Movies')
    dstfilename = title
    if episode:
      dstfilename += f' - {episode:d}'
  if episode_title and episode_title != title:
    dstfilename += f' - {episode_title}'
  elif extra_title and extra_title != title:
    dstfilename += f' - {extra_title}'
  if part:
    dstfilename += f' - pt{part:d}'
  dstpath.append(dstfilename + ext)
  subpath = joinpath(*(part.replace('/', '::') for part in dstpath))
  return subpath

# pylint: disable=redefined-builtin
def plex_linkpath(
    srcpath: str,
    plex_topdirpath,
    *,
    modes: Optional[Sequence[str]] = None,
    doit=True,
    quiet=False,
    hashname=DEFAULT_HASHNAME,
    symlink_mode=True,
    seen=None,
):
  ''' Symlink `srcpath` into `plex_topdirpath`.

      Parameters:
      * `srcpath`: filesystem path of the file to link into Plex tree
      * `plex_topdirpath`: filesystem pathname of the Plex tree
      * `symlink_mode`: if true (default) make a symbolic link,
        otherwise a hard link
      * `doit`: default `True`: if false do not make the link
      * `quiet`: default `False`; if false print the planned link
      * `hashname`: the file content hash algorithm name
  '''
  if seen is None:
    seet = set()
  subpath = plex_subpath(srcpath, modes=modes)
  if subpath in seen:
    quiet or warning(
        "skipping %r -> %r, we already set it up", srcpath, subpath
    )
  seen.add(subpath)
  plexpath = joinpath(plex_topdirpath, subpath)
  with Pfx(plexpath):
    if doit and not existspath(plexpath):
      needdir(dirname(plexpath), use_makedirs=True, log=warning)
    try:
      merge(
          abspath(srcpath),
          plexpath,
          hashname=hashname,
          symlink_mode=symlink_mode,
          quiet=False,
          doit=doit
      )
    except FileExistsError:
      warning("already exists")

if __name__ == '__main__':
  sys.exit(main(sys.argv))
