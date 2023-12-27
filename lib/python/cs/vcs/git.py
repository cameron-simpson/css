#!/usr/bin/env python3

from os.path import exists as existspath, join as joinpath, realpath
from cs.fileutils import findup
from cs.psutils import pipefrom
from . import VCS

from cs.x import X
X("__file__ = %r", __file__)

class VCS_Git(VCS):

  COMMAND_NAME = 'git'

  TOPDIR_MARKER_ENTRY = '.git'

  def resolve_revision(self, rev_spec):
    ''' Resolve a revision specification to the commit hash (a `str`).
    '''
    with self._pipefrom('rev-parse', rev_spec) as f:
      rev_hash = f.readline().rstrip()
    return rev_hash

  def tags(self):
    ''' Generator yielding the current tags.
    '''
    with self._pipefrom('tag') as f:
      yield from map(lambda line: line.rstrip(), f)

  def tag(self, tag_name, revision=None):
    ''' Tag a revision with the supplied `tag`, by default revision "HEAD".
    '''
    if revision is None:
      revision = 'HEAD'
    self._cmd('tag', tag_name, revision)

  def log_since(self, tag, paths):
    with self._pipefrom('log', '-r', tag + ':', '--template',
                        '{files}\t{desc|firstline}\n', '--', *paths) as f:
      for hgline in f:
        files, firstline = hgline.split('\t', 1)
        files = files.split()
        firstline = firstline.strip()
        yield files, firstline

  def add_files(self, *paths):
    ''' Add the specified paths to the repository.
    '''
    self._cmd('add', *paths)

  def commit(self, message, *paths):
    ''' Commit the specified `paths` with the specified `message`.
    '''
    self._cmd('commit', '-m', message, '--', *paths)

  def uncommitted(self):
    ''' Generator yielding uncommited but tracked paths.
    '''
    with self._pipefrom('status') as f:
      for hgline in f:
        s, path = hgline.rstrip().split(' ', 1)
        if s != '?':
          yield path

  def hg_include(self, paths):
    ''' Generator yielding hg(1) -I/-X option strings to include the `paths`.
    '''
    for subpath in self.paths():
      yield '-I'
      yield 'path:' + subpath

  def log_entry(self, rev):
    ''' Return the log entry for the specified revision `rev`.
    '''
    with self._pipefrom('log', '-r', rev, '--template', '{desc}\n',
                        '--') as piped:
      return ''.join(piped)

  def release_log(self, package_name):
    ''' Generator yeilding `(tag,log_entry)` for the release tags
        of `package_name` in reverse tag order (most recent first).
    '''
    tag_prefix = package_name + '-'
    for tag in sorted(
        [tag for tag in self.tags() if tag.startswith(tag_prefix)],
        reverse=True):
      yield tag, self.log_entry(tag)
