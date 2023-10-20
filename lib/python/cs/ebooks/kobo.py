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
    isfile as isfilepath,
    join as joinpath,
)
import sys
from tempfile import NamedTemporaryFile, TemporaryDirectory
from threading import RLock
from typing import Optional, Union
from uuid import UUID
from zipfile import ZipFile, ZIP_DEFLATED

from cs.cmdutils import BaseCommand
from cs.context import stackattrs
from cs.deco import fmtdoc
from cs.fileutils import atomic_filename
from cs.fs import FSPathBasedSingleton, HasFSPath, shortpath
from cs.fstags import FSTags, uses_fstags
from cs.lex import s
from cs.logutils import warning
from cs.pfx import Pfx, pfx, pfx_call
from cs.progress import progressbar
from cs.resources import MultiOpenMixin

from .calibre import CalibreTree

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

@pfx
@fmtdoc
def import_obok(obok_package_path=None):
  ''' Import the `obok.py` module.

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
  if isfilepath(obok_package_path):
    with stackattrs(sys, path=[obok_package_path] + sys.path):
      obok = pfx_call(importlib.import_module, '.obok', package='obok')
  else:
    with TemporaryDirectory() as tmpdirpath:
      pfx_call(os.symlink, obok_package_path, joinpath(tmpdirpath, 'obok'))
      with stackattrs(sys, path=[tmpdirpath] + sys.path):
        obok = pfx_call(importlib.import_module, '.obok', package='obok')
  return obok

obok = import_obok()

@pfx
def decrypt_obok(obok_lib, obok_book, dstpath: str, exists_ok=False):
  ''' Decrypt the encrypted kepub file of `obok_book` and save the
      decrypted form at `dstpath`.

      This is closely based on the `decrypt_book()` function from
      `obok.obok` in the DeDRM Obok_plugin.
  '''
  userkeys = obok_lib.userkeys
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
                      except (IndexError, ValueError) as e:
                        # Parse failures mean the key is probably wrong.
                        ##warning("file.check fails: %s", e)
                        continue
                      break
                  else:
                    raise ValueError(
                        f'could not decrypt using any keys from userkeys:{userkeys!r}'
                    )
                zout.writestr(filename, plain_contents)

class KoboTree(FSPathBasedSingleton, MultiOpenMixin):
  ''' Work with a Kobo ebook tree.

      This actually knows very little about Kobo ebooks or its database.
      This is mostly to aid keeping track of state using `cs.fstags`.
  '''

  CONTENT_DIRNAME = 'kepub'

  FSPATH_DEFAULT = default_kobo_library
  FSPATH_ENVVAR = KOBO_LIBRARY_ENVVAR

  def __init__(self, fspath=None):
    if hasattr(self, '_lock'):
      return
    super().__init__(fspath=fspath)
    self._lock = RLock()
    self.lib = None

  @contextmanager
  def startup_shutdown(self):
    ''' Open/closethe obok library. '''
    assert self.lib is None
    self.lib = obok.KoboLibrary(desktopkobodir=self.fspath)
    try:
      yield
    finally:
      # close the library
      try:
        del self.books
      except AttributeError:
        pass
      self.lib.close()
      self.lib = None

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

  def __getitem__(self, book_uuid: Union[UUID, str]):
    if isinstance(book_uuid, str):
      book_uuid = UUID(book_uuid)
    return self.books[book_uuid]

@dataclass
class KoboBook(HasFSPath):
  uuid: UUID
  kobo_tree: KoboTree
  kobo_book: obok.KoboBook

  def __str__(self):
    return f'{self.kobo_tree}[{self.uuid}]'

  def __getattr__(self, attr):
    return getattr(self.kobo_book, attr)

  @property
  def fspath(self):
    ''' The filesystem path of the KEPub file. '''
    return self.filename

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
  def decrypt(self, dstpath, exists_ok=False):
    ''' Decrypt the encrypted kepub file of `book` and save the
        decrypted form at `dstpath`.

        This is closely based on the `decrypt_book()` function from
        `obok.obok` in the DeDRM Obok_plugin.
    '''
    return decrypt_obok(
        self.kobo_tree.lib, self.kobo_book, dstpath, exists_ok=exists_ok
    )

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

class KoboCommand(BaseCommand):
  ''' Command line for interacting with a Kobo Desktop filesystem tree.
  '''

  SUBCOMMAND_ARGV_DEFAULT = 'info'

  @dataclass
  class Options(BaseCommand.Options):
    ''' Set up the default values in `options`.
    '''

    def _calibre_path():
      try:
        # pylint: disable=protected-access
        calibre_path = CalibreTree._resolve_fspath(None)
      except ValueError:
        calibre_path = None
      return calibre_path

    calibre_path: Optional[str] = field(default_factory=_calibre_path)
    calibre: Optional[CalibreTree] = None
    kobo_path: Optional[str] = None
    kobo: Optional[KoboTree] = None

  @contextmanager
  @uses_fstags
  def run_context(self, fstags: FSTags):
    with super().run_context():
      options = self.options
      kobo_path = options.kobo_path
      if kobo_path is None:
        kobo_path = default_kobo_library()
      with KoboTree(kobo_path) as kobo:
        with CalibreTree(options.calibre_path) as cal:
          with stackattrs(options, kobo=kobo, calibre=cal):
            with fstags:
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

  # pylint: disable=too-many-locals
  def cmd_export(self, argv):
    ''' Usage: {cmd} [-fnqv] [volumeids...]
          Export Kobo books to Calibre library.
          -f    Force: replace the EPUB format if already present.
          -n    No action, recite planned actions.
          -q    Quiet: report only warnings.
          -v    Verbose: report more information about actions and inaction.
          volumeids
                Optional Kobo volumeid identifiers to export.
                The default is to export all books.
                (TODO: just those with no "calibre.dbid" fstag.)
    '''
    options = self.options
    calibre = options.calibre
    kobo = options.kobo
    runstate = options.runstate
    self.popopts(argv, options, f='force', n='-doit', q='quiet', v='verbose')
    doit = options.doit
    force = options.force
    quiet = options.quiet
    verbose = options.verbose
    volumeids = argv or sorted(str(vid) for vid in kobo.volumeids)
    xit = 0
    quiet or print("export", kobo.shortpath, "=>", calibre.shortpath)
    for vid in progressbar(volumeids, f"export to {calibre}"):
      with Pfx(vid):
        book = kobo[vid]
        if runstate.cancelled:
          xit = 1
          break
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
