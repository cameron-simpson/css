#!/usr/bin/env python3

''' Support for Calibre libraries.
'''

from contextlib import contextmanager
from datetime import datetime, timezone
from functools import lru_cache, total_ordering
from getopt import GetoptError
import os
from os.path import isabs as isabspath, expanduser, join as joinpath
from subprocess import run, DEVNULL, CalledProcessError
import sys
from threading import Lock

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
from cs.logutils import error
from cs.lex import cutprefix
from cs.pfx import Pfx, pfx_call
from cs.resources import MultiOpenMixin
from cs.sqlalchemy_utils import (
    ORM,
    BasicTableMixin,
    HasIdMixin,
)
from cs.tagset import TagSet
from cs.threads import locked_property
from cs.units import transcribe_bytes_geek

from cs.x import X

from . import HasFSPath

class CalibreTree(HasFSPath, MultiOpenMixin):
  ''' Work with a Calibre ebook tree.
  '''

  CALIBRE_LIBRARY_DEFAULT = '~/CALIBRE'
  CALIBRE_LIBRARY_ENVVAR = 'CALIBRE_LIBRARY'
  CALIBRE_BINDIR_DEFAULT = '/Applications/calibre.app/Contents/MacOS'

  def __init__(self, calibre_library=None):
    if calibre_library is None:
      calibre_library = os.environ.get(self.CALIBRE_LIBRARY_ENVVAR)
      if calibre_library is None:
        calibre_library = expanduser(self.CALIBRE_LIBRARY_DEFAULT)
    HasFSPath.__init__(self, calibre_library)
    self._lock = Lock()

  @contextmanager
  def startup_shutdown(self):
    ''' Stub startup/shutdown.
    '''
    yield

  @locked_property
  def db(self):
    ''' The associated `CalibreMetadataDB` ORM,
        instantiated on demand.
    '''
    return CalibreMetadataDB(self)

  @typechecked
  def __getitem__(self, dbid: int):
    return self.book_by_dbid(dbid)

  @lru_cache(maxsize=None)
  @typechecked
  @require(lambda dbid: dbid > 0)
  def book_by_dbid(self, dbid, *, db_book=None):
    ''' Return a cached `CalibreBook` for `dbid`.
    '''
    return CalibreBook(self, dbid, db_book=db_book)

  def __iter__(self):
    ''' Generator yielding `CalibreBook`s.
    '''
    db = self.db
    with db.db_session() as session:
      for author in sorted(db.authors.lookup(session=session)):
        with Pfx("%d:%s", author.id, author.name):
          print(author.name)
          for book in sorted(author.books):
            yield self.book_by_dbid(book.id, db_book=book)

  def _run(self, *calargv, subp_options=None):
    ''' Run a Calibre utility command.

        Parameters:
        * `calargv`: an iterable of the calibre command to issue;
          if the command name is not an absolute path
          it is expected to come from `self.CALIBRE_BINDIR_DEFAULT`
        * `subp_options`: optional mapping of keyword arguments
          to pass to `subprocess.run`
    '''
    X("calargv=%r", calargv)
    if subp_options is None:
      subp_options = {}
    subp_options.setdefault('check', True)
    cmd, *calargv = calargv
    if not isabspath(cmd):
      cmd = joinpath(self.CALIBRE_BINDIR_DEFAULT, cmd)
    print("RUN", cmd, *calargv)
    try:
      cp = pfx_call(run, [cmd, *calargv], **subp_options)
    except CalledProcessError as cpe:
      error(
          "run fails, exit code %s:\n  %s",
          cpe.returncode,
          ' '.join(map(repr, cpe.cmd)),
      )
      if cpe.stderr:
        print(cpe.stderr.replace('\n', '  \n'), file=sys.stderr)
      raise
    return cp

  def calibredb(self, dbcmd, *argv, subp_options=None):
    ''' Run `dbcmd` via the `calibredb` command.
    '''
    return self._run(
        'calibredb',
        dbcmd,
        '--library-path=' + self.fspath,
        *argv,
        subp_options=subp_options
    )

  def add(self, bookpath):
    ''' Add a book file via the `calibredb add` command.
        Return the database id.
    '''
    cp = self.calibredb(
        'add',
        '--duplicates',
        bookpath,
        subp_options=dict(stdin=DEVNULL, capture_output=True, text=True)
    )
    # Extract the database id from the "calibredb add" output.
    dbids = []
    for line in cp.stdout.split('\n'):
      line_sfx = cutprefix(line, 'Added book ids:')
      if line_sfx is not line:
        dbids.extend(map(lambda s: int(s.strip()), line_sfx.split(',')))
    dbid, = dbids
    return dbid


class CalibreBook:

  @typechecked
  def __init__(self, tree: CalibreTree, dbid: int, *, db_book=None):
    self.tree = tree
    self.dbid = dbid
    self._db_book = db_book

  @cachedmethod
  def db_book(self):
    ''' Return a cached reference to the database book record.
    '''
    db = self.tree.db
    with db.db_session() as session:
      X("FETCH BOOK %r", self.dbid)
      return db.books.by_id(self.dbid, session=session)

  def __getattr__(self, attr):
    ''' Unknown public attributes defer to the database record.
    '''
    if attr.startswith('_'):
      raise AttributeError(attr)
    return getattr(self.db_book(), attr)

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

  # lifted from SQLTags
  @contextmanager
  def db_session(self, *, new=False):
    ''' Context manager to obtain a db session if required
        (or if `new` is true).
    '''
    orm_state = self.orm.sqla_state
    get_session = orm_state.new_session if new else orm_state.auto_session
    with get_session() as session2:
      yield session2

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
          declared_attr(lambda self: relationship(f'{left_name.title()}s'))
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
          declared_attr(lambda self: relationship(f'{right_name.title()}s'))
      )
      for colname, colspec in addtional_columns.items():
        setattr(linktable, colname, declared_attr(lambda self: colspec))
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

      def formats_as_dict(self):
        ''' Return a `dict` mapping formats to book format relative paths.
        '''
        return {
            format.format:
            joinpath(self.path, f'{format.name}.{format.format.lower()}')
            for format in self.formats
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
      item_order = Column(Integer, nullable=False, default=1)

    class BooksAuthorsLink(Base, _linktable('book', 'author')):
      ''' Link table between `Books` and `Authors`.
      '''


    Authors.book_links = relationship(BooksAuthorsLink)
    Authors.books = association_proxy('book_links', 'book')

    Books.author_links = relationship(BooksAuthorsLink)
    Books.authors = association_proxy('author_links', 'author')
    Books.identifiers = relationship(Identifiers)
    Books.formats = relationship(Data, backref="book")

    Identifiers.book = relationship(Books, back_populates="identifiers")

    # references to table definitions
    self.authors = Authors
    self.books = Books

class CalibreCommand(BaseCommand):
  ''' Command line tool to interact with a Calibre filesystem tree.
  '''

  GETOPT_SPEC = 'C:K:'

  USAGE_FORMAT = '''Usage: {cmd} [-K kindle-library-path] subcommand [...]
  -C calibre_library
    Specify calibre library location.
  -K kindle_library
    Specify kindle library location.'''

  SUBCOMMAND_ARGV_DEFAULT = 'ls'

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
    with KindleTree(kindle_library=options.kindle_path) as kt:
      with CalibreTree(calibre_library=options.calibre_path) as cal:
        db = cal.db
        with db.db_session() as session:
          with stackattrs(options, kindle=kt, calibre=cal, db=db,
                          session=session, verbose=True):
            yield

  def cmd_ls(self, argv):
    ''' Usage: {cmd}
          List the contents of the Calibre library.
    '''
    if argv:
      raise GetoptError("extra arguments: %r" % (argv,))
    options = self.options
    calibre = options.calibre
    db = options.db
    session = options.session
    for author in sorted(db.authors.lookup(session=session)):
      with Pfx("%d:%s", author.id, author.name):
        print(author.name)
        for book in sorted(author.books):
          with Pfx("%d:%s", book.id, book.title):
            print(" ", book.title)
            for fmt, subpath in book.formats_as_dict().items():
              with Pfx(fmt):
                fspath = calibre.pathto(subpath)
                size = pfx_call(os.stat, fspath).st_size
                print("   ", fmt, transcribe_bytes_geek(size), subpath)

if __name__ == '__main__':
  sys.exit(CalibreCommand(sys.argv).run())
