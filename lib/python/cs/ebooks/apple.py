#!/usr/bin/env python3

''' Apple Books access.
'''

from contextlib import contextmanager
from getopt import GetoptError
from glob import glob
from os.path import join as joinpath
from pprint import pprint
import sys

from sqlalchemy import (
    Column,
    Float,
    Integer,
    LargeBinary,
    String,
)
from typeguard import typechecked

from cs.app.osx.plist import ingest_plist
from cs.cmdutils import BaseCommand
from cs.context import stackattrs
from cs.deco import strable
from cs.fs import FSPathBasedSingleton
from cs.logutils import warning
from cs.pfx import pfx_call, pfx_method
from cs.psutils import run
from cs.resources import MultiOpenMixin
from cs.sqlalchemy_utils import ORM, BasicTableMixin
from cs.threads import locked_property

class AppleBooksTree(FSPathBasedSingleton, MultiOpenMixin):
  ''' Work with an Apple Books tree.
  '''

  FSPATH_DEFAULT = '~/Library/Containers/com.apple.iBooksX/Data//Documents/BKLibrary'
  FSPATH_ENVVAR = 'APPLE_BOOKS_LIBRARY'

  @contextmanager
  def startup_shutdown(self):
    ''' For MultiOpenMixin.
    '''
    yield

  @locked_property
  def db(self):
    ''' The associated `AppleBooksDB` ORM,
        instantiated on demand.
    '''
    return AppleBooksDB(self)

  def dbshell(self):
    ''' Interactive db shell.
    '''
    return self.db.shell()

  def __iter__(self):
    ''' Generator yielding Apple Book db entries.
    '''
    db = self.db
    with db.db_session() as session:
      yield from sorted(
          db.books.lookup(session=session),
          key=lambda book: (book.sort_author, book.sort_title)
      )

class AppleBooksDB(ORM):
  ''' An ORM to access the Apple Books SQLite database.
  '''

  # example: BKLibrary-1-091020131601.sqlite
  DB_GLOB = 'BKLibrary-1-*.sqlite'

  @strable(open_func=AppleBooksTree)
  @typechecked
  def __init__(self, tree: AppleBooksTree):
    self.tree = tree
    self.db_path = self._find_library_dbpath()
    self.db_url = 'sqlite:///' + self.db_path
    super().__init__(self.db_url)

  @pfx_method
  def _find_library_dbpath(self):
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

  def shell(self):
    ''' Interactive db shell.
    '''
    print("sqlite3", self.db_path)
    run(['sqlite3', self.db_path], check=True)
    return 0

  @property
  def orm(self):
    ''' No distinct ORM class for `CalibreMetadataDB`.
    '''
    return self

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

            echo .schema | sqlite3 /Users/cameron/Library/Containers/com.apple.iBooksX/Data//Documents/BKLibrary/BKLibrary-1-091020131601.sqlite | sed 's/\([(,]\) /\1\n  /g'
    '''
    Base = self.Base

    # pylint: disable=too-few-public-methods
    class HasPKMixin:
      ''' Include a "Z_PK" `Column` as the primary key.
      '''
      pk = Column('Z_PK', Integer, primary_key=True)

    class LibraryAsset(Base, BasicTableMixin, HasPKMixin):
      ''' A library asset, presumeably a book.

          This has many columns, I'm just defining the interesting ones.
          We get away with this because we're only selecting, not inserting.
      '''
      __tablename__ = 'ZBKLIBRARYASSET'
      content_type = Column('ZCONTENTTYPE', Integer)
      file_size = Column('ZFILESIZE', Integer)
      page_size = Column('ZPAGECOUNT', Integer)
      creation_date = Column('ZCREATIONDATE', Float)  # not a DateTime
      purchase_date = Column('ZPURCHASEDATE', Float)  # not a DateTime
      release_date = Column('ZRELEASEDATE', Float)  # not a DateTime
      version_number = Column('ZVERSIONNUMBER', Float)
      account_id = Column('ZACCOUNTID', String)
      asset_id = Column('ZASSETID', String)
      author = Column('ZAUTHOR', String)
      description = Column('ZBOOKDESCRIPTION', String)
      cover_url = Column('ZCOVERURL', String)
      epub_id = Column('ZEPUBID', String)
      family_id = Column('ZFAMILYID', String)
      genre = Column('ZGENRE', String)
      grouping = Column('ZGROUPING', String)
      kind = Column('ZKIND', String)
      language = Column('ZLANGUAGE', String)
      path = Column('ZPATH', String)
      perm_link = Column('ZPERMLINK', String)
      series_id = Column('ZSERIESID', String)
      sort_author = Column('ZSORTAUTHOR', String)
      sort_title = Column('ZSORTTITLE', String)
      store_id = Column('ZSTOREID', String)
      title = Column('ZTITLE', String)
      human_version_number = Column('ZVERSIONNUMBERHUMANREADABLE', String)
      year = Column('ZYEAR', String)

    class Metadata(Base, BasicTableMixin):
      ''' The metadata table - seems to be a single row with library metadata in a plist.
      '''
      __tablename__ = 'Z_METADATA'
      version = Column('Z_VERSION', Integer, primary_key=True)
      uuid = Column('Z_UUID', String)
      plist = Column('Z_PLIST', LargeBinary)

      def plist_as_dict(self):
        ''' Return the plist column as a `PListDict`.
        '''
        return ingest_plist(self.plist)

    class Collection(Base, BasicTableMixin, HasPKMixin):
      ''' Collection definitions.
      '''
      __tablename__ = 'ZBKCOLLECTION'
      collection_id = Column('ZCOLLECTIONID', String)
      details = Column('ZDETAILS', String)
      title = Column('ZTITLE', String)

    class CollectionMember(Base, BasicTableMixin, HasPKMixin):
      ''' Collection members.
      '''
      __tablename__ = 'ZBKCOLLECTIONMEMBER'
      asset = Column('ZASSET', Integer)
      collection = Column('ZCOLLECTION', Integer)
      asset_id = Column('ZASSETID', String)

    self.books = LibraryAsset
    self.metadata = Metadata
    self.collections = Collection
    self.collection_members = CollectionMember

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

  def cmd_dbshell(self, argv):
    ''' Usage: {cmd}
          Start an interactive database shell.
    '''
    if argv:
      raise GetoptError("extra arguments: %r" % (argv,))
    return self.options.apple.dbshell()

  def cmd_ls(self, argv):
    ''' Usage: {cmd}
          List books in the library.
    '''
    options = self.options
    long_mode = False
    if argv and argv[0] == '-l':
      long_mode = True
      argv.pop(0)
    if argv:
      raise GetoptError("extra arguments: %r" % (argv,))
    for book in options.apple:
      print(f"{book.title}, {book.author} ({book.asset_id})")
      if long_mode:
        if book.cover_url:
          print("  cover:", book.cover_url)
        if book.genre:
          print("  genre:", book.genre)
        if book.description:
          print("  description:")
          print("   ", book.description.replace("\n", "    \n"))

  def cmd_md(self, argv):
    ''' Usage: {cmd}
          List metadata.
    '''
    options = self.options
    if argv:
      raise GetoptError("extra arguments: %r" % (argv,))
    db = options.apple.db
    with db.db_session() as session:
      for md in db.metadata.lookup(session=session):
        pprint(md.plist_as_dict())

if __name__ == '__main__':
  sys.exit(AppleBooksCommand(sys.argv).run())
