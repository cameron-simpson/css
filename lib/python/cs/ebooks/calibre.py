#!/usr/bin/env python3

''' Support for Calibre libraries.
'''

from contextlib import contextmanager
from datetime import datetime, timezone
from functools import total_ordering
from getopt import GetoptError
import os
from os.path import expanduser, join as joinpath
import sys
from threading import Lock

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

from cs.cmdutils import BaseCommand
from cs.context import stackattrs
from cs.pfx import Pfx, pfx_call
from cs.resources import MultiOpenMixin
from cs.sqlalchemy_utils import (
    ORM,
    BasicTableMixin,
    HasIdMixin,
)
from cs.threads import locked_property
from cs.units import transcribe_bytes_geek

from cs.x import X

class CalibreTree(MultiOpenMixin):
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
    self.path = calibre_library
    self._lock = Lock()

  @contextmanager
  def startup_shutdown(self):
    ''' Stub startup/shutdown.
    '''
    yield

  def pathto(self, subpath):
    ''' Return the filesystem path of `subpath`
        located within the Calibre filesystem tree.
    '''
    return joinpath(self.path, subpath)

  @locked_property
  def db(self):
    ''' The associated `CalibreMetadataDB` ORM,
        instantiated on demand.
    '''
    return CalibreMetadataDB(self)

  def _run(self, *calargv):
    ''' Run a Calibre utility command.
    '''
    X("calargv=%r", calargv)
    cmd, *calargv = calargv
    if not cmd.startswith(os.sep):
      cmd = joinpath(self.CALIBRE_BINDIR_DEFAULT, cmd)
    print("RUN", cmd, *calargv)
    ##return run([cmd, *calargv])

  def calibredb(self, dbcmd, *argv):
    ''' Run `dbcmd` via the `calibredb` command.
    '''
    return self._run('calibredb', dbcmd, '--library-path=' + self.path, *argv)

  def add(self, bookpath):
    ''' Add a book file via the `calibredb` command.
    '''
    self.calibredb('add', bookpath)

class CalibreMetadataDB(ORM):
  ''' An ORM to access the Calibre `metadata.db` SQLite database.
  '''

  DB_FILENAME = 'metadata.db'

  def __init__(self, tree):
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
    ##Books.language_links = relationship(
    ##    BooksLanguagesLink, back_populates="book"
    ##)

    # references to table definitions
    self.authors = Authors
    self.books = Books
