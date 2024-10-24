#!/usr/bin/env python3

''' Support for Kobo Desktop libraries.
'''

from contextlib import contextmanager
from dataclasses import dataclass, field
import filecmp
from functools import cached_property, partial
from getopt import GetoptError
import importlib
import os
from os.path import (
    basename,
    exists as existspath,
    expanduser,
    isdir as isdirpath,
    join as joinpath,
)
import sys
from tempfile import NamedTemporaryFile, TemporaryDirectory
from threading import RLock
from typing import Optional, Union
from uuid import UUID
from zipfile import ZipFile, ZIP_DEFLATED

from cs.cmdutils import BaseCommand, vprint
from cs.context import stackattrs
from cs.deco import fmtdoc
from cs.fileutils import atomic_filename
from cs.fs import FSPathBasedSingleton, HasFSPath, shortpath
from cs.fstags import FSTags, uses_fstags
from cs.lex import s
from cs.logutils import warning
from cs.pfx import Pfx, pfx, pfx_call
from cs.progress import progressbar
from cs.resources import MultiOpenMixin, RunState, uses_runstate

from .calibre import CalibreTree
from .common import AbstractEbooksTree, EBooksCommonBaseCommand

pfx_listdir = partial(pfx_call, os.listdir)

def main(argv=None):
  ''' Kobo command line mode.
  '''
  return KoboCommand(argv).run()

KOBO_LIBRARY_ENVVAR = 'KOBO_LIBRARY'
KOBO_LIBRARY_DEFAULT_OSX = '~/Library/Application Support/Kobo/Kobo Desktop Edition'

KINDLE_APP_OSX_DEFAULTS_DOMAIN = 'com.kobo.Kobo Desktop Edition'

OBOK_PACKAGE_PATH_ENVVAR = 'OBOK_PACKAGE_PATH'
OBOK_PACKAGE_ZIPFILE = 'Obok DeDRM.zip'

class KoboTree(AbstractEbooksTree):
  ''' Work with a Kobo ebook tree.
  '''

  CONTENT_DIRNAME = 'kepub'

  FSPATH_ENVVAR = KOBO_LIBRARY_ENVVAR

  def __init__(self, fspath=None):
    if hasattr(self, '_lock'):
      return
    super().__init__(fspath=fspath)
    self._lock = RLock()
    self.lib = None

  @classmethod
  @fmtdoc
  def FSPATH_DEFAULT(cls):
    ''' Return the default Kobo library content path
        from ${KOBO_LIBRARY_ENVVAR}.
        On Darwin, fall back to `KOBO_LIBRARY_DEFAULT_OSX`,
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

  @classmethod
  @fmtdoc
  def import_obok(cls, obok_package_path=None):
    ''' Import the `obok.py` module from the `obok` package.

        This looks in `obok_package_path`, which defaults to
        `${OBOK_PACKAGE_PATH_ENVVAR}` if defined,
        otherwise {OBOK_PACKAGE_ZIPFILE!r} in the default `CalibreTree`
        plugins directory.
    '''
    if obok_package_path is None:
      obok_package_path = pfx_call(os.environ.get, OBOK_PACKAGE_PATH_ENVVAR)
      if obok_package_path is None:
        from .calibre import CalibreTree
        obok_package_path = joinpath(
            CalibreTree().plugins_dirpath, OBOK_PACKAGE_ZIPFILE
        )
    if not existspath(obok_package_path):
      raise ValueError(
          f'obok_package_path does not exist: {obok_package_path!r}'
      )
    if isdirpath(obok_package_path):
      # assume a directory with an obok.py inside it
      with TemporaryDirectory() as tmpdirpath:
        pfx_call(os.symlink, obok_package_path, joinpath(tmpdirpath, 'obok'))
        with stackattrs(sys, path=[tmpdirpath] + sys.path):
          obok = pfx_call(importlib.import_module, '.obok', package='obok')
    else:
      # assume it is a zip file with an obok/obok.py inside it
      with stackattrs(sys, path=[obok_package_path] + sys.path):
        obok = pfx_call(importlib.import_module, '.obok', package='obok')
    return obok

  @contextmanager
  def startup_shutdown(self):
    ''' Open/closethe obok library. '''
    obok = self.import_obok(self.fspath)
    assert self.lib is None
    with stackattrs(self, lib=obok.KoboLibrary(desktopkobodir=self.fspath)):
      try:
        yield
      finally:
        # close the library
        try:
          del self.books
        except AttributeError:
          pass
        self.lib.close()

  @cached_property
  def kobo_lib_books(self):
    ''' A mapping of Kobo library books by their volumeids. '''
    return {UUID(book.volumeid): book for book in self.lib.books}

  def get_library_books_mapping(self):
    ''' A mapping of book volumeids (`UUID`s) to `KoboBook` instance. '''
    return {
        UUID(book.volumeid):
        KoboBook(kobo_tree=self, uuid=UUID(book.volumeid))
        for book in self.kobo_lib_books.values()
    }

  def bookpaths(self):
    ''' Return a list of the filesystem paths in `self.CONTENT_DIRNAME`. '''
    return [
        self.pathto(self.CONTENT_DIRNAME, name)
        for name in pfx_listdir(self.pathto(self.CONTENT_DIRNAME))
    ]

  def __getitem__(self, book_uuid: Union[UUID, str]):
    if isinstance(book_uuid, str):
      book_uuid = UUID(book_uuid)
    return super().__getitem__(book_uuid)

@dataclass
class KoboBook(HasFSPath):
  # the book UUID
  uuid: UUID
  kobo_tree: KoboTree

  def __str__(self):
    return f'{self.kobo_tree}[{self.uuid}]'

  def __getattr__(self, attr):
    return getattr(self.kobo_book, attr)

  @property
  def fspath(self):
    ''' The filesystem path of the KEPub file. '''
    return self.filename

  @property
  def kobo_book(self):
    ''' The KoboBook instance from the obok module. '''
    return self.kobo_tree.kobo_lib_books[self.uuid]

  @property
  def volumeid(self):
    ''' The Kobo volumeid, the book `UUID` as a `str`. '''
    return str(self.uuid)

  @property
  @uses_fstags
  def tags(self, fstags: FSTags):
    ''' The `FSTags` for this book file.
    '''
    return fstags[self.fspath]

  @pfx
  def decrypt(self, dstpath: str, *, exists_ok=False):
    ''' Decrypt the encrypted kepub file of `self` and save the
        decrypted form at `dstpath`.

        This is closely based on the `decrypt_book()` function from
        `obok.obok` in the DeDRM Obok_plugin.

        Parameters:
        * `dstpath`: the filesystem path for the decrypted copy
        * `exists_ok`: optional flag, default `False`;
          if true then it is not an error is `dstpath` already exists
    '''
    obok_book = self.kobo_book
    userkeys = self.kobo_tree.lib.userkeys
    with pfx_call(ZipFile, obok_book.filename, "r") as zin:
      with open(os.devnull, 'w') as devnull:
        with stackattrs(sys, stdout=devnull):
          with atomic_filename(
              dstpath,
              exists_ok=exists_ok,
              suffix=f'--{basename(obok_book.filename)}.zip',
          ) as f:
            with ZipFile(f.name, 'w', ZIP_DEFLATED) as zout:
              for filename in zin.namelist():
                with Pfx(filename):
                  contents = zin.read(filename)
                  try:
                    file = obok_book.encryptedfiles[filename]
                  except KeyError:
                    plain_contents = contents
                  else:
                    for userkey in userkeys:
                      with Pfx("userkey %s", userkey):
                        try:
                          plain_contents = file.decrypt(userkey, contents)
                        except ValueError as e:
                          warning("%s", e)
                          continue
                        try:
                          file.check(plain_contents)
                        except (IndexError, ValueError):
                          # Parse failures mean the key is probably wrong.
                          ##warning("file.check fails: %s", e)
                          continue
                        break
                    else:
                      raise ValueError(
                          f'could not decrypt using any keys from userkeys:{userkeys!r}'
                      )
                  zout.writestr(filename, plain_contents)

  @contextmanager
  def decrypted(self):
    ''' Context manager which decrypts a Kobo book
        and yields the filesystem path of the decrypted copy,
        valid for the duration of the context.
    '''
    with NamedTemporaryFile(prefix=f'kobo--{self.uuid}--',
                            suffix='.epub') as f:
      self.decrypt(f.name, exists_ok=True)
      yield f.name

  # pylint: disable=too-many-branches
  def export_to_calibre(
      self,
      calibre,
      *,
      doit=True,
      replace_format=False,
      force=False,
      quiet=False,
      verbose=False,
  ):
    ''' Export this Kobo book to a Calibre instance,
        return `(cbook,added)`
        being the `CalibreBook` and whether the Kobo book was added
        (books are not added if the format is already present).

        Parameters:
        * `calibre`: the `CalibreTree`
        * `doit`: optional flag, default `True`;
          if false just recite planned actions
        * `force`: optional flag, default `False`;
          if true pull the AZW file even if an AZW format already exists
        * `replace_format`: if true, export even if the `AZW3`
          format is already present
        * `quiet`: default `False`, do not print nonwarnings
        * `verbose`: default `False`, print all actions or nonactions
    '''
    with self.decrypted() as bookpath:
      added = False
      cbooks = list(calibre.by_kobo_volumeid(self.volumeid))
      if not cbooks:
        # new book
        # pylint: disable=expression-not-assigned
        quiet or print("new book <=", self.shortpath)
        dbid = calibre.add(
            bookpath,
            doit=doit,
            quiet=quiet,
            add_args=['-I', f'kobo-volumeid:{self.volumeid}'],
        )
        if dbid is None:
          added = not doit
          cbook = None
        else:
          added = True
          cbook = calibre[dbid]
          quiet or print(" ", cbook)
      else:
        # book already present in calibre
        cbook = cbooks[0]
        if len(cbooks) > 1:
          warning(
              "multiple calibre books, dbids %r: choosing %s",
              [cb.dbid for cb in cbooks], cbook
          )
        with Pfx(cbook):
          # look for exact content match
          for fmtk in 'EPUB', :
            fmtpath = cbook.formatpath(fmtk)
            if fmtpath and existspath(fmtpath):
              if filecmp.cmp(fmtpath, bookpath):
                # pylint: disable=expression-not-assigned
                verbose and print(
                    cbook, fmtk, shortpath(fmtpath), '=', shortpath(bookpath)
                )
                return cbook, False
            # remaining logic is in CalibreBook.pull_format
            cbook.pull_format(
                bookpath, doit=doit, force=force, quiet=quiet, verbose=verbose
            )
    return cbook, added

class KoboCommand(EBooksCommonBaseCommand):
  ''' Command line for interacting with a Kobo Desktop filesystem tree.
  '''

  SUBCOMMAND_ARGV_DEFAULT = 'info'

  @contextmanager
  @uses_fstags
  def run_context(self, fstags: FSTags):
    with super().run_context():
      options = self.options
      kobo_path = options.kobo_path
      with KoboTree(kobo_path) as kobo:
        options.kobo_path = kobo.fspath
        with CalibreTree(options.calibre_path) as cal:
          with stackattrs(options, kobo=kobo, calibre=cal):
            with fstags:
              yield

  # pylint: disable=too-many-locals
  @uses_runstate
  def cmd_export(self, argv, *, runstate: RunState):
    ''' Usage: {cmd} [-f] [volumeids...]
          Export Kobo books to Calibre library.
          -f    Force: replace the EPUB format if already present.
          volumeids
                Optional Kobo volumeid identifiers to export.
                The default is to export all books.
                (TODO: just those with no "calibre.dbid" fstag.)
    '''
    options = self.options
    options.popopts(argv, f='force')
    doit = options.doit
    force = options.force
    quiet = options.quiet
    verbose = options.verbose
    calibre = options.calibre
    kobo = options.kobo
    volumeids = argv or sorted(str(vid) for vid in kobo.volumeids)
    xit = 0
    quiet or print("export", kobo.shortpath, "=>", calibre.shortpath)
    for vid in progressbar(volumeids, f"export to {calibre}"):
      with Pfx(vid):
        book = kobo[vid]
        runstate.raiseif()
        try:
          book.export_to_calibre(
              calibre,
              doit=doit,
              force=force,
              replace_format=force,
              quiet=quiet,
              verbose=verbose,
          )
        except ValueError as e:
          warning("export failed: %s", e)
          xit = 1
        except Exception as e:
          warning("kobo book.export_to_calibre: e=%s", s(e))
          raise
    return xit

  def cmd_ls(self, argv):
    ''' Usage: {cmd} [volumeids...]
          List the contents of the library.
          (TODO: -l  Long mode.)
          volumeids
                Optional Kobo volumeid identifiers to list.
    '''
    kobo = self.options.kobo
    volumeids = argv or sorted(str(vid) for vid in kobo.volumeids)
    for vid in volumeids:
      book = kobo[vid]
      print(book.uuid, book)
      print(" ", book.author, book.title)
      print(" ", book.filename)

if __name__ == '__main__':
  sys.exit(KoboCommand(sys.argv).run())
