#!/usr/bin/env python3
#
# pylint: disable=too-many-lines

''' Support for Calibre libraries.
'''

from contextlib import contextmanager
from datetime import datetime, timezone
import filecmp
from functools import lru_cache, total_ordering
from getopt import GetoptError
from itertools import chain
import json
import os
from os.path import (
    basename,
    isabs as isabspath,
    join as joinpath,
    splitext,
)
import shlex
from subprocess import DEVNULL
import sys
from tempfile import TemporaryDirectory

from icontract import require
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
from sqlalchemy.orm import declared_attr, relationship
from typeguard import typechecked

from cs.cmdutils import BaseCommand
from cs.context import stackattrs
from cs.deco import cachedmethod
from cs.fs import FSPathBasedSingleton, HasFSPath, shortpath
from cs.lex import cutprefix
from cs.logutils import warning
from cs.obj import SingletonMixin
from cs.pfx import Pfx, pfx_call, pfx_method
from cs.progress import progressbar
from cs.resources import MultiOpenMixin
from cs.sqlalchemy_utils import (
    ORM, BasicTableMixin, HasIdMixin, RelationProxy
)
from cs.tagset import TagSet
from cs.threads import locked
from cs.units import transcribe_bytes_geek
from cs.upd import Upd, UpdProxy, print  # pylint: disable=redefined-builtin

from . import run

class CalibreTree(FSPathBasedSingleton, MultiOpenMixin):
  ''' Work with a Calibre ebook tree.
  '''

  FSPATH_DEFAULT = '~/CALIBRE'
  FSPATH_ENVVAR = 'CALIBRE_LIBRARY'

  CALIBRE_BINDIR_DEFAULT = '/Applications/calibre.app/Contents/MacOS'

  # pylint: disable=too-many-statements
  def __init__(self, calibrepath):
    super().__init__(calibrepath)

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
    ]), HasFSPath):
      ''' A reference to a book in a Calibre library.
      '''

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
        ''' An alias for `self.path`.
        '''
        return self.path

      @property
      def dbid(self):
        ''' An alias for the `.id` attribute.
        '''
        return self.id

      @property
      def asin(self):
        ''' The Amazon ASIN, or `None`, from `self.identifiers['mobi-asin'].
        '''
        return self.identifiers.get('mobi-asin', None)

      @classmethod
      def refresh_from_db_row(cls, db_row, fields, *, session):
        ''' Refresh the cached values from the database.
        '''
        super().refresh_from_db_row(db_row, fields, session=session)
        fields['authors'] = db_row.authors
        fields['formats'] = {
            fmt.format:
            joinpath(db_row.path, f'{fmt.name}.{fmt.format.lower()}')
            for fmt in db_row.formats
        }
        fields['identifiers'] = {
            identifier.type: identifier.val
            for identifier in db_row.identifiers
        }
        fields['tags'] = [tag.name for tag in db_row.tags]

      def formatpath(self, fmtk):
        ''' Return the filesystem path of the format file for `fmtk`
            or `None` if the format is not present.
        '''
        try:
          subpath = self.formats[fmtk]
        except KeyError:
          return None
        return self.tree.pathto(subpath)

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
        if cp.return_code != 0:
          warning("command fails, return code %d", cp.return_code)
          return False
        self.refresh_from_db()
        return True

      @property
      def mobipath(self):
        ''' The filesystem path of a Mobi format book file, or `None`.
        '''
        for fmtk in 'MOBI', 'AZW3', 'AZW':
          fmtpath = self.formatpath(fmtk)
          if fmtpath is not None:
            return fmtpath
        return None

      def make_cbz(self, replace_format=False):
        ''' Create a CBZ format from the AZW3 Mobi format.
        '''
        from .mobi import Mobi  # pylint: disable=import-outside-toplevel
        formats = self.formats
        if 'CBZ' in formats and not replace_format:
          warning("format CBZ already present, not adding")
        else:
          mobipath = self.mobipath
          if mobipath:
            base, _ = splitext(basename(mobipath))
            MB = Mobi(mobipath)
            with TemporaryDirectory() as tmpdirpath:
              cbzpath = joinpath(tmpdirpath, base + '.cbz')
              pfx_call(MB.make_cbz, cbzpath)
              self.add_format(cbzpath, force=replace_format)
          else:
            raise ValueError(
                "no AZW3, AZW or MOBI format from which to construct a CBZ"
            )

      def pull(
          self,
          obook,
          *,
          doit=True,
          formats=None,
          runstate=None,
          force=False,
          quiet=False,
          verbose=False,
      ):
        ''' Pull formats from another `CalibreBook`.

            Parameters:
            * `obook`: the other book
            * `doit`: optional flag, default `True`;
              import formats if true, report actions otherwise
            * `formats`: optional list of Calibre format keys to pull if present
            * `runstate`: optional `RunState` for early termination
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
            if runstate and runstate.cancelled:
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

      def pull_format(
          self,
          ofmtpath,
          *,
          fmtk=None,
          doit=True,
          force=False,
          quiet=False,
          verbose=False,
      ):
        ''' Pull formats from another `CalibreBook`.

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
            fmtpath = self.formatpath('AZW3') or self.formatpath('AZW')
          if fmtpath is not None and not force:
            if filecmp.cmp(fmtpath, ofmtpath):
              verbose and print(
                  self, fmtk, "identical to", shortpath(ofmtpath)
              )
            else:
              verbose and warning(
                  "already present with different content\n"
                  "  present: %s\n"
                  "  other:   %s",
                  shortpath(fmtpath),
                  shortpath(ofmtpath),
              )
            return
        # pylint: disable=expression-not-assigned
        quiet or print(self, '+', fmtk, '<=', shortpath(ofmtpath))
        self.add_format(ofmtpath, doit=doit, force=force, quiet=quiet)

    self.CalibreBook = CalibreBook

  def __str__(self):
    return "%s:%s" % (type(self).__name__, self.shortpath)

  @contextmanager
  def startup_shutdown(self):
    ''' Stub startup/shutdown.
    '''
    yield

  @property
  @locked
  @cachedmethod
  def db(self):
    ''' The associated `CalibreMetadataDB` ORM,
        instantiated on demand.
    '''
    return CalibreMetadataDB(self)

  def dbshell(self):
    ''' Interactive db shell.
    '''
    return self.db.shell()

  def preload(self):
    ''' Scan all the books, preload their data.
    '''
    with UpdProxy(text=f"preload {self}"):
      db = self.db
      with db.session() as session:
        for db_book in self.db.books.lookup(session=session):
          self.book_by_dbid(db_book.id, db_book=db_book)

  @typechecked
  def __getitem__(self, dbid: int):
    return self.book_by_dbid(dbid)

  def __contains__(self, dbid: int):
    db = self.db
    try:
      with db.session() as session:
        db.books.by_id(dbid, session=session)
    except IndexError:
      return False
    return True

  @lru_cache(maxsize=None)
  @typechecked
  @require(lambda dbid: dbid > 0)
  def book_by_dbid(self, dbid: int, *, db_book=None):
    ''' Return a cached `CalibreBook` for `dbid`.
    '''
    return self.CalibreBook(self, dbid, db_book=db_book)

  def __iter__(self):
    ''' Generator yielding `CalibreBook`s.
    '''
    db = self.db
    seen_dbids = set()
    with db.session() as session:
      for author in sorted(db.authors.lookup(session=session)):
        with Pfx("%d:%s", author.id, author.name):
          for book in sorted(author.books):
            if book.id in seen_dbids:
              continue
            yield self.book_by_dbid(book.id, db_book=book)
            seen_dbids.add(book.id)

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
      for identifier in db.identifiers.lookup(session=session, type=type_,
                                              val=value):
        yield self[identifier.book_id]

  def by_asin(self, asin):
    ''' Return an iterable of `CalibreBook`s with the supplied ASIN.
    '''
    return self.by_identifier('mobi-asin', asin.upper())

  def _run(self, calcmd, *calargv, doit=True, quiet=False, subp_options=None):
    ''' Run a Calibre utility command.
        Return the `CompletedProcess` result.

        Parameters:
        * `calcmd`: the Calibre command to invoke;
          if the command name is not an absolute path
          it is expected to come from `self.CALIBRE_BINDIR_DEFAULT`
        * `calargv`: the arguments for the command
        * `doit`: default `True`; do not run the command of false
        * `quiet`: default `False`; if true, do not print the command or its output
        * `subp_options`: optional mapping of keyword arguments
          to pass to `subprocess.run`
    '''
    if subp_options is None:
      subp_options = {}
    subp_options.setdefault('capture_output', True)
    subp_options.setdefault('check', False)
    subp_options.setdefault('text', True)
    if not isabspath(calcmd):
      calcmd = joinpath(self.CALIBRE_BINDIR_DEFAULT, calcmd)
    calargv = [calcmd, *calargv]
    return run(calargv, doit=doit, quiet=quiet, **subp_options)

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

  @pfx_method
  def add(self, bookpath, doit=True, quiet=False):
    ''' Add a book file via the `calibredb add` command.
        Return the database id or `None` if `doit` is false or the command fails.
    '''
    cp = self.calibredb(
        'add',
        '--duplicates',
        bookpath,
        subp_options=dict(stdin=DEVNULL, capture_output=True, text=True),
        doit=doit,
        quiet=quiet,
    )
    if cp is None:
      return None
    if cp.returncode != 0:
      return None
    # Extract the database id from the "calibredb add" output.
    dbids = []
    for line in cp.stdout.split('\n'):
      line_sfx = cutprefix(line, 'Added book ids:')
      if line_sfx is not line:
        dbids.extend(
            map(lambda dbid_s: int(dbid_s.strip()), line_sfx.split(','))
        )
    dbid, = dbids  # pylint: disable=unbalanced-tuple-unpacking
    return dbid

# pylint: disable=too-many-instance-attributes
class CalibreMetadataDB(ORM):
  ''' An ORM to access the Calibre `metadata.db` SQLite database.
  '''

  DB_FILENAME = 'metadata.db'

  def __init__(self, tree):
    if isinstance(tree, str):
      tree = CalibreTree(tree)
    self.tree = tree
    self.db_url = 'sqlite:///' + self.db_path
    super().__init__(self.db_url)

  @property
  def orm(self):
    ''' No distinct ORM class for `CalibreMetadataDB`.
    '''
    return self

  @property
  def db_path(self):
    ''' The filesystem path to the database.
    '''
    return self.tree.pathto(self.DB_FILENAME)

  def shell(self):
    ''' Interactive db shell.
    '''
    print("sqlite3", self.db_path)
    run(['sqlite3', self.db_path], check=True)
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

    def _linktable(left_name, right_name, **addtional_columns):
      ''' Prepare and return a Calibre link table base class.

          Parameters:
          * `left_name`: the left hand entity, lowercase, singular,
            example `'book'`
          * `right_name`: the right hand entity, lowercase, singular,
            example `'author'`
          * `addtional_columns`: other keyword parameters
            define further `Column`s and relationships
      '''

      class linktable(_CalibreTable):
        ''' Prepare a `_CalibreTable` subclass representing a Calibre link table.
        '''

        __tablename__ = f'{left_name}s_{right_name}s_link'

      setattr(
          linktable, f'{left_name}_id',
          declared_attr(
              lambda self: Column(
                  left_name,
                  ForeignKey(f'{left_name}s.id'),
                  primary_key=True,
              )
          )
      )
      setattr(
          linktable, left_name,
          declared_attr(
              lambda self: relationship(
                  f'{left_name.title()}s',
                  back_populates=f'{right_name}_links',
              )
          )
      )
      setattr(
          linktable, f'{right_name}_id',
          declared_attr(
              lambda self: Column(
                  right_name,
                  ForeignKey(f'{right_name}s.id'),
                  primary_key=True,
              )
          )
      )
      setattr(
          linktable, right_name,
          declared_attr(
              lambda self: relationship(
                  f'{right_name.title()}s',
                  back_populates=f'{left_name}_links',
              )
          )
      )
      for colname, colspec in addtional_columns.items():
        setattr(
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

    class BooksTagsLink(Base, _linktable('book', 'tag')):
      ''' Link table between `Books` and `Tags`.
      '''

    ##class BooksLanguagesLink(Base, _linktable('book', 'lang_code')):
    ##  item_order = Column(Integer, nullable=False, default=1)

    Authors.book_links = relationship(BooksAuthorsLink)
    Authors.books = association_proxy('book_links', 'book')

    Books.author_links = relationship(BooksAuthorsLink)
    Books.authors = association_proxy('author_links', 'author')
    Books.identifiers = relationship(Identifiers)
    Books.formats = relationship(Data, backref="book")
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
    self.tags = Tags
    Tags.orm = self

class CalibreCommand(BaseCommand):
  ''' Command line tool to interact with a Calibre filesystem tree.
  '''

  GETOPT_SPEC = 'C:K:'

  USAGE_FORMAT = '''Usage: {cmd} [-C calibre_library] [-K kindle-library-path] subcommand [...]
  -C calibre_library
    Specify calibre library location.
  -K kindle_library
    Specify kindle library location.'''

  SUBCOMMAND_ARGV_DEFAULT = 'info'

  DEFAULT_LINK_IDENTIFIER = 'mobi-asin'

  USAGE_KEYWORDS = {
      'DEFAULT_LINK_IDENTIFIER': DEFAULT_LINK_IDENTIFIER,
  }

  def apply_defaults(self):
    ''' Set up the default values in `options`.
    '''
    options = self.options
    options.kindle_path = None
    options.calibre_path = None

  def apply_opt(self, opt, val):
    ''' Apply a command line option.
    '''
    options = self.options
    if opt == '-C':
      options.calibre_path = val
    elif opt == '-K':
      options.kindle_path = val
    else:
      super().apply_opt(opt, val)

  @contextmanager
  def run_context(self):
    ''' Prepare the `SQLTags` around each command invocation.
    '''
    from .kindle import KindleTree  # pylint: disable=import-outside-toplevel
    options = self.options
    with KindleTree(options.kindle_path) as kt:
      with CalibreTree(options.calibre_path) as cal:
        with stackattrs(
            options,
            kindle=kt,
            calibre=cal,
        ):
          Upd().out('')
          yield

  def cmd_dbshell(self, argv):
    ''' Usage: {cmd}
          Start an interactive database prompt.
    '''
    if argv:
      raise GetoptError("extra arguments: %r" % (argv,))
    return self.options.calibre.dbshell()

  def cmd_info(self, argv):
    ''' Usage: {cmd}
          Report basic information.
    '''
    if argv:
      raise GetoptError("extra arguments: %r" % (argv,))
    print("calibre", self.options.calibre.shortpath)
    print("kindle", self.options.kindle.shortpath)

  def cmd_make_cbz(self, argv):
    ''' Usage: {cmd} dbids...
          Add the CBZ format to the designated Calibre books.
    '''
    if not argv:
      raise GetoptError("missing dbids")
    options = self.options
    calibre = options.calibre
    runstate = options.runstate
    xit = 0
    for dbid_s in argv:
      if runstate.cancelled:
        xit = 1
        break
      with Pfx(dbid_s):
        try:
          dbid = int(dbid_s)
        except ValueError as e:
          warning("invalid dbid: %s", e)
          xit = 1
          continue
        cbook = calibre[dbid]
        with Pfx("%s: make_cbz", cbook.title):
          cbook.make_cbz()
    return xit

  def cmd_ls(self, argv):
    ''' Usage: {cmd} [-l] [dbids...]
          List the contents of the Calibre library.
    '''
    options = self.options
    options.longmode = False
    options.popopts(argv, l='longmode')
    longmode = options.longmode
    calibre = options.calibre
    xit = 0
    cbooks = []
    if argv:
      while argv:
        dbid = self.popargv(argv, "dbid", int)
        if dbid not in calibre:
          warning("unknown dbid %d", dbid)
          xit = 1
          continue
        cbook = calibre[dbid]
        cbooks.append(cbook)
      assert not argv
    else:
      cbooks = calibre
      calibre.preload()
    runstate = options.runstate
    for cbook in cbooks:
      if runstate.cancelled:
        xit = 1
        break
      with Pfx("%d:%s", cbook.id, cbook.title):
        print(f"{cbook.title} ({cbook.dbid})")
        if longmode:
          print(" ", cbook.path)
          identifiers = cbook.identifiers
          if identifiers:
            print("   ", TagSet(identifiers))
          tags = cbook.tags
          if tags:
            print("   ", ", ".join(sorted(tags)))
          for fmt, subpath in cbook.formats.items():
            with Pfx(fmt):
              fspath = calibre.pathto(subpath)
              size = pfx_call(os.stat, fspath).st_size
              print("   ", fmt, transcribe_bytes_geek(size), subpath)
    return xit

  # pylint: disable=too-many-branches
  def cmd_prefs(self, argv):
    ''' Usage: {cmd}
          List the library preferences.
    '''
    if argv:
      raise GetoptError("extra arguments: %r" % (argv,))
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
  def cmd_pull(self, argv):
    ''' Usage: {cmd} [-fnqv] other-library [identifier-name [identifier-values...]]
          Import formats from another Calibre library.
          -f    Force. Overwrite existing formats with formats from other-library.
          -n    No action: recite planned actions.
          -q    Quiet. Only issue warnings and errors.
          -v    Verbose. Print more information.
          other-library: the path to another Calibre library tree
          identifier-name: the key on which to link matching books;
            the default is {DEFAULT_LINK_IDENTIFIER}
            If the identifier '?' is specified the available
            identifiers in use in other-library are listed.
          identifier-values: specific book identifiers to import
            If no identifiers are provided, all books which have
            the specified identifier will be pulled.
    '''
    Upd().out("pull " + shlex.join(argv))
    options = self.options
    calibre = options.calibre
    runstate = options.runstate
    self.popopts(argv, options, f='force', n='-doit', q='quiet', v='verbose')
    other_library = self.poparg(argv, "other-library", CalibreTree)
    doit = options.doit
    force = options.force
    quiet = options.quiet
    verbose = options.verbose
    with Pfx(other_library.shortpath):
      if other_library is calibre:
        raise GetoptError("cannot import from the same library")
      if argv:
        identifier_name = argv.pop(0)
      else:
        identifier_name = self.DEFAULT_LINK_IDENTIFIER
      if identifier_name == '?':
        if argv:
          warning("ignoring extra arguments after identifier-name=?: %r", argv)
        print("Default identifier:", self.DEFAULT_LINK_IDENTIFIER)
        print("Available idenitifiers in %s:" % (other_library,))
        for identifier_name in sorted(other_library.identifier_names()):
          print(" ", identifier_name)
        return 0
      with UpdProxy(text=f"scan identifiers from {other_library}..."):
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
            "no books have the identifier %r; identifiers in use are: %s" % (
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
      with UpdProxy(prefix="pull " + other_library.shortpath + ": ") as proxy:
        for identifier_value in progressbar(identifier_values,
                                            "pull " + other_library.shortpath):
          if runstate.cancelled:
            break
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
                verbose and print("no formats to pull")
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
                cbook = calibre[dbid]
                quiet or print('new', cbook, '<=', obook)
              elif len(cbooks) > 1:
                verbose or warning(
                    "  \n".join(
                        [
                            "multiple \"local\" books with this identifier:",
                            *map(str, cbooks)
                        ]
                    )
                )
                continue
              else:
                cbook, = cbooks
              cbook.pull(
                  obook,
                  runstate=runstate,
                  doit=doit,
                  force=force,
                  quiet=quiet,
                  verbose=verbose
              )
      if runstate.cancelled:
        xit = 1
      return xit

if __name__ == '__main__':
  sys.exit(CalibreCommand(sys.argv).run())
