#!/usr/bin/env python3

''' Simplistic version control system (VCS) support.

    Initially support for Mercurial (`hg`) and Git,
    all done with callouts to the command line tools.
'''

from abc import ABC, abstractmethod
from collections import namedtuple
from contextlib import contextmanager
from subprocess import check_call
import sys
from os.path import exists as existspath, join as joinpath, realpath
from cs.fileutils import findup
from cs.logutils import debug, trace, warning
from cs.pfx import pfx_method
from cs.psutils import pipefrom

ReleaseLogEntry = namedtuple('ReleaseLogEntry', 'tag entry')

@contextmanager
def pipef(*argv, **kw):
  ''' Context manager returning the standard output of a command.
  '''
  trace("+ %r |", argv)
  P = pipefrom(argv, **kw)
  yield P.stdout
  if P.wait() != 0:
    pipecmd = ' '.join(argv)
    raise ValueError("%s: exit status %d" % (
        pipecmd,
        P.returncode,
    ))

class VCS(ABC):
  ''' Abstract base class for version control system implementations.
  '''

  # this needs definition by subclasses, eg 'hg' or 'git'
  COMMAND_NAME = None

  # this needs definition by subclasses, eg '.hg' or '.git'
  TOPDIR_MARKER_ENTRY = None

  def _pipefrom(self, *vcscmd_args):
    ''' Context manager return the stdout of a VCS command.
    '''
    return pipef(self.COMMAND_NAME, *vcscmd_args)

  def _cmd(self, *vcscmd_args):
    argv = [self.COMMAND_NAME] + list(vcscmd_args)
    trace("+ %r", argv)
    check_call(argv)

  @pfx_method
  def get_topdir(self, path=None):
    ''' Locate the top of the repository from `path` (default `'.'`).
        Return the directory realpath or `None`.
    '''

    def testfunc(testpath):
      probe_path = joinpath(testpath, self.TOPDIR_MARKER_ENTRY)
      debug("probe %r", probe_path)
      return existspath(probe_path)

    path0 = path
    if path is None:
      path = '.'
    path = realpath(path)
    topdirpath = next(findup(path, testfunc, first=True))
    if topdirpath is None:
      warning("no top dir found from %r (originally %r)", path, path0)
    return topdirpath

  @abstractmethod
  def resolve_revision(self, rev_spec):
    ''' Resolve a revision specification to the commit hash (a `str`).
    '''
    raise NotImplementedError()
