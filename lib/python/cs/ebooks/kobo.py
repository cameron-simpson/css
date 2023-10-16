#!/usr/bin/env python3

from contextlib import contextmanager
from dataclasses import dataclass
from functools import cached_property, partial
from getopt import GetoptError
import os
from os.path import expanduser
import sys
from tempfile import NamedTemporaryFile
from threading import RLock
from typing import Optional
from uuid import UUID

from cs.cmdutils import BaseCommand
from cs.context import stackattrs
from cs.deco import fmtdoc
from cs.fs import FSPathBasedSingleton, shortpath
from cs.pfx import Pfx, pfx, pfx_call
from cs.resources import MultiOpenMixin

from .dedrm import import_obok, decrypt_obok

obok = import_obok()

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
    self.lib = obok.KoboLibrary(desktopkobodir=self.fspath)

  @cached_property
  def books(self):
    ''' A mapping of book volumeids (`UUID`s) to Kobo books. '''
    return {
        UUID(book.volumeid):
        KoboBook(kobo_tree=self, uuid=UUID(book.volumeid), kobo_book=book)
        for book in self.lib.books
    }

  @cached_property
  def volumeids(self):
    ''' `self.books.keys()`, a collection of `UUID`s. '''
    return self.books.keys()

  def bookpaths(self):
    ''' Return a list of the filesystem paths in `self.CONTENT_DIRNAME`. '''
    return [
        self.pathto(self.CONTENT_DIRNAME, name)
        for name in pfx_listdir(self.pathto(self.CONTENT_DIRNAME))
    ]

  def __iter__(self):
    return iter(sorted(self.books.values(), key=lambda book: book.uuid))

  def __getitem__(self, book_uuid: UUID):
    return self.books[book_uuid]

@dataclass
class KoboBook:
  uuid: UUID
  kobo_tree: KoboTree
  kobo_book: obok.KoboBook

  def __str__(self):
    return f'{self.kobo_tree}[{self.uuid}]'

  def __getattr__(self, attr):
    return getattr(self.kobo_book, attr)

  @property
  def fspath(self):
    return self.filename

  @pfx
  def decrypt(self, dstpath, exists_ok=False):
    ''' Decrypt the encrypted kepub file of `book` and save the
        decrypted form at `dstpath`.

        This is closely based on the `decrypt_book()` function from
        `obok.obok` in the DeDRM Obok_plugin.
    '''
    return decrypt_obok(
        self.kobo_tree.lib, self.kobo_book, dstpath, exists_ok=exists_ok
    )

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
