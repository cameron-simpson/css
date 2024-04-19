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
from cs.hashindex import merge, HASHNAME_DEFAULT
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
    options.modes = "tv"
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
    if not doit:
      options.verbose = True
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
        for _, srcpath in scandirtree(
            srcroot,
            sort_names=True,
            only_suffixes=('mp4', 'mkv'),
        ):
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
@ensure(
    lambda result:
    all(is_valid_rpath(subpath) for subpath in result[1].keys())
)
@typechecked
def plex_subpath(
    fspath: str,
    *,
    modes: Optional[Sequence[str]] = None,
    fstags: FSTags
) -> Tuple[str, dict]:
  ''' Compute a Plex filesystem subpath based on the tags of `fspath`.
      Return a 2-tuple of `(subpath,plexmatches)` containing the
      Plex filesystem subpath and a `dict` with any entries for
      `.plexmatch` files along the path of the form
      `subdirpath`->`hint`->`value`.

      See: https://support.plex.tv/articles/naming-and-organizing-your-tv-show-files/
      and: https://support.plex.tv/articles/plexmatch/
  '''
  plexmatches = defaultdict(dict)
  if modes is None:
    modes = "movie", "tv"
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

  def scrub(part):
    ''' Clean up a path component.
    '''
    return part.replace('/', '::').strip()

  # compose the filesystem path
  if tv.series_title and season and episode:
    # TV Series
    mode = 'tv'
    if mode not in modes:
      raise UnsupportedPlexModeError(f'{mode!r} not in modes {modes!r}')
    toppath = 'TV Shows'
    series_title = tv.series_title
    series_part = series_title
    if tv.series_year:
      series_part = f'{series_part} ({tv.series_year})'
    series_path = joinpath(toppath, scrub(series_part))
    plexmatches[series_path]['show'] = series_title
    if tv.series_year:
      plexmatches[series_path]['year'] = tv.series_year
    season_part = f'Season {season:02d}'
    season_path = joinpath(series_path, scrub(season_part))
    plexmatches[season_path]['season'] = season
    dstfilename = series_part
    if episode:
      dstfilename += f' - s{season:02d}e{episode:02d}'
    else:
      dstfilename += f' - s{season:02d}x{extra:02d}'
    subdirpath = season_path
  else:
    # Movie
    mode = 'movie'
    if mode not in modes:
      raise UnsupportedPlexModeError(f'{mode!r} not in modes {modes!r}')
    toppath = 'Movies'
    dstfilename = title
    if episode:
      dstfilename += f' - {episode:d}'
    subdirpath = toppath
  if episode_title and episode_title != title:
    dstfilename += f' - {episode_title}'
  elif extra_title and extra_title != title:
    dstfilename += f' - {extra_title}'
  if part:
    dstfilename += f' - pt{part:d}'
  subpath = joinpath(subdirpath, scrub(dstfilename) + ext)
  return subpath, plexmatches

# pylint: disable=redefined-builtin
def plex_linkpath(
    srcpath: str,
    plex_topdirpath,
    *,
    modes: Optional[Sequence[str]] = None,
    doit=True,
    quiet=False,
    hashname=HASHNAME_DEFAULT,
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
  subpath = plex_subpath(srcpath, modes=modes)
    seen = set()
  subpath, plexmatches = plex_subpath(srcpath, modes=modes)
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
    else:
      # make .plexmatch files
      for subdirpath, matches in sorted(plexmatches.items()):
        assert subpath.startswith(subdirpath + '/')
        if not matches:
          warning("no plex matches for %r?", subdirpath)
          continue
        plexmatchpath = joinpath(plex_topdirpath, subdirpath, '.plexmatch')
        match_hints = read_matchfile(plexmatchpath)
        if not quiet:
          for hint, value in sorted(matches.items()):
            if match_hints.get(hint) == value:
              del match_hints[hint]
            else:
              print(shortpath(plexmatchpath), '+', f'{hint}:', value)
        if doit:
          if match_hints:
            with pfx_call(open, plexmatchpath, "a") as pmf:
              for hint, value in sorted(matches.items()):
                print(f'{hint}:', value, file=pmf)

def read_matchfile(fspath) -> dict:
  match_hints = {}
  try:
    with pfx_call(open, fspath, "r") as f:
      for lineno, line in enumerate(f, 1):
        with Pfx("%s:%d", fspath, lineno):
          line = line.strip()
          if not line or line.startswith('#'):
            continue
          try:
            hint, value = line.split(':', 1)
          except ValueError as e:
            warning("bad syntax")
          else:
            value = value.strip()
            if hint in ('season', 'year'):
              try:
                value = int(value)
              except ValueError as e:
                warning("%s: expected an int, got: %r", hint, value)
            match_hints[hint] = value
  except FileNotFoundError:
    pass
  return match_hints

if __name__ == '__main__':
  sys.exit(main(sys.argv))
