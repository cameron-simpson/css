#!/usr/bin/env python3
#
# pylint: disable=too-many-lines

''' Support for Calibre libraries.
'''

from code import interact
from collections import ChainMap
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import filecmp
from functools import cached_property, total_ordering
from getopt import GetoptError
from itertools import chain
import json
import os
from os.path import (
    basename,
    dirname,
    exists as existspath,
    expanduser,
    isabs as isabspath,
    isdir as isdirpath,
    isfile as isfilepath,
    join as joinpath,
    splitext,
)
import re
import shlex
from subprocess import DEVNULL
import sys
from tempfile import TemporaryDirectory
from typing import Iterable, Optional, Union
from uuid import UUID

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
)
from sqlalchemy.ext.associationproxy import association_proxy
from sqlalchemy.orm import (
    declared_attr,
    Mapped,
    mapped_column,
    relationship,
)
from typeguard import typechecked

from cs.cmdutils import popopts, qvprint
from cs.context import contextif
from cs.deco import (
    fmtdoc,
    uses_cmd_options,
    uses_doit,
    uses_force,
    uses_quiet,
    uses_verbose,
)
from cs.fs import HasFSPath, shortpath
from cs.lex import (
    cutprefix,
    get_dotted_identifier,
    lc_,
    stripped_dedent,
    FormatableMixin,
    FormatAsError,
)
from cs.logutils import warning, error
from cs.numeric import intif
from cs.obj import SingletonMixin
from cs.pfx import Pfx, pfx_call, pfx_method
from cs.progress import progressbar
from cs.psutils import run
from cs.resources import RunState, uses_runstate
from cs.seq import unrepeated
from cs.sqlalchemy_utils import (
    ORM, BasicTableMixin, HasIdMixin, RelationProxy, proxy_on_demand_field
)
from cs.tagset import TagSet
from cs.threads import locked
from cs.units import transcribe_bytes_geek
from cs.upd import UpdProxy, print, run_task  # pylint: disable=redefined-builtin

from .common import AbstractEbooksTree, EBooksCommonBaseCommand
from .dedrm import DeDRMWrapper
from .mobi import Mobi  # pylint: disable=import-outside-toplevel
from .pdf import PDFDocument

CALIBRE_FSPATH = '~/Calibre Library'
CALIBRE_FSPATH_ENVVAR = 'CALIBRE_LIBRARY'
# envar $CALIBRE_LIBRARY_OTHER as push/pull etc "other library"
OTHER_LIBRARY_PATH_ENVVAR = CALIBRE_FSPATH_ENVVAR + '_OTHER'
CALIBRE_BINDIR_DEFAULT = '/usr/bin'
CALIBRE_PREFSDIR_DEFAULT = expanduser('~/.config/calibre')
if sys.platform == 'darwin':
  ## macos default? ## CALIBRE_FSPATH = '~/Calibre Library'
  CALIBRE_BINDIR_DEFAULT = '/Applications/calibre.app/Contents/MacOS'
  CALIBRE_PREFSDIR_DEFAULT = expanduser('~/Library/Preferences/calibre')

class CalibreTree(AbstractEbooksTree):
  ''' Work with a Calibre ebook tree.
  '''

  # used by FSPathBasedSingleton for the default library path
  FSPATH_ENVVAR = CALIBRE_FSPATH_ENVVAR

  @classmethod
  @fmtdoc
  def FSPATH_DEFAULT(cls):
    ''' Called for the default `CalibreTree` filesystem path
        if unspecified and no `${CALIBRE_FSPATH_ENVVAR}` environment
        variable.
    '''
    return expanduser(CALIBRE_FSPATH)

  # pylint: disable=too-many-statements
  @typechecked
  def __init__(
      self,
      calibrepath: Optional[str] = None,
      bin_dirpath: Optional[str] = None,
      prefs_dirpath: Optional[str] = None,
  ):
    ''' Initialise the `CalibreTree`.

        Parameters:
        * `calibrepath`: optional filesystem path of the Calibre library,
          default from 
    '''
    super().__init__(calibrepath)
    if not isdirpath(self.fspath):
      raise ValueError(f'no directory at {self.fspath!r}')
    self.bin_dirpath = bin_dirpath or CALIBRE_BINDIR_DEFAULT
    self.prefs_dirpath = prefs_dirpath or self.get_default_prefs_dirpath()
    self.books_by_dbib = {}

    # define the proxy classes
    class CalibreBook(SingletonMixin, RelationProxy(self.db.books, [
        'author_sort',
        'authors',
        'flags',
        'has_cover',
        'isbn',
        'last_modified',
        'lccn',
        'path',
        'pubdate',
        'series_index',
        'sort',
        'tags',
        'timestamp',
        'title',
        'uuid',
    ]), HasFSPath, FormatableMixin):
      ''' A reference to a book in a Calibre library.
      '''

      # pylint: disable=unused-argument
      @classmethod
      def _singleton_key(cls, tree: CalibreTree, dbid: int, db_book=None):
        ''' The singleton key is `(tree,dbid)`.
        '''
        return id(tree), dbid

      @typechecked
      def __init__(self, tree: CalibreTree, dbid: int, db_book=None):
        if 'tree' in self.__dict__:
          if db_book is not None:
            self.refresh_from_db(db_row=db_book)
        else:
          super().__init__(dbid, db_row=db_book)
          self.tree = tree

      def __str__(self):
        return f"{self.dbid}: {self.title}"

      @property
      def fspath(self):
        ''' The filesystem path to where the book formats are stored.
        '''
        return self.tree.pathto(self.path)

      def format_tagset(self):
        ''' Compute a `TagSet` representing this book's metadata.
        '''
        formats = self.formats
        tags = TagSet(
            dbid=self.dbid,
            asin=self.asin,
            title=self.title,
            authors=self.author_names,
            formats=formats,
            format_names=sorted(formats.keys()),
            tags=self.tags,
        )
        tags.update(
            {
                'series.id': self.series_id,
                'series.name': self.series_name,
                'series.index': intif(self.series_index),
            }
        )
        return tags

      def format_kwargs(self):
        ''' Return the `CalibreTree` format `TagSet` as a mapping.
        '''
        return self.format_tagset()

      @staticmethod
      def get_arg_name(field_name):
        ''' Override for `FormattableMixin.get_arg_name`:
            return the leading dotted identifier,
            which represents a tag or tag prefix.
        '''
        return get_dotted_identifier(field_name)

      @property
      def dbid(self):
        ''' An alias for the `.id` attribute.
        '''
        return self.id

      @property
      def asin(self):
        ''' The Amazon ASIN, or `None`, from `self.identifiers['mobi-asin']`.
        '''
        return self.identifiers.get('mobi-asin', None)

      @classmethod
      def refresh_from_db_row(cls, db_row, fields, *, session):
        ''' Refresh the cached values from the database.
        '''
        super().refresh_from_db_row(db_row, fields, session=session)
        for field_name in 'authors', 'formats', 'idenitifiers', 'tags':
          try:
            del fields[field_name]
          except KeyError:
            pass

      @property
      @proxy_on_demand_field
      # pylint: disable=property-with-parameters
      def authors(self, db_row, *, session):
        ''' The book Authors.
        '''
        return db_row.authors

      @property
      @proxy_on_demand_field
      # pylint: disable=property-with-parameters
      def series_id(self, db_row, *, session):
        ''' The book Series or `None`.
        '''
        if not db_row.series:
          return None
        sid, = db_row.series
        return sid

      @property
      def series_name(self):
        ''' The series name or `None`.
        '''
        sid = self.series_id
        if sid is None:
          return None
        orm = self.orm
        with orm.session() as session:
          srow = orm.series.by_id(sid, session=session)
          return srow.name

      @property
      def author_names(self):
        ''' A list of the author names.
        '''
        return [author.name for author in self.authors]

      @property
      @proxy_on_demand_field
      # pylint: disable=property-with-parameters
      def formats(self, db_row, *, session):
        ''' A mapping of Calibre format keys to format paths
            computed on demand.
        '''
        return {
            fmt.format: f'{fmt.name}.{fmt.format.lower()}'
            for fmt in db_row.formats
        }

      def formatpath(self, fmtk):
        ''' Return the filesystem path of the format file for `fmtk`
            or `None` if the format is not present.
        '''
        try:
          fmtsubpath = self.formats[fmtk]
        except KeyError:
          return None
        return self.pathto(fmtsubpath)

      @property
      def dedrm(self):
        ''' The available `DeDRMWrapper` is any, or `None`.
        '''
        return self.tree.dedrm

      @uses_cmd_options(dedrm=None)
      @pfx_method
      def decrypt(self, dedrm: "DeDRMWrapper", fmtk='AZW'):
        ''' Decrypt the book file in format `fmtk`.
        '''
        bookpath = self.formatpath(fmtk)
        if bookpath is None:
          raise KeyError(fmtk)
        if dedrm is None:
          raise NotImplementedError('no DeDRMWrapper available')
        with dedrm:
          decrypted = pfx_call(
              dedrm.decrypt, bookpath, bookpath, exists_ok=True
          )
          if decrypted:
            qvprint("decrypted:", shortpath(bookpath))
          else:
            qvprint("already decrypted:", shortpath(bookpath))
        return decrypted

      @pfx_method
      @typechecked
      def add_format(
          self,
          bookpath: str,
          *,
          force: bool = False,
          doit=True,
          quiet=False,
          **subp_options,
      ):
        ''' Add a book file to the existing book formats
            via the `calibredb add_format` command.
            Return `True` if the `doit` is false or the command succeeds,
            `False` otherwise.

            Parameters:
            * `bookpath`: filesystem path to the source MOBI file
            * `doit`: default `True`; do not run the command if false
            * `force`: replace an existing format if already present, default `False`
            * `quiet`: default `False`; only print warning if true
        '''
        cp = self.tree.calibredb(
            'add_format',
            *(() if force else ('--dont-replace',)),
            str(self.id),
            bookpath,
            doit=doit,
            quiet=quiet,
            stdin=DEVNULL,
            **subp_options,
        )
        if cp is None:
          return True
        if cp.returncode != 0:
          warning("command fails, return code %d", cp.returncode)
          return False
        self.refresh_from_db()
        return True

      @property
      @proxy_on_demand_field
      # pylint: disable=property-with-parameters
      def identifiers(self, db_row, *, session):
        ''' A mapping of Calibre identifier keys to identifier values
            computed on demand.
        '''
        return {
            identifier.type: identifier.val
            for identifier in db_row.identifiers
        }

      @property
      @proxy_on_demand_field
      # pylint: disable=property-with-parameters
      def tags(self, db_row, *, session):
        ''' A list of Calibre tags computed on demand.
        '''
        return [tag.name for tag in db_row.tags]

      # TODO: should really edit the db directly
      @tags.setter
      def tags(self, new_tags):
        ''' Update the tags.
        '''
        self.tree.calibredb(
            'set_metadata',
            '--field',
            f'tags:{",".join(new_tags)}',
            str(self.dbid),
        )
        try:
          del self._RelProxy__fields['tags']
        except KeyError:
          pass

      def convert(
          self,
          srcfmtk,
          dstfmtk,
          *conv_opts,
          doit=True,
          force=False,
          quiet=False,
      ):
        ''' Convert the existing format `srcfmtk` into `dstfmtk`.
        '''
        calibre = self.tree
        srcpath = self.formatpath(srcfmtk)
        srcbase = basename(srcpath)
        dstbase = splitext(srcbase)[0] + '.' + dstfmtk.lower()
        if srcbase == dstbase:
          raise ValueError(
              "source format basename %r == destination format basename %r, skipping"
              % (srcbase, dstbase)
          )
        with TemporaryDirectory(prefix='.convert-') as dirpath:
          dstpath = joinpath(dirpath, dstbase)
          calibre.ebook_convert(
              srcpath, dstpath, *conv_opts, check=True, doit=doit, quiet=quiet
          )
          self.add_format(dstpath, force=force, doit=doit, quiet=quiet)

      @property
      def mobipath(self):
        ''' The filesystem path of a Mobi format book file, or `None`.
        '''
        for fmtk in 'MOBI', 'AZW3', 'AZW':
          fmtpath = self.formatpath(fmtk)
          if fmtpath is not None:
            return fmtpath
        return None

      @property
      def pdfpath(self):
        ''' The filesystem path of a Mobi format book file, or `None`.
        '''
        return self.formatpath('PDF')

      def make_cbz(self, replace_format=False):
        ''' Create a CBZ format from the AZW3 Mobi format,
            falling back to PDF if there's one of those.
        '''
        formats = self.formats
        if 'CBZ' in formats and not replace_format:
          warning(
              "format CBZ already present (forats=%r), not adding", formats
          )
          return
        mobipath = self.mobipath
        pdfpath = self.pdfpath
        if mobipath:
          base, _ = splitext(basename(mobipath))
          MB = Mobi(mobipath)
          with TemporaryDirectory() as tmpdirpath:
            cbzpath = joinpath(tmpdirpath, base + '.cbz')
            pfx_call(MB.make_cbz, cbzpath)
            self.add_format(cbzpath, force=replace_format)
        elif pdfpath:
          base, _ = splitext(basename(pdfpath))
          pdf = PDFDocument.from_fspath(pdfpath)
          with TemporaryDirectory() as tmpdirpath:
            cbzpath = joinpath(tmpdirpath, base + '.cbz')
            pfx_call(pdf.make_cbz, cbzpath)
            self.add_format(cbzpath, force=replace_format)
        else:
          raise ValueError(
              "no AZW3, AZW, MOBI or PDF format from which to construct a CBZ"
          )

      @uses_runstate
      def pull(
          self,
          obook,
          *,
          doit=True,
          formats=None,
          force=False,
          quiet=False,
          verbose=False,
          runstate: RunState,
      ):
        ''' Pull formats from another `CalibreBook`.

            Parameters:
            * `obook`: the other book
            * `doit`: optional flag, default `True`;
              import formats if true, report actions otherwise
            * `formats`: optional list of Calibre format keys to pull if present
            * `force`: optional flag, default `False`;
              if true import formats even if already present
            * `quiet`: optional flag, default `False`;
              if true only print warnings
            * `verbose`: optional flag, default `False`;
              if true print all actions and inactions
        '''
        if formats is None:
          formats = sorted(obook.formats.keys())
        with Pfx("%s <= %s", self, obook):
          for fmtk in formats:
            if runstate.cancelled:
              break
            ofmtpath = obook.formatpath(fmtk)
            if ofmtpath is None:
              continue
            with Pfx(fmtk):
              self.pull_format(
                  ofmtpath,
                  fmtk=fmtk,
                  doit=doit,
                  force=force,
                  quiet=quiet,
                  verbose=verbose
              )

      @uses_doit
      @uses_force
      @uses_quiet
      @uses_verbose
      def pull_format(
          self,
          ofmtpath,
          *,
          fmtk=None,
          doit,
          force,
          quiet,
          verbose,
      ):
        ''' Pull a format file, typically from another `CalibreBook`.

            Parameters:
            * `ofmtpath`: the filesystem path of the format to pull
            * `fmtk`: optional format key,
              default derived from the `ofmtpath` filename extension
            * `doit`: optional flag, default `True`;
              import formats if true, report actions otherwise
            * `force`: optional flag, default `False`;
              if true import formats even if already present
            * `quiet`: optional flag, default `False`;
              if true only print warnings
            * `verbose`: optional flag, default `False`;
              if true print all actions and inactions
        '''
        if fmtk is None:
          _, ext = splitext(basename(ofmtpath))
          if ext:
            assert ext.startswith('.')
            fmtk = ext[1:].upper()
        if fmtk is None:
          warning(
              "cannot infer format key from %r, not doing a precheck", ofmtpath
          )
        else:
          fmtpath = self.formatpath(fmtk)
          if fmtpath is None and fmtk.startswith('AZW'):
            fmtpath = (
                self.formatpath('AZW4') or self.formatpath('AZW3')
                or self.formatpath('AZW') or self.formatpath('MOBI')
            )
          if fmtpath is not None and not force:
            if filecmp.cmp(fmtpath, ofmtpath):
              # pylint: disable=expression-not-assigned
              qvprint(self, fmtk, "identical to", shortpath(ofmtpath))
            else:
              # pylint: disable=expression-not-assigned
              verbose and warning(
                  "already present with different content\n"
                  "  present: %s\n"
                  "  other:   %s",
                  shortpath(fmtpath),
                  shortpath(ofmtpath),
              )
            return
        # pylint: disable=expression-not-assigned
        qvprint(self, self.formats, '+', fmtk, '<=', shortpath(ofmtpath))
        self.add_format(ofmtpath, doit=doit, force=force, quiet=quiet)

    self.CalibreBook = CalibreBook

  @staticmethod
  def get_default_prefs_dirpath():
    return (
        os.environ.get('CALIBRE_CONFIG_DIRECTORY')
        or expanduser(CALIBRE_PREFSDIR_DEFAULT)
    )

  @contextmanager
  def startup_shutdown(self):
    ''' Stub startup/shutdown.
    '''
    yield

  @property
  def plugins_dirpath(self):
    ''' Where the installed plugins live. '''
    return joinpath(self.prefs_dirpath, 'plugins')

  @cached_property
  @locked
  def db(self):
    ''' The associated `CalibreMetadataDB` ORM,
        instantiated on demand.
    '''
    return CalibreMetadataDB(self)

  @contextmanager
  def db_session(self):
    ''' Context manager for a database session.
        This is not fired by `startup_shutdown` because any callout
        to the Calibre command executables also want exclusive
        access to the database.
    '''
    with self.db.session() as session:
      yield session

  def dbshell(self):
    ''' Interactive db shell.
    '''
    return self.db.shell()

  def refresh_dbid(self, dbid, session=None):
    ''' Refresh our knowledge of `dbid` from the database,
        which may be a new `dbid` not previously known.
    '''
    db = self.db
    with db.session() as session:
      db_book = self.db.books.lookup1(id=dbid, session=session)
      if db_book is None:
        raise ValueError(f'unknown database book id {dbid!r}')
      book = self.books_by_dbib.get(dbid)
      if book is None:
        self.books_by_dbib[dbid] = self.CalibreBook(
            self, dbid, db_book=db_book
        )
      else:
        book.refresh_from_db(db_book)

  @uses_quiet
  @uses_verbose
  def preload(self, *, quiet, verbose):
    ''' Scan all the books, preload their data.
    '''
    with contextif(
        not quiet,
        run_task,
        f'preload {self}',
        report_print=verbose,
    ):
      db = self.db
      with db.session() as session:
        for db_book in self.db.books.lookup(session=session):
          dbid = db_book.id
          book = self.books_by_dbib.get(dbid)
          if book is None:
            self.books_by_dbib[dbid] = self.CalibreBook(
                self, dbid, db_book=db_book
            )
          else:
            book.refresh_from_db(db_book)

  def get_library_books_mapping(self, preload=False):
    if preload or not self.books_by_dbib:
      self.preload()
    return self.books_by_dbib

  def __getitem__(self, dbid: int):
    mapping = self.get_library_books_mapping()
    try:
      cbook = mapping[dbid]
    except KeyError:
      self.refresh_dbid(dbid)
      cbook = mapping[dbid]
    return cbook

  def identifier_names(self):
    ''' Return an iterable of the identifiers in use in the library.
    '''
    return set(chain(*(cbook.identifiers.keys() for cbook in self)))

  def by_identifier(self, type_, value):
    ''' Generator yielding `CalibreBook`
        matching the provided `(type,val)` identifier.
    '''
    db = self.db
    with db.session() as session:
      for identifier in db.identifiers.lookup(
          session=session,
          type=type_,
          val=value,
      ):
        yield self[identifier.book_id]

  def by_asin(self, asin):
    ''' Return an iterable of `CalibreBook`s with the supplied ASIN.
    '''
    return self.by_identifier('mobi-asin', asin.upper())

  @typechecked
  def by_kobo_volumeid(self, uuid: Union[str, UUID]):
    ''' Return an iterable of `CalibreBook`s with the supplied Kobo volumeid.
    '''
    if isinstance(uuid, str):
      uuid = UUID(uuid)
    else:
      assert isinstance(uuid, UUID)
    return self.by_identifier('kobo-volumeid', str(uuid).lower())

  def _run(self, calcmd, *calargv, doit=True, quiet=False, **subp_options):
    ''' Run a Calibre utility command.
        Return the `CompletedProcess` result.

        Parameters:
        * `calcmd`: the Calibre command to invoke;
          if the command name is not an absolute path
          it is expected to come from `self.bin_dirpath`
        * `calargv`: the arguments for the command
        * `doit`: default `True`; do not run the command of false
        * `quiet`: default `False`; if true, do not print the command or its output
        * `subp_options`: optional mapping of keyword arguments
          to pass to `subprocess.run`
    '''
    subp_options.setdefault('capture_output', True)
    subp_options.setdefault('check', False)
    subp_options.setdefault('text', True)
    if not isabspath(calcmd):
      calcmd = joinpath(self.bin_dirpath, calcmd)
    calargv = [calcmd, *calargv]
    cp = run(calargv, doit=doit, quiet=quiet, print=print, **subp_options)
    if cp is not None and cp.stdout and not quiet:
      print(" ", cp.stdout.rstrip().replace("\n", "\n  "))
    return cp

  def by_spec(self, book_spec):
    ''' A generator yielding `CalibreBook` instances from `book_spec`.

        Book specifications:
        - dbid: an integer Calibre book id
        - FORMAT: an upper case format name, eg EPUB
        - /regexp: a case insensitive regular expression matching book
          authors, titles series names or tags
        - [identfier,...=]value,...: values to match against a Calibre
          identifier such as asin
    '''
    # raw dbid
    try:
      dbid = int(book_spec)
    except ValueError:
      # FORMAT
      if book_spec.isupper():
        # TODO: fast search of book formats? like identifiers
        match_fn = lambda book: book_spec in book.formats
      # /regexp
      elif book_spec.startswith('/'):
        re_s = book_spec[1:]
        if not re_s:
          raise ValueError("empty regexp")  # pylint: disable=raise-missing-from
        regexp = re.compile(re_s, re.I)
        match_fn = lambda book: (
            regexp.search(book.title) or any(
                map(regexp.search, book.author_names)
            ) or regexp.search(book.series_name or "") or
            any(map(regexp.search, book.tags))
        )
      else:
        # [identifier=]id-value,...
        try:
          identifiers_s, values_s = book_spec.split('=', 1)
        except ValueError:
          # id-value,...
          identifiers = None
          values_s = book_spec
        else:
          identifiers = identifiers_s.split(',')
        values = list(map(str.lower, values_s.split(',')))
        if identifiers:
          # fast search by identifier fields
          yield from unrepeated(
              chain(
                  *(
                      self.by_identifier(identifier, value)
                      for identifier in identifiers
                      for value in values
                  )
              )
          )
          return
        match_fn = lambda book: any(
            (
                (identifiers is None or idk in identifiers) and idv.lower() in
                values
            ) for idk, idv in book.identifiers.items()
        )
      # slow search by arbitrary match_fn
      for book in self.values():
        if match_fn(book):
          yield book
    else:
      # integer dbid
      yield self[dbid]

  def calibredb(self, dbcmd, *argv, doit=True, quiet=False, **subp_options):
    ''' Run `dbcmd` via the `calibredb` command.
        Return a `CompletedProcess` or `None` if `doit` is false.
    '''
    subp_argv = [
        'calibredb',
        dbcmd,
        '--library-path=' + self.fspath,
        *argv,
    ]
    return self._run(*subp_argv, doit=doit, quiet=quiet, **subp_options)

  def ebook_convert(
      self,
      srcpath,
      dstpath,
      *conv_opts,
      doit=True,
      quiet=False,
      **subp_options
  ):
    ''' Run `dbcmd` via the `calibredb` command.
        Return a `CompletedProcess` or `None` if `doit` is false.
    '''
    if not isfilepath(srcpath):
      raise ValueError("source path is not a file: %r" % (srcpath,))
    if existspath(dstpath):
      raise ValueError("destination path already exists: %r" % (dstpath,))
    subp_argv = [
        'ebook-convert',
        srcpath,
        dstpath,
        *conv_opts,
    ]
    return self._run(*subp_argv, doit=doit, quiet=quiet, **subp_options)

  @uses_cmd_options(dedrm=None, doit=True, quiet=False)
  @pfx_method
  def add(
      self,
      bookpath,
      *,
      dedrm,
      doit,
      quiet,
      add_args=(),
      **subp_options,
  ):
    ''' Add a book file via the `calibredb add` command.
        Return the database id or `None` if `doit` is false or the command fails.

        Parameters:
        * `bookpath`: the filesystem path to the book
        * `dedrm`: optional `DeDRMWrapper` instance
        * `add_args`: optional iterable of additional `calibredb`
          command line arguments
    '''
    if dedrm is not None:
      # try to remove DRM from the book file
      # and add the cleared temporary copy
      if doit:
        with dedrm.decrypted(bookpath) as clearpath:
          # bookpath is not encrypted, use as is
          if clearpath is None:
            clearpath = bookpath
          return self.add(clearpath, doit=doit, quiet=quiet, **subp_options)
    cp = self.calibredb(
        'add',
        '--duplicates',
        *add_args,
        bookpath,
        doit=doit,
        quiet=quiet,
        stdin=DEVNULL,
        capture_output=True,
        text=True,
        **subp_options,
    )
    if cp is None:
      return None
    if cp.returncode != 0:
      return None
    # Extract the database id from the "calibredb add" output.
    dbids = []
    output_lines = cp.stdout.split('\n')
    for line in output_lines:
      line_sfx = cutprefix(line, 'Added book ids:')
      if line_sfx is not line:
        dbids.extend(
            map(lambda dbid_s: int(dbid_s.strip()), line_sfx.split(','))
        )
    if not dbids:
      warning(
          "calibredb add %r failed\n  Output:\n    %s", bookpath,
          "    \n".join(output_lines)
      )
      return None
    dbid, = dbids  # pylint: disable=unbalanced-tuple-unpacking
    self.refresh_dbid(dbid)
    return dbid

# pylint: disable=too-many-instance-attributes
class CalibreMetadataDB(ORM):
  ''' An ORM to access the Calibre `metadata.db` SQLite database.
  '''

  DB_FILENAME = 'metadata.db'

  def __init__(self, tree, db_url=None, readwrite=False):
    if isinstance(tree, str):
      tree = CalibreTree(tree)
    self.tree = tree
    if db_url is None:
      db_path = (
          os.environ.get('CALIBRE_OVERRIDE_DATABASE_PATH')
          or tree.pathto(self.DB_FILENAME)
      )
      db_url = 'sqlite:///' + db_path
    elif db_url.startswith('/'):
      db_path = db_url
      db_url = 'sqlite:///' + db_path
    elif db_url.startswith('~'):
      db_path = expanduser(db_url)
      db_url = 'sqlite:///' + db_path
    else:
      db_path = None
    self.db_path = db_path
    if not readwrite:
      db_url += '?immutable=1'
    super().__init__(db_url)

  @property
  def orm(self):
    ''' No distinct ORM class for `CalibreMetadataDB`.
    '''
    return self

  def shell(self):
    ''' Interactive db shell.
    '''
    run(['sqlite3', self.db_path], check=True, quiet=False, stdin=sys.stdin)
    return 0

  # lifted from SQLTags
  @contextmanager
  def session(self, *, new=False):
    ''' Context manager to obtain a db session if required
        (or if `new` is true).
    '''
    orm_state = self.orm.sqla_state
    get_session = orm_state.new_session if new else orm_state.auto_session
    with get_session() as session2:
      yield session2

  # pylint: disable=too-many-statements
  def declare_schema(self):
    r''' Define the database schema / ORM mapping.

        Database schema queried thus:

            sqlite3 ~/CALIBRE/metadata.db .schema
    '''
    Base = self.Base

    class _CalibreTable(BasicTableMixin, HasIdMixin):
      ''' Base class for Calibre tables.
      '''

    def _linktable(
        left_name, right_name, tablename=None, **additional_columns
    ):
      ''' Prepare and return a Calibre link table base class.

          Parameters:
          * `left_name`: the left hand entity, lowercase, singular,
            example `'book'`
          * `right_name`: the right hand entity, lowercase, singular,
            example `'author'`
          * `additional_columns`: other keyword parameters
            define further `Column`s and relationships
      '''
      if tablename is None:
        tablename = f'{left_name}s_{right_name}s_link'

      class linktable(_CalibreTable):
        ''' Prepare a `_CalibreTable` subclass representing a Calibre link table.
        '''
        __tablename__ = tablename

      def vsetattr(o, a, v):
        ##X("setattr(%r,%r,%r)", o, a, v)
        setattr(o, a, v)

      vsetattr(
          linktable, f'{left_name}_id',
          declared_attr(
              lambda self: Column(
                  left_name,
                  ForeignKey(f'{left_name}s.id'),
                  primary_key=True,
              )
          )
      )
      vsetattr(
          linktable, left_name,
          declared_attr(
              lambda self: relationship(
                  f'{left_name.title()}s',
                  back_populates=f'{right_name}_links',
                  lazy='joined',
              )
          )
      )
      vsetattr(
          linktable, f'{right_name}_id',
          declared_attr(
              lambda self: Column(
                  right_name,
                  ForeignKey(f'{right_name}s.id'),
                  primary_key=True,
              )
          )
      )
      vsetattr(
          linktable, right_name,
          declared_attr(
              lambda self: relationship(
                  f'{right_name.title()}s',
                  back_populates=f'{left_name}_links',
                  lazy='joined',
              )
          )
      )
      for colname, colspec in additional_columns.items():
        vsetattr(
            linktable,
            colname,
            declared_attr(lambda self, colspec=colspec: colspec),
        )
      return linktable

    @total_ordering
    class Authors(Base, _CalibreTable):
      ''' An author.
      '''
      __tablename__ = 'authors'
      name = Column(String, nullable=False, unique=True)
      sort = Column(String)
      link = Column(String, nullable=False, default="")

      def __hash__(self):
        return self.id

      def __eq__(self, other):
        return self.id == other.id

      def __lt__(self, other):
        return self.sort.lower() < other.sort.lower()

    @total_ordering
    class Books(Base, _CalibreTable):
      ''' A book.
      '''
      __tablename__ = 'books'
      title = Column(String, nullable=False, unique=True, default='unknown')
      sort = Column(String)
      timestamp = Column(DateTime)
      pubdate = Column(DateTime)
      series_index = Column(Float, nullable=False, default=1.0)
      author_sort = Column(String)
      isbn = Column(String, default="")
      lccn = Column(String, default="")
      path = Column(String, nullable=False, default="")
      flags = Column(Integer, nullable=False, default=1)
      uuid = Column(String)
      has_cover = Column(Boolean, default=False)
      last_modified = Column(
          DateTime,
          nullable=False,
          default=datetime(2000, 1, 1, tzinfo=timezone.utc)
      )

      def __hash__(self):
        return self.id

      def __eq__(self, other):
        return self.id == other.id

      def __lt__(self, other):
        return self.author_sort.lower() < other.author_sort.lower()

      def identifiers_as_dict(self):
        ''' Return a `dict` mapping identifier types to values.
        '''
        return {
            identifier.type: identifier.val
            for identifier in self.identifiers
        }

    class Data(Base, _CalibreTable):
      ''' Data files associated with a book.
      '''
      __tablename__ = 'data'
      book_id = Column(
          "book", ForeignKey('books.id'), nullable=False, primary_key=True
      )
      format = Column(String, nullable=False, primary_key=True)
      uncompressed_size = Column(Integer, nullable=False)
      name = Column(String, nullable=False)

    class Identifiers(Base, _CalibreTable):
      ''' Identifiers associated with a book such as `"isbn"` or `"mobi-asin"`.
      '''
      __tablename__ = 'identifiers'
      book_id = Column("book", ForeignKey('books.id'), nullable=False)
      type = Column(String, nullable=False, default="isbn")
      val = Column(String, nullable=None)

    class Languages(Base, _CalibreTable):
      ''' Lamguage codes.
      '''
      __tablename__ = 'languages'
      lang_code = Column(String, nullable=False, unique=True)

    class Preferences(Base, _CalibreTable):
      ''' Calibre preferences.
      '''
      __tablename__ = 'preferences'
      key = Column(String, nullable=False, unique=True)
      value = Column("val", String, nullable=False)

    class Series(Base, _CalibreTable):
      ''' Calibre preferences.
      '''
      __tablename__ = 'series'
      name = Column(String, nullable=False, unique=True)
      sort = Column(String, nullable=False)

    @total_ordering
    class Tags(Base, _CalibreTable):
      ''' A tag.
      '''
      __tablename__ = 'tags'
      name = Column(String, nullable=False, unique=True)

      def __hash__(self):
        return self.id

      def __eq__(self, other):
        return self.id == other.id

      def __lt__(self, other):
        return self.name.lower() < other.name.lower()

    class BooksAuthorsLink(Base, _linktable('book', 'author')):
      ''' Link table between `Books` and `Authors`.
      '''

    class BookSeriesLink(Base, _CalibreTable):
      ''' Link table between `Books` and `Series`.
      '''
      __tablename__ = 'books_series_link'
      book = Column('book', ForeignKey('books.id'))
      series = Column('series', ForeignKey('series.id'))

    class BooksTagsLink(Base, _linktable('book', 'tag')):
      ''' Link table between `Books` and `Tags`.
      '''

    ##class BooksLanguagesLink(Base, _linktable('book', 'lang_code')):
    ##  item_order = Column(Integer, nullable=False, default=1)

    Authors.book_links = relationship(BooksAuthorsLink)
    Authors.books = association_proxy('book_links', 'book')

    Books.author_links = relationship(BooksAuthorsLink)
    Books.authors = association_proxy('author_links', 'author')
    Books.formats = relationship(Data, backref="book")
    Books.identifiers = relationship(Identifiers)
    Books.series_links = relationship(BookSeriesLink)
    Books.series = association_proxy('series_links', 'series')
    Books.tag_links = relationship(BooksTagsLink)
    Books.tags = association_proxy('tag_links', 'tag')

    ##Books.language_links = relationship(BooksLanguagesLink)
    ##Books.languages = association_proxy('languages_links', 'languages')

    Identifiers.book = relationship(Books, back_populates="identifiers")

    Tags.book_links = relationship(BooksTagsLink)
    Tags.books = association_proxy('book_links', 'book')

    # references to table definitions
    self.authors = Authors
    Authors.orm = self
    self.books = Books
    Books.orm = self
    self.identifiers = Identifiers
    Identifiers.orm = self
    self.languages = Languages
    Languages.orm = self
    self.preferences = Preferences
    Preferences.orm = self
    self.series = Series
    Series.orm = self
    self.tags = Tags
    Tags.orm = self

class CalibreCommand(EBooksCommonBaseCommand):
  ''' Command line tool to interact with a Calibre filesystem tree.
  '''

  USAGE_FORMAT = '\n'.join(
      (
          'Usage: {cmd} subcommand [...]',
          *(
              stripped_dedent(paragraph, "  ") for paragraph in (
                  ''' Operate on a Calibre library.
                  ''',
                  # by_spec docs
                  CalibreTree.by_spec.__doc__.split("\n\n")[1],
              )
          )
      ),
  )

  SUBCOMMAND_ARGV_DEFAULT = 'info'

  DEFAULT_LINK_IDENTIFIER = 'mobi-asin'

  DEFAULT_LINKTO_DIRPATH = '~/media/books'
  DEFAULT_LINKTO_DIRPATH_ENVVAR = 'MEDIA_BOOKSPATH'
  DEFAULT_LINKTO_FORMATS = ['CBZ', 'EPUB']
  DEFAULT_LINKTO_SELECTORS = ['CBZ', 'EPUB']
  DEFAULT_LINKTO_SERIES_FORMAT = '{series.name:lc_}--{series.index}--{title:lc_}'
  DEFAULT_LINKTO_NOSERIES_FORMAT = '{title:lc_}'

  # mapping of target format key to source format and extra options
  CONVERT_MAP = {
      'EPUB': (['MOBI', 'AZW', 'AZW3'], ()),
  }

  @dataclass
  class Options(EBooksCommonBaseCommand.Options):
    ''' Special class for `self.options` with various properties.
    '''

    calibre_path_other: Optional[str] = None
    linkto_dirpath: Optional[str] = None
    # used by "calibre add [--cbz]"
    make_cbz: bool = False

    @cached_property
    def calibre_other(self):
      ''' The alternate `CalibreTree` from `self.calibre_path_other`.
      '''
      if self.calibre_path_other is None:
        self.calibre_path_other = os.environ.get(OTHER_LIBRARY_PATH_ENVVAR)
        if self.calibre_path_other is None:
          raise AttributeError(
              f'.calibre_other: no .calibre_path_other and no ${OTHER_LIBRARY_PATH_ENVVAR}'
          )
      return CalibreTree(self.calibre_path_other)

    COMMON_OPT_SPECS = ChainMap(
        dict(
            O_=(
                'calibre_path_other',
                f'Path to secondary Calibre library, default from ${OTHER_LIBRARY_PATH_ENVVAR}.',
                str,
                isdirpath,
                'not a directory',
            ),
        ),
        EBooksCommonBaseCommand.Options.COMMON_OPT_SPECS,
    )

  @contextmanager
  def run_context(self):
    ''' Prepare the `SQLTags` around each command invocation.
    '''
    with super().run_context():
      options = self.options
      if options.calibre_path_other is None:
        options.calibre_path_other = os.environ.get(OTHER_LIBRARY_PATH_ENVVAR)
      if options.linkto_dirpath is None:
        options.linkto_dirpath = os.environ.get(
            CalibreCommand.DEFAULT_LINKTO_DIRPATH_ENVVAR
        ) or expanduser(CalibreCommand.DEFAULT_LINKTO_DIRPATH)
      with options.calibre:
        yield

  def books_from_spec(self, book_spec) -> Iterable["CalibreBook"]:
    ''' A generator yielding `CalibreBook` instances from `book_spec`.
    '''
    return self.options.calibre.by_spec(book_spec)

  @staticmethod
  def cbook_default_sortkey(cbook):
    ''' The default presentation order for things like "ls":
        series-name-index,title,authors,dbid.
    '''
    return (
        (cbook.series_name.lower(),
         cbook.series_index) if cbook.series_name else ("", 0),
        cbook.title.lower(),
        tuple(map(str.lower, cbook.author_names)),
        cbook.dbid,
    )

  def popbooks(self, argv, once=False, sortkey=None, reverse=False):
    ''' Consume `argv` as book specifications and return a list of matching books.

        If `once` is true (default `False`) consume only the first argument.
        If `sortkey` is not `None`, sort the returned books by this function.
    '''
    options = self.options
    cbooks = []
    while argv:
      book_spec = self.poparg(argv, "book_spec")
      cbooks.extend(self.books_from_spec(book_spec))
      if once:
        break
    if sortkey is not None and sortkey is not False:
      if sortkey is True:
        sortkey = self.cbook_default_sortkey
      cbooks = sorted(cbooks, key=sortkey, reverse=reverse)
    return cbooks

  @popopts(cbz='Also make a CBZ file.')
  def cmd_add(self, argv):
    ''' Usage: {cmd} [--cbz] bookpaths...
          Add the specified ebook bookpaths to the library.
    '''
    if not argv:
      raise GetoptError("missing bookpaths")
    options = self.options
    calibre = options.calibre
    for bookpath in argv:
      with Pfx(bookpath):
        dbid = calibre.add(
            bookpath,
            doit=options.doit,
            quiet=options.quiet,
        )
        if options.make_cbz and options.doit:
          cbook = calibre[dbid]
          pfx_call(cbook.make_cbz)

  # pylint: disable=too-many-branches,too-many-locals
  @popopts(
      f=('force', 'Force: convert even if the format is already present.')
  )
  @uses_runstate
  def cmd_convert(self, argv, *, runstate: RunState):
    ''' Usage: {cmd} [-f] formatkey dbids...
          Convert books to the format `formatkey`.
    '''
    dstfmtk = self.poparg(argv).upper()
    srcfmtks, conv_opts = self.CONVERT_MAP.get(dstfmtk, ([], ()))
    if not srcfmtks:
      raise GetoptError(
          "no source formats can produce formatkey %r" % (dstfmtk,)
      )
    if not argv:
      raise GetoptError("missing dbids")
    try:
      cbooks = self.popbooks(argv)
    except ValueError as e:
      raise GetoptError("invalid book specifiers: %s") from e
    xit = 0
    options = self.options
    doit = options.doit
    force = options.force
    quiet = options.quiet
    verbose = options.verbose
    for cbook in cbooks:
      runstate.raiseif()
      with Pfx(cbook):
        if dstfmtk in cbook.formats:
          if force:
            # pylint: disable=expression-not-assigned
            verbose and warning("replacing format %r")
          else:
            # pylint: disable=expression-not-assigned
            qvprint(f"{cbook}: format {dstfmtk!r} already present")
            continue
        for srcfmtk in srcfmtks:
          if srcfmtk in cbook.formats:
            break
        else:
          srcfmtk = None
        if srcfmtk is None:
          warning(
              "no suitable source formats (%r); I looked for %r",
              sorted(cbook.formats.keys()), srcfmtks
          )
          xit = 1
          continue

      cbook.convert(srcfmtk, dstfmtk, *conv_opts, doit=doit, quiet=quiet)

    if runstate.cancelled:
      xit = 1
    return xit

  def cmd_dbshell(self, argv):
    ''' Usage: {cmd}
          Start an interactive database prompt.
    '''
    if argv:
      raise GetoptError(f'extra arguments: {argv!r}')
    return self.options.calibre.dbshell()

  def cmd_decrypt(self, argv):
    ''' Usage: {cmd} [dbids...]
          Remove DRM from the specified books.
    '''
    options = self.options
    calibre = options.calibre
    dedrm = options.dedrm
    if not self.options.dedrm:
      warning("no DeDRM available")
      return 1
    if not argv:
      argv = ['AZW', 'AZW3']
    try:
      cbooks = self.popbooks(argv)
    except ValueError as e:
      raise GetoptError("invalid book specifiers: %s") from e
    runstate = self.options.runstate
    xit = 0
    with run_task("decrypt") as proxy:
      for cbook in progressbar(list(unrepeated(cbooks)), "decrypt"):
        runstate.raiseif()
        proxy.text = str(cbook)
        with Pfx(cbook):
          for fmtk in 'AZW', 'AZW3':
            proxy.text = f'{cbook} {fmtk}'
            try:
              if cbook.decrypt(fmtk):
                print("decrypted", fmtk, "of", cbook)
            except KeyError:
              continue
            except dedrm.DeDRMError as e:
              warning("count not decrypt: %s", e)
              xit = 1
    return xit

  @popopts(
      _1=('first_format', 'Link only the first format found.'),
      d_=(
          'linkto_dirpath',
          '''Specify the target directory, default from
              ${DEFAULT_LINKTO_DIRPATH_ENVVAR} or {DEFAULT_LINKTO_DIRPATH}.''',
      ),
      F_=('formats', 'Source formats, default: {DEFAULT_LINKTO_FORMATS}'),
      f=('force', 'Force. Replace existing links.'),
      o_=('link_format', 'Link name format.'),
  )
  @uses_runstate
  def cmd_linkto(self, argv, *, runstate: RunState):
    ''' Usage: {cmd} [-1f] [-d linkto-dir] [-F fmt,...] [-o link-format] [dbids...]
          Export books to linkto-dir by hard linking.
    '''
    ##Default with series: {DEFAULT_LINKTO_SERIES_FORMAT}
    ##Default without series: {DEFAULT_LINKTO_NOSERIES_FORMAT}
    options = self.options
    if options.formats is None:
      options.formats = self.DEFAULT_LINKTO_FORMATS
    doit = options.doit
    first_format = options.first_format
    force = options.force
    calibre = options.calibre
    formats = options.formats
    if isinstance(formats, str):
      # pylint: disable=no-member
      formats = [fmt.strip().upper() for fmt in formats.split(',')]
    link_format = options.link_format
    linkto_dirpath = options.linkto_dirpath
    quiet = options.quiet
    verbose = options.verbose
    qvprint(
        "linkto", calibre.shortpath, "=>", shortpath(options.linkto_dirpath)
    )
    cbooks = sorted(
        set(self.popbooks(argv or list(self.DEFAULT_LINKTO_SELECTORS))),
        key=lambda cbook: cbook.title.lower()
    )
    for cbook in progressbar(cbooks, "linkto"):
      with run_task(options.cmd) as proxy:
        runstate.raiseif()
        proxy.text = str(cbook)
        with Pfx(cbook):
          fmttags = cbook.format_tagset()
          series_name = fmttags.get('series.name')
          if series_name:
            series_name = series_name.strip()
          name_format = link_format or (
              '{series.name:lc_}--{series.index}--{title:lc_}'
              if series_name else '{title:lc_}'
          )
          name = (
              cbook.format_as(name_format).replace('_', '-').replace('/', ':')
          )
          name_subdir = (
              lc_(series_name).replace('/', ':')
              if series_name and not link_format else ''
          )
          for fmt in formats:
            runstate.raiseif()
            proxy.text = f'{cbook}: {fmt}'
            srcpath = cbook.formatpath(fmt)
            if srcpath is None:
              continue
            dstpath = joinpath(
                linkto_dirpath, name_subdir, name + '.' + fmt.lower()
            )
            if existspath(dstpath):
              if force:
                warning("dst already exists, will be replaced: %s", dstpath)
                (verbose or not doit) and print("unlink", shortpath(dstpath))
                doit and pfx_call(os.unlink, dstpath)
              else:
                ##warning("dst already exists, skipped: %s", dstpath)
                continue
            dstdir = dirname(dstpath)
            if not isdirpath(dstdir) and doit:
              qvprint("mkdir", shortpath(dstdir))
              pfx_call(os.mkdir, dstdir)
            (quiet and doit
             ) or print("link", shortpath(srcpath), '=>', shortpath(dstpath))
            doit and pfx_call(os.link, srcpath, dstpath)
            if first_format:
              break
          proxy.text = f'{cbook}'
    return 0

  # pylint: disable=too-many-locals
  @popopts(
      l=('longmode', 'Long mode, listing book details over several lines.'),
      o_=('ls_format', 'Output format for use in a single line book listing.'),
      r=('sort_reverse', 'Reverse the listing order.'),
      t=('sort_timestamp', 'Order listing by timestamp.'),
  )
  @uses_runstate
  def cmd_ls(self, argv, *, runstate: RunState):
    ''' Usage: {cmd} [-l] [-o ls-format] [book_specs...]
          List the contents of the Calibre library.
    '''
    options = self.options
    if options.sort_timestamp:
      cbook_sort_key = lambda cbook: cbook.timestamp
    else:
      cbook_sort_key = self.cbook_default_sortkey
    longmode = options.longmode
    ls_format = options.ls_format
    calibre = options.calibre
    verbose = options.verbose
    xit = 0
    cbooks = []
    with calibre.db_session():
      calibre.preload()
      if argv:
        try:
          cbooks = self.popbooks(
              argv, sortkey=cbook_sort_key, reverse=options.sort_reverse
          )
        except ValueError as e:
          raise GetoptError("invalid book specifiers: %s") from e
      else:
        with contextif(verbose, run_task, "sort calibre contents"):
          cbooks = sorted(
              calibre.books(),
              key=cbook_sort_key,
              reverse=options.sort_reverse
          )
      for cbook in cbooks:
        runstate.raiseif()
        with Pfx(cbook):
          if ls_format is None:
            top_row = []
            series_name = cbook.series_name
            if series_name:
              top_row.append(f"{series_name} [{intif(cbook.series_index)}]")
            top_row.append(cbook.title)
            author_names = cbook.author_names
            if author_names:
              top_row.extend(
                  ("by", ", ".join(sorted(cbook.author_names, key=str.lower)))
              )
            top_row.append(f"({cbook.dbid})")
            if not longmode:
              top_row.append(",".join(sorted(map(str.upper, cbook.formats))))
              top_row.append(",".join(sorted(map(str.lower, cbook.tags))))
            print(*top_row)
          else:
            try:
              output = cbook.format_as(ls_format, error_sep='\n  ')
            except FormatAsError as e:
              error(str(e))
              xit = 1
              continue
            print(output)
          if longmode:
            print(" ", cbook.path)
            print(" ", shortpath(cbook.shortpath))
            tags = cbook.tags
            if tags:
              print("   ", ", ".join(sorted(tags)))
            identifiers = cbook.identifiers
            if identifiers:
              print("   ", TagSet(identifiers))
            for fmt, subpath in cbook.formats.items():
              with Pfx(fmt):
                fspath = cbook.pathto(subpath)
                try:
                  size = pfx_call(os.stat, fspath).st_size
                except OSError as e:
                  warning("cannot stat: %s", e)
                else:
                  print(f"    {fmt:4s}", transcribe_bytes_geek(size), subpath)
    return xit

  @uses_runstate
  def cmd_make_cbz(self, argv, *, runstate: RunState):
    ''' Usage: {cmd} book_specs...
          Add the CBZ format to the designated Calibre books.
    '''
    if not argv:
      raise GetoptError("missing book_specs")
    xit = 0
    while argv and not runstate.cancelled:
      with Pfx(argv[0]):
        try:
          cbooks = self.popbooks(argv, once=True)
        except ValueError as e:
          warning("invalid book specifications: %s", e)
          xit = 2
          continue
        for cbook in cbooks:
          runstate.raiseif()
          with Pfx(cbook):
            try:
              pfx_call(cbook.make_cbz)
            except ValueError as e:
              warning("cannot make CBZ from %s: %s" % (cbook, e))
              xit = 1
    return xit

  # pylint: disable=too-many-branches
  def cmd_prefs(self, argv):
    ''' Usage: {cmd}
          List the library preferences.
    '''
    if argv:
      raise GetoptError(f'extra arguments: {argv!r}')
    xit = 0
    db = self.options.calibre.db
    with db.session() as session:
      if argv:
        for pref_name in argv:
          with Pfx(pref_name):
            pref = db.preferences.lookup1(key=pref_name, session=session)
            if pref is None:
              warning("unknown preference")
              xit = 1
            else:
              print(pref_name)
              print(" ", json.dumps(pfx_call(json.loads, pref.value)))
      else:
        for pref in sorted(db.preferences.lookup(session=session),
                           key=lambda pref: pref.key):
          with Pfx(pref.key):
            print(pref.key)
            value = pfx_call(json.loads, pref.value)
            if isinstance(value, list):
              if value:
                for item in value:
                  print(" ", json.dumps(item))
            elif isinstance(value, dict):
              for k, v in sorted(value.items()):
                print(" ", json.dumps(k), ":", json.dumps(v))
            else:
              print(" ", json.dumps(value))
    return xit

  # pylint: disable=too-many-branches,too-many-locals,too-many-statements
  @popopts(
      f=(
          'force',
          'Force. Overwrite existing formats with formats from other-library.'
      )
  )
  @uses_runstate
  def cmd_pull(self, argv, *, runstate: RunState):
    ''' Usage: {cmd} [-f] [/path/to/other-library] [identifiers...]
          Import formats from another Calibre library.
          /path/to/other-library: optional path to another Calibre library tree
          identifier-name: the key on which to link matching books;
            the default is {DEFAULT_LINK_IDENTIFIER}
            If the identifier '?' is specified the available
            identifiers in use in other-library are listed.
          identifier-values: specific book identifiers to import
            If no identifiers are provided, all books which have
            the specified identifier will be pulled.
    '''
    options = self.options
    calibre = options.calibre
    if argv and argv[0].startswith('/') and isdirpath(argv[0]):
      options.calibre_path_other = argv.pop(0)
    doit = options.doit
    force = options.force
    quiet = options.quiet
    verbose = options.verbose
    other_library = options.calibre_other
    qvprint("pull", other_library.shortpath, "=>", calibre.shortpath)
    with contextif(
        not quiet,
        run_task,
        "pull " + shlex.join(argv),
        report_print=verbose,
    ):
      with Pfx(other_library.shortpath):
        with other_library:
          if other_library is calibre:
            raise GetoptError("cannot import from the same library")
          if argv:
            identifier_name = argv.pop(0)
          else:
            identifier_name = self.DEFAULT_LINK_IDENTIFIER
          if identifier_name == '?':
            if argv:
              warning(
                  "ignoring extra arguments after identifier-name=?: %r", argv
              )
            print("Default identifier:", self.DEFAULT_LINK_IDENTIFIER)
            print("Available idenitifiers in %s:" % (other_library,))
            for identifier_name in sorted(other_library.identifier_names()):
              print(" ", identifier_name)
            return 0
          with contextif(
              not quiet,
              run_task,
              f'scan identifiers from {other_library}...',
              report_print=verbose,
          ):
            obooks_map = {
                idv: obook
                for idv, obook in (
                    (obook.identifiers.get(identifier_name), obook)
                    for obook in other_library
                )
                if idv is not None
            }
          if not obooks_map:
            raise GetoptError(
                "no books have the identifier %r; identifiers in use are: %s" %
                (
                    identifier_name,
                    ', '.join(sorted(other_library.identifier_names()))
                )
            )
          if argv:
            identifier_values = argv
          else:
            identifier_values = [
                idv for idv, obook in sorted(
                    obooks_map.items(),
                    key=lambda id_ob:
                    (id_ob[1].title, id_ob[1].author_sort, id_ob[1].dbid)
                )
            ]
          xit = 0
          calibre.preload()
          with UpdProxy(prefix="pull " + other_library.shortpath + ": "
                        ) as proxy:
            for identifier_value in progressbar(identifier_values, "pull " +
                                                other_library.shortpath):
              runstate.raiseif()
              with Pfx.scope("%s=%s", identifier_name, identifier_value):
                try:
                  obook = obooks_map[identifier_value]
                except KeyError:
                  warning("unknown")
                  xit = 1
                  continue
                with proxy.extend_prefix(
                    "%s=%s: %s" % (identifier_name, identifier_value, obook)):
                  if not obook.formats:
                    # pylint: disable=expression-not-assigned
                    qvprint("no formats to pull")
                    continue
                  cbooks = list(
                      calibre.by_identifier(identifier_name, identifier_value)
                  )
                  if not cbooks:
                    # new book
                    fmtk = list(obook.formats.keys())[0]
                    ofmtpath = obook.formatpath(fmtk)
                    # pylint: disable=expression-not-assigned
                    quiet or (
                        print(
                            "new book from %s:%s <= %s" %
                            (fmtk, obook, shortpath(ofmtpath))
                        ) if verbose else
                        print("new book from %s:%s" % (fmtk, obook))
                    )
                    dbid = calibre.add(ofmtpath, doit=doit, quiet=quiet)
                    if not doit:
                      # we didn't make a new book, so move to the next one
                      continue
                    if dbid is None:
                      error("calibre add failed")
                      xit = 1
                    else:
                      cbook = calibre[dbid]
                      # pylint: disable=expression-not-assigned
                      qvprint('new', cbook, '<=', obook)
                  elif len(cbooks) > 1:
                    # pylint: disable=expression-not-assigned
                    verbose and warning(
                        "  \n".join(
                            [
                                'multiple "local" books with this identifier:',
                                *map(str, cbooks)
                            ]
                        )
                    )
                    continue
                  else:
                    cbook, = cbooks
                  cbook.pull(
                      obook,
                      doit=doit,
                      force=force,
                      quiet=quiet,
                      verbose=verbose
                  )
          return xit

  def cmd_shell(self, argv):
    ''' Usage: {cmd}
          Run a command prompt via cmd.Cmd using calibre's subcommands.
          Run an interactive Python prompt with some predefined names:
            calibre: the CalibreTree
            options: self.options
    '''
    if argv:
      raise GetoptError(f'extra arguments: {argv!r}')
    options = self.options
    interact(
        banner=f'{self.cmd}: {options.calibre}',
        local=dict(
            calibre=options.calibre,
            options=options,
        )
    )

  def cmd_tag(self, argv):
    ''' Usage: {cmd} [--] [-]tag[,tag...] book_specs...
          Modify the tags of the specified books.
          Note the required -- separator if you are removing a tag with "-tag".
    '''
    options = self.options
    options.popopts(argv)
    doit = options.doit
    tags = self.poparg(argv, "tags")
    add_mode = True
    if tags.startswith('-'):
      add_mode = False
      tags = tags[1:]
    tag_names = sorted(map(str.lower, filter(None, tags.split(','))))
    if not tag_names:
      raise GetoptError("no tags specified")
    if not argv:
      raise GetoptError("missing book_specs")
    cbooks = self.popbooks(argv)
    with run_task(options.cmd) as proxy:
      for cbook in cbooks:
        proxy.text = f'{cbook} {cbook.tags}'
        tags = set(cbook.tags)
        new_tags = set(cbook.tags)
        for tag_name in tag_names:
          if add_mode:
            new_tags.add(tag_name)
          else:
            new_tags.discard(tag_name)
        if new_tags != tags:
          if add_mode:
            print(cbook, '+', sorted(new_tags - tags))
          else:
            print(cbook, '-', sorted(tags - new_tags))
          if doit:
            cbook.tags = new_tags

if __name__ == '__main__':
  sys.exit(CalibreCommand(sys.argv).run())
