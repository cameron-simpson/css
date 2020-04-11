#!/usr/bin/env python3

from os.path import exists as existspath, join as joinpath, realpath
from cs.fileutils import findup
from cs.psutils import pipefrom
from . import VCS

class VCS_Hg(VCS):

  def get_topdir(self, path=None):
    ''' Locate the top of the repository from `path` (default `'.'`).
        Return the directory containing the `'.hg'` subdirectory or `None`.
    '''
    if path is None:
      path = '.'
    path = realpath(path)
    return next(
        findup(
            path,
            lambda testpath: pathexists(joinpath(testpath, '.hg')),
            first=True
        )
    )

  def _pipefrom(self, *hgargs):
    hgargv = ['hg'] + list(hgargs) + ['|']
    return pipefrom('hg', *hgargs)

  def _hgcmd(self, *hgargs):
    print('hg', *hgargs, file=sys.stderr)
    check_call(['hg'] + list(hgargs))

  def tags(self):
    ''' Generator yielding the current tags.
    '''
    with self._pipefrom('tags') as hgfp:
      tags = set()
      for tagline in hgfp:
        tag, _ = tagline.split(None, 1)
        yield tag

  def tag(self, tag_name, revision=None):
    ''' Tag a revision with the supplied `tag`, by default revision "tip".
    '''
    if revision is None:
      revision = 'tip'
    self._hgcmd('tag', '-r', revision, '--', tag_name)

  def log_since(self, tag, paths):
    with self._pipefrom('log', '-r', tag + ':', '--template',
                        '{files}\t{desc|firstline}\n', '--', *paths) as hgfp:
      for hgline in hgfp:
        files, firstline = hgline.split('\t', 1)
        files = files.split()
        firstline = firstline.strip()
        yield files, firstline

  def add_files(self, *paths):
    ''' Add the specified paths to the repository.
    '''
    self._hgcmd('add', *paths)

  def commit(self, message, *paths):
    ''' Commit the specified `paths` with the specified `message`.
    '''
    self._hgcmd('commit', '-m', message, '--', *paths)

  def uncommitted(self):
    ''' Generator yielding uncommited but tracked paths.
    '''
    with self._pipefrom('status') as hgfp:
      for hgline in hgfp:
        s, path = hgline.rstrip().split(' ', 1)
        if s != '?':
          yield path
