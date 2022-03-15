#!/usr/bin/env python3

''' Apple Books access.
'''

from contextlib import contextmanager
from getopt import GetoptError
from glob import glob
from os.path import join as joinpath
import sys

from . import FSPathBasedSingleton

from typeguard import typechecked

from cs.cmdutils import BaseCommand
from cs.context import stackattrs
from cs.deco import strable
from cs.logutils import warning
from cs.pfx import pfx_call, pfx_method
from cs.resources import MultiOpenMixin
from cs.sqlalchemy_utils import ORM

class AppleBooksTree(FSPathBasedSingleton, MultiOpenMixin):
  ''' Work with an Apple Books tree.
  '''

  FSPATH_DEFAULT = '~/Library/Containers/com.apple.iBooksX/Data//Documents/BKLibrary'
  FSPATH_ENVVAR = 'APPLE_BOOKS_LIBRARY'

class AppleBooksDB(ORM):
  ''' An ORM to access the Apple Books SQLite database.
  '''

  # example: BKLibrary-1-091020131601.sqlite
  DB_GLOB = 'BKLibrary-1-*.sqlite'

  @strable(open_func=AppleBooksTree)
  @typechecked
  def __init__(self, tree: AppleBooksTree):
    self.tree = tree
    self.dbpath = self._find_library_path()

  @pfx_method
  def _find_library_path(self):
    ''' Look up the path of the SQLite database.
      '''
    dbpaths = pfx_call(glob, joinpath(self.tree.fspath, self.DB_GLOB))
    if not dbpaths:
      raise ValueError("no matching library file")
    if len(dbpaths) > 1:
      dbpaths = sorted(dbpaths)
      warning(
          "  \n".join(["multiple matches, choosing the latest:", *dbpaths])
      )
      dbpath = dbpaths[-1]
    else:
      dbpath, = dbpaths
    return dbpath

class AppleBooksCommand(BaseCommand):
  ''' Command line access to Apple Books.
  '''

  def apply_defaults(self):
    ''' Set up the default values in `options`.
    '''
    options = self.options
    options.apple_path = None

  @contextmanager
  def run_context(self):
    ''' Prepare the `SQLTags` around each command invocation.
    '''
    options = self.options
    with AppleBooksTree(options.apple_path) as at:
      with stackattrs(options, apple=at, verbose=True):
        yield

  def cmd_ls(self, argv):
    ''' Usage: {cmd} ls
    '''
    if argv:
      raise GetoptError("extra arguments: %r" % (argv,))

if __name__ == '__main__':
  sys.exit(AppleBooksCommand(sys.argv).run())
