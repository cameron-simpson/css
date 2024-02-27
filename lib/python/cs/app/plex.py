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
    expanduser,
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
      return super().apply_opt(opt, val)

  @contextmanager
  @uses_fstags
  def run_context(self, fstags: FSTags, **kw):
    ''' Use the FSTags context.
    '''
    with super().run_context(**kw):
      with fstags:
        yield

  def cmd_linktree(self, argv):
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
    '''
    options = self.options
    options.symlink_mode = False
    options.popopts(
        argv,
        d='plextree',
        n='dry_run',
        m_='modes',
        sym='symlink_mode',
    )
    doit = options.doit
    modes = options.modes.split(',')
    plextree = options.plextree
    symlink_mode = options.symlink_mode
    runstate = options.runstate
    if not argv:
      raise GetoptError("missing srctrees")
    srcroots = argv
    if not isdirpath(plextree):
      raise GetoptError(f'plextree does not exist: {plextree!r}')
    for srcroot in srcroots:
      runstate.raiseif()
      for srcpath in srcroot if isfilepath(srcroot) else sorted(
          (joinpath(srcroot, normpath(subpath))
           for subpath in rfilepaths(srcroot))):
        runstate.raiseif()
        with Pfx(srcpath):
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
            )
          except UnsupportedPlexModeError as e:
            warning("skipping, unsupported plex mode: %s", e)
            continue
          except ValueError as e:
            warning("skipping: %s", e)
            continue
          except OSError as e:
            warning("failed: %s", e)

def scrub_title(title: str, *, season=None, episode=None):
  ''' Strip redundant text from the start of an episode title.
  '''
  title = title.strip()
  if season:
    spfx, n, offset = get_prefix_n(title, 's', n=season)
    if spfx:
      assert title.startswith(f's{season:02d}')
      title = title[offset:]
  if episode:
    epfx, n, offset = get_prefix_n(title, 'e', n=episode)
    if spfx:
      assert title.startswith(f'e{episode:02d}')
      title = title[offset:]
  title = title.lstrip(' -')
  if episode:
    epfx, n, offset = get_prefix_n(title.lower(), 'episode ', n=episode)
    if spfx:
      title = title[offset:]
    title = title.lstrip(' -')
  return title

@uses_fstags
@typechecked
def plex_subpath(
    fspath: str, *, modes: Optional[Sequence[str]] = None, fstags: FSTags
):
  ''' Compute a Plex filesystem subpath based on the tags of `fspath`.
  '''
  if modes is None:
    modes = "movie", "tv"
  assert tuple(modes) == ("tv",), "expected just [tv], got %r" % (modes,)
  base, ext = splitext(basename(fspath))
  itags = fstags[fspath].infer_tags()
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
  if tv.series_title and season and episode:
    # TV Series
    if "tv" not in modes:
      raise UnsupportedPlexModeError("tv not in modes %r" % (modes,))
    dstpath = ['TV Shows', tv.series_title, f'Season {season:02d}']
    if episode:
      dstbase += f' - s{season:02d}e{episode:02d}'
    else:
      dstbase += f' - s{season:02d}x{extra:02d}'
  else:
    # Movie
    if "movie" not in modes:
      raise UnsupportedPlexModeError("movie not in modes %r" % (modes,))
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
    srcpath: str,
    plex_topdirpath,
    *,
    modes: Optional[Sequence[str]] = None,
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
  subpath = plex_subpath(srcpath, modes=modes)
  plexpath = joinpath(plex_topdirpath, subpath)
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
