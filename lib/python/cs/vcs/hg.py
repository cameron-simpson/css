#!/usr/bin/env python3

''' Mercurial support for the cs.vcs package.
'''

from contextlib import contextmanager
from functools import cached_property
import io
import os
from types import SimpleNamespace as NS

from cs.buffer import CornuCopyBuffer
from cs.cache import cachedmethod
from cs.fs import HasFSPath
from cs.pfx import Pfx, pfx_method

from . import VCS, ReleaseLogEntry

class VCS_Hg(HasFSPath, VCS):
  ''' Mercurial implementation of `cs.vcs.VCS`.
  '''

  COMMAND_NAME = 'hg'

  TOPDIR_MARKER_ENTRY = '.hg'

  def __init__(self, fspath='.'):
    super().__init__(fspath)

  @cached_property
  def ui(self):
    from mercurial.ui import ui
    return ui()

  @cached_property
  def repo(self):
    from mercurial.hg import LocalFactory
    repopath = self.fspath
    repo = LocalFactory.instance(
        self.ui,
        os.fsencode(repopath) if isinstance(repopath, str) else repopath,
        create=False,
    )
    return repo

  @contextmanager
  def _pipefrom(self, vcscmd, *vcscmd_args, **hgcmd_options):
    ''' Context manager yielding the output of a Mercurial command.
    '''
    import mercurial.commands
    cmdfunc = getattr(mercurial.commands, vcscmd)

    u = self.ui
    u.pushbuffer()
    try:
      cmdfunc(
          u,
          self.repo,
          *(arg.encode('utf-8') for arg in vcscmd_args),
          **{
              opt: value.encode('utf-8') if isinstance(value, str) else value
              for opt, value in hgcmd_options.items()
          },
      )
    finally:
      output = u.popbuffer()
    yield CornuCopyBuffer([output])

  def _linesfrom(self, vcscmd, *vcscmd_args, **hgcmd_options):
    ''' A generator yielding `str` lines from the output of a Mercurial command.
    '''
    with self._pipefrom(vcscmd, *vcscmd_args, **hgcmd_options) as f:
      while True:
        bline = f.readline()
        if not bline:
          break
        yield bline.decode('utf-8')

  def hg_cmd(self, hgcmd, *argv, **hgcmd_options):
    ''' Make sure external users know they're calling a backend
        specific command line.
    '''
    import mercurial.commands
    cmdfunc = getattr(mercurial.commands, hgcmd)
    u = self.ui
    return cmdfunc(
        u,
        self.repo,
        *(arg.encode('utf-8') for arg in argv),
        **{
            opt: value.encode('utf-8') if isinstance(value, str) else value
            for opt, value in hgcmd_options.items()
        },
    )

  def hg_archive(
      self,
      dirpath,
      vcs_revision,
      paths,
  ):
    return self.hg_cmd(
        'archive',
        dirpath,
        rev=vcs_revision,
        prefix=b'',
        exclude=[],
        include=[path.encode('utf-8') for path in paths],
    )

  def resolve_revision(self, rev_spec):
    ''' Resolve a revision specification to the commit hash (a `str`).
    '''
    raise RuntimeError
    with self._pipefrom('-i', '-r', rev_spec) as f:
      rev_hash = f.readline().rstrip()
    return rev_hash

  @cachedmethod
  def tags(self):
    ''' Return the list of tags.
    '''
    return [line.split(None, 1)[0] for line in self._linesfrom('tags')]

  def tag(self, tag_name, revision=None, message=None):
    ''' Tag a revision with the supplied `tag`, by default revision "tip".
    '''
    if revision is None:
      revision = 'tip'
    self.hg_cmd('tag', tag_name, message=message, rev=revision)

  def logs(self, paths, **logcmd_options):
    ''' Generator yielding lines from an "hg log" incantation
        with trailing `\r` and `\n` stripped.
    '''
    for line in self._linesfrom('log', *paths, **logcmd_options):
      yield line.rstrip('\r\n')

  def log_since(self, tag, paths):
    ''' Generator yielding `(commit_files,commit_firstline)`
        for commit log entries since `tag`
        involving `paths` (a list of `str`).
    '''
    for lineno, line in enumerate(self.logs(
        paths,
        rev=[f'{tag!r}::tip'.encode('utf-8')],
        template='{files}\t{desc|firstline}\n',
    ), 1):
      with Pfx("line %d", lineno):
        files, firstline = line.split('\t', 1)
        files = files.split()
        firstline = firstline.strip()
      yield files, firstline

  @pfx_method
  def file_revisions(self, paths):
    ''' Return a mapping of `path->(rev,node)`
        containing the latest revision of each file in `paths`.

        The `rev` is the sequential revision number
        and the `node` if the changeset identification hash.
        This pair supports choosing the latest hash from some files.
    '''
    path_map = {}
    for path in paths:
      for line in self.logs([path], limit='1', template='{rev} {node}\n'):
        rev, node = line.split()
        break
      else:
        continue
      path_map[path] = (None if rev is None else int(rev)), node
    return path_map

  def add_files(self, *paths):
    ''' Add the specified paths to the repository.
    '''
    self.hg_cmd('add', *paths)

  def commit(self, message, *paths):
    ''' Commit the specified `paths` with the specified `message`.
    '''
    if not paths:
      raise ValueError("no paths supplied for commit")
    self.hg_cmd('commit', *paths, message=message)

  @cachedmethod
  def uncommitted(self, paths=None):
    ''' Return a list of the uncommited but tracked paths.
    '''
    status_argv = ['status']
    if paths:
      status_argv.extend(paths)
    paths = []
    for line in self._linesfrom(*status_argv):
      line = line.rstrip()
      if not line:
        continue
      s, path = line.split(' ', 1)
      if s != '?':
        paths.append(path)
    return paths


  def log_entries(self, *revs):
    ''' Return the log entry for the specified revision `rev`.
    '''
    with self._pipefrom(
        'log',
        rev=",".join(revs),
        template='{node}\n{tags}\n{desc}\a',
    ) as piped:
      for entry_s in piped.read().decode('utf-8').split('\a'):
        if not entry_s:
          continue
        node, tags, desc = entry_s.split('\n', 2)
        yield NS(node=node, tags=tags.split(), desc=desc)

  def release_log(self, tag_prefix):
    ''' Generator yielding `ReleaseLogEntry` instances
        for the release tags starting with `tag_prefix`
        in reverse tag order (most recent first).
    '''
    release_tags = sorted(
        filter(lambda tag: tag.startswith(tag_prefix), self.tags()),
        reverse=True
    )
    for log in self.log_entries(*release_tags):
      logtags = {
          logtag
          for logtag in log.tags
          if logtag.startswith(tag_prefix)
      }
      for tag in logtags:
        yield ReleaseLogEntry(tag, log.desc)
