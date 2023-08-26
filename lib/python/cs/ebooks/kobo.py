#!/usr/bin/env python3

from contextlib import contextmanager
from dataclasses import dataclass
from functools import partial
from getopt import GetoptError
import os
from os.path import expanduser
import sys
from threading import RLock
from typing import Optional

from cs.cmdutils import BaseCommand
from cs.context import stackattrs
from cs.deco import fmtdoc
from cs.fs import FSPathBasedSingleton, shortpath
from cs.pfx import Pfx, pfx_call
from cs.resources import MultiOpenMixin

pfx_listdir = partial(pfx_call, os.listdir)

def main(argv=None):
  ''' Kobo command line mode.
  '''
  return KoboCommand(argv).run()

KOBO_LIBRARY_ENVVAR = 'KOBO_LIBRARY'
KOBO_LIBRARY_DEFAULT_OSX = '~/Library/Application Support/Kobo/Kobo Desktop Edition'

KINDLE_APP_OSX_DEFAULTS_DOMAIN = 'com.kobo.Kobo Desktop Edition'

@fmtdoc
def default_kobo_library():
  ''' Return the default Kobo library content path
      from ${KOBO_LIBRARY_ENVVAR}.
      On Darwin, fall back to KOBO_LIBRARY_DEFAULT_OSX,
      {KOBO_LIBRARY_DEFAULT_OSX!r}.
      Raises `RuntimeError` if there is no default.
    '''
  path = os.environ.get(KOBO_LIBRARY_ENVVAR, None)
  if path is not None:
    return path
  if sys.platform == 'darwin':
    return expanduser(KOBO_LIBRARY_DEFAULT_OSX)
  raise RuntimeError(
      "I do not know the default Kobo content path on platform %r" %
      (sys.platform,)
  )

class KoboTree(FSPathBasedSingleton, MultiOpenMixin):
  ''' Work with a Kobo ebook tree.

      This actually knows very little about Kobo ebooks or its database.
      This is mostly to aid keeping track of state using `cs.fstags`.
  '''

  CONTENT_DIRNAME = 'kepub'

  FSPATH_DEFAULT = default_kobo_library()

  FSPATH_ENVVAR = KOBO_LIBRARY_ENVVAR

  def __init__(self, fspath=None):
    if hasattr(self, '_lock'):
      return
    super().__init__(fspath=fspath)
    self._lock = RLock()

  def bookpaths(self):
    return [
        self.pathto(self.CONTENT_DIRNAME, name)
        for name in pfx_listdir(self.pathto(self.CONTENT_DIRNAME))
    ]

class KoboCommand(BaseCommand):

  SUBCOMMAND_ARGV_DEFAULT = 'info'

  @dataclass
  class Options(BaseCommand.Options):
    ''' Set up the default values in `options`.
    '''
    kobo_path: Optional[str] = None

  @contextmanager
  def run_context(self):
    with super().run_context():
      options = self.options
      kobo_path = options.kobo_path
      if kobo_path is None:
        kobo_path = default_kobo_library()
      with KoboTree(kobo_path) as kt:
        with stackattrs(options, kobo=kt):
          yield

  def cmd_info(self, argv):
    ''' Usage: {cmd}
          Report basic information.
    '''
    if argv:
      raise GetoptError("extra arguments: %r" % (argv,))
    kobo = self.options.kobo
    print("kobo", kobo)
    for bookpath in kobo.bookpaths():
      print(" ", shortpath(bookpath))
