#!/usr/bin/env python3

''' Mercurial support for the cs.vcs package.
'''

from cs.deco import cachedmethod
from cs.pfx import Pfx
from . import VCS, ReleaseLogEntry

class VCS_Hg(VCS):
  ''' Mercurial implementation of cs.vcs.VCS.
  '''

  COMMAND_NAME = 'hg'

  TOPDIR_MARKER_ENTRY = '.hg'

  def hg_cmd(self, *argv):
    ''' Make sure external users know they're calling a backend
        specific command line.
    '''
    return self._cmd(*argv)

  def resolve_revision(self, rev_spec):
    ''' Resolve a revision specification to the commit hash (a `str`).
    '''
    with self._pipefrom('-i', '-r', rev_spec) as f:
      rev_hash = f.readline().rstrip()
    return rev_hash

  @cachedmethod
  def tags(self):
    ''' Return the list of tags.
    '''
    with self._pipefrom('tags') as f:
      return list(map(lambda line: line.split(None, 1)[0], f))

  def tag(self, tag_name, revision=None, message=None):
    ''' Tag a revision with the supplied `tag`, by default revision "tip".
    '''
    if revision is None:
      revision = 'tip'
    args = ['tag', '-r', revision]
    if message is not None:
      args.extend(['-m', message])
    args.extend(['--', tag_name])
    self.hg_cmd(*args)

  def log_since(self, tag, paths):
    ''' Generator yielding `(commit_files,commit_firstline)`
        for commit log entries since `tag`
        involving `paths` (a list of `str`).
    '''
    with self._pipefrom('log', '-r', tag + ':', '--template',
                        '{files}\t{desc|firstline}\n', '--', *paths) as f:
      for lineno, line in enumerate(f, 1):
        with Pfx("line %d", lineno):
          files, firstline = line.split('\t', 1)
          files = files.split()
          firstline = firstline.strip()
        yield files, firstline

  def add_files(self, *paths):
    ''' Add the specified paths to the repository.
    '''
    self.hg_cmd('add', *paths)

  def commit(self, message, *paths):
    ''' Commit the specified `paths` with the specified `message`.
    '''
    if not paths:
      raise ValueError("no paths supplied for commit")
    self.hg_cmd('commit', '-m', message, '--', *paths)

  @cachedmethod
  def uncommitted(self, paths=None):
    ''' Return a list of the uncommited but tracked paths.
    '''
    status_argv = ['status']
    if paths:
      status_argv.extend(paths)
    paths = []
    with self._pipefrom(*status_argv) as f:
      for line in f:
        s, path = line.rstrip().split(' ', 1)
        if s != '?':
          paths.append(path)
    return paths

  @staticmethod
  def hg_include(paths):
    ''' Generator yielding hg(1) -I/-X option strings to include the `paths`.
    '''
    for subpath in paths:
      yield '-I'
      yield 'path:' + subpath

  def log_entry(self, rev):
    ''' Return the log entry for the specified revision `rev`.
    '''
    with self._pipefrom('log', '-r', rev, '--template', '{desc}\n',
                        '--') as piped:
      return ''.join(piped)

  def release_log(self, tag_prefix):
    ''' Generator yielding `ReleaseLogEntry` instances
        for the release tags starting with `tag_prefix`
        in reverse tag order (most recent first).
    '''
    for tag in sorted(filter(lambda tag: tag.startswith(tag_prefix),
                             self.tags()), reverse=True):
      yield ReleaseLogEntry(tag, self.log_entry(tag))
