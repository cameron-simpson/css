#!/usr/bin/env python3

''' Support for Kindle libraries.
'''

from contextlib import contextmanager
from getopt import GetoptError
import os
from os.path import expanduser, join as joinpath
import sys
from threading import Lock

from icontract import require
from sqlalchemy import (
    Column,
    ForeignKey,
    Integer,
    String,
)
from sqlalchemy.orm import relationship
from typeguard import typechecked
try:
  from lxml import etree
except ImportError:
  import xml.etree.ElementTree as etree

from cs.cmdutils import BaseCommand
from cs.context import stackattrs
from cs.fileutils import shortpath
from cs.fstags import FSTags
from cs.lex import cutsuffix
from cs.logutils import warning
from cs.pfx import Pfx, pfx_call
from cs.resources import MultiOpenMixin
from cs.sqlalchemy_utils import (
    ORM,
    BasicTableMixin,
    HasIdMixin,
)
from cs.threads import locked_property

class KindleTree(MultiOpenMixin):
  ''' Work with a Kindle ebook tree.

      This actually knows very little about Kindle ebooks or its rather opaque database.
      This is mostly to aid keeping track of state using `cs.fstags`.
  '''

  # default to the MacOS path, needs updates for other platforms
  KINDLE_LIBRARY_DEFAULT = '~/Library/Containers/com.amazon.Kindle/Data/Library/Application Support/Kindle/My Kindle Content'

  # environment variable to override the path
  KINDLE_LIBRARY_ENVVAR = 'KINDLE_LIBRARY'

  def __init__(self, kindle_library=None):
    if kindle_library is None:
      kindle_library = os.environ.get(self.KINDLE_LIBRARY_ENVVAR)
      if kindle_library is None:
        # default to the MacOS path, needs updates for other platforms
        kindle_library = expanduser(self.KINDLE_LIBRARY_DEFAULT)
    self.path = kindle_library
    self._bookrefs = {}
    self._lock = Lock()

  def __str__(self):
    return "%s:%s" % (type(self).__name__, shortpath(self.path))

  def __repr__(self):
    return "%s(%r)" % (type(self).__name__, self.path)

  @contextmanager
  def startup_shutdown(self):
    ''' Context manager to obtain and release resources.
    '''
    with FSTags() as fstags:
      with stackattrs(self, fstags=fstags):
        yield

  @locked_property
  def db(self):
    ''' The associated `KindleBookAssetDB` ORM,
        instantiated on demand.
    '''
    return KindleBookAssetDB(self)

  @staticmethod
  def is_book_subdir(subdir_name):
    ''' Test whther `subdir_name` is a Kindle ebook subdirectory basename.
    '''
    return subdir_name.endswith(('_EBOK', '_EBSP'))

  def book_subdir_names(self):
    ''' Return a list of the individual ebook subdirectory names.
    '''
    return [
        dirbase for dirbase in os.listdir(self.path)
        if self.is_book_subdir(dirbase)
    ]

  def asins(self):
    ''' Return a `set` of the `books.asin` column values.
    '''
    db = self.db
    books = db.books
    with db.db_session() as session:
      return set(asin for asin, in session.query(books.asin))

  def by_asin(self, asin):
    ''' Return a `KindleBook` for the supplied `asin`.
    '''
    return self[asin.upper() + '_EBOK']

  def keys(self):
    ''' The keys of a `KindleTree` are its book subdirectory names.
    '''
    return self.book_subdir_names()

  def __getitem__(self, subdir_name):
    ''' Return the `cs.fstags.TaggedPath` for the ebook subdirectory named `subdir_name`.
    '''
    if not self.is_book_subdir(subdir_name):
      raise ValueError(
          "not a Kindle ebook subdirectory name: %r" % (subdir_name,)
      )
    try:
      book = self._bookrefs[subdir_name]
    except KeyError:
      book = self._bookrefs[subdir_name] = KindleBook(self, subdir_name)
    return book

  def __iter__(self):
    ''' Mapping iteration method.
    '''
    return iter(self.keys())

  def values(self):
    ''' Mapping method yielding `KindleBook` instances.
    '''
    yield from map(self.__getitem__, self)

  def items(self):
    ''' Mapping method yielding `(subdir_name,KindleBook)` pairs.
    '''
    for k in self:
      yield k, self[k]

class KindleBook:
  ''' A reference to a Kindle library book subdirectory.
  '''

  @typechecked
  @require(lambda subdir_name: os.sep not in subdir_name)
  def __init__(self, tree: KindleTree, subdir_name: str):
    ''' Initialise this book subdirectory reference.

        Parameters:
        * `tree`: the `Kindletree` containing the subdirectory
        * `subdir_name`: the subdirectory name
    '''
    self.tree = tree
    self.subdir_name = subdir_name

  def __str__(self):
    return "%s[%s]:%s" % (self.tree, self.subdir_name, self.tags)

  def __repr__(self):
    return "%s(%r,%r)" % (type(self).__name__, self.tree, self.subdir_name)

  @property
  def asin(self):
    ''' The ASIN of this book subdirectory.
    '''
    for suffix in '_EBOK', '_EBSP':
      prefix = cutsuffix(self.subdir_name, suffix)
      if prefix is not self.subdir_name:
        return prefix
    raise ValueError(
        "subdir_name %r does not end with _EBOK or _BSP" % (self.subdir_name,)
    )

  def listdir(self):
    ''' Return a list of the names inside the subdirectory,
          or an empty list if the subdirectory is not present.
      '''
    try:
      return os.listdir(self.path)
    except FileNotFoundError:
      return []

  @property
  def path(self):
    ''' The filesystem path of this book subdirectory.
    '''
    return joinpath(self.tree.path, self.subdir_name)

  def pathto(self, subpath):
    ''' Return the filesystem path of `subpath`
        located within the book subdirectory.
    '''
    return joinpath(self.path, subpath)

  def extpath(self, ext):
    ''' Return the filesystem path to the booknamed file
        within the book subdirectory.
    '''
    return self.pathto(self.subdir_name + '.' + ext)

  @property
  def tags(self):
    ''' The `FSTags` for this book subdirectory.
    '''
    return self.tree.fstags[self.path]

  def asset_names(self):
    ''' Return the names of files within the subdirectory
        whose names start with `self.subdir_name+'.'`.
    '''
    prefix_ = self.subdir_name
    return [name for name in self.listdir() if name.startswith(prefix_)]

  def subpath(self, name):
    ''' The filesystem path of `name` within this subdirectory.
    '''
    return joinpath(self.path, name)

  def phl_xml(self):
    ''' Decode the `.phl` XML file if present and return an XML `ElementTree`.
        Return `None` if the file is not present.

        This file seems to contain popular highlights in the
        `popular/content/annotation` tags.
      '''
    phl_path = self.subpath(self.subdir_name + '.phl')
    try:
      with open(phl_path, 'rb') as f:
        xml_bs = f.read()
    except FileNotFoundError:
      return None
    with Pfx(phl_path):
      return pfx_call(etree.fromstring, xml_bs)

class KindleBookAssetDB(ORM):
  ''' An ORM to access the Kindle `book_asset.db` SQLite database.
  '''

  DB_FILENAME = 'book_asset.db'

  def __init__(self, tree):
    self.tree = tree
    self.db_url = 'sqlite:///' + self.db_path
    super().__init__(self.db_url)

  @property
  def orm(self):
    ''' No distinct ORM class for `KindleBookAssetDB`.
    '''
    return self

  @property
  def db_path(self):
    ''' The filesystem path to the database.
    '''
    return joinpath(self.tree.path, self.DB_FILENAME)

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

            sqlite3 ~/KINDLE/book_asset.db .schema | sed 's/,  *\([^ ]\)/,\n    \1/g'
    '''
    Base = self.Base

    # pylint: disable=missing-class-docstring
    class VersionInfo(Base, BasicTableMixin):
      __tablename__ = 'VersionInfo'
      version = Column(
          Integer,
          primary_key=True,
          comment='database schema version number, just one row'
      )

    # pylint: disable=missing-class-docstring
    class DownloadState(Base, BasicTableMixin, HasIdMixin):
      __tablename__ = 'DownloadState'
      state = Column(
          String,
          nullable=False,
          unique=True,
          comment='mapping of download state ids to text description'
      )

    # pylint: disable=missing-class-docstring
    class Book(Base, BasicTableMixin, HasIdMixin):
      __tablename__ = 'Book'
      asin = Column(String, nullable=False, comment='Amazon ASIN indentifier')
      type = Column(
          String, nullable=False, comment='Book type eg "kindle.ebook"'
      )
      revision = Column(
          String, nullable=False, comment='Book revision, often blank'
      )
      sampling = Column(
          String,
          nullable=False,
          comment=
          'Book sample state, often blank, "Sample" for a sample download'
      )

    # pylint: disable=missing-class-docstring
    class BookDownloadInfo(Base, BasicTableMixin, HasIdMixin):
      __tablename__ = 'BookDownloadInfo'
      bookId = Column(
          Integer,
          ForeignKey("Book.id"),
          nullable=False,
          index=True,
          comment='Book row id',
      )
      book = relationship("Book")
      responseContext = Column(String, comment='BASE64 encoded information')

    # pylint: disable=missing-class-docstring
    class RequirementLevel(Base, BasicTableMixin, HasIdMixin):
      __tablename__ = 'RequirementLevel'
      level = Column(
          String,
          nullable=False,
          unique=True,
          comment='mapping of requirement level ids to text description'
      )

    # pylint: disable=missing-class-docstring
    class Asset(Base, BasicTableMixin, HasIdMixin):
      __tablename__ = 'Asset'
      bookId = Column(
          Integer,
          ForeignKey("Book.id"),
          nullable=False,
          index=True,
          comment='Book row id',
      )
      book = relationship("Book")
      guid = Column(String, nullable=False, comment='GUID of the Asset?')
      requirementLevel_id = Column(
          "requirementLevel",
          Integer,
          ForeignKey("RequirementLevel.id"),
          nullable=False,
          index=True,
          comment='Requirement Level id'
      )
      requirementLevel = relationship("RequirementLevel")
      size = Column(Integer)
      contentType = Column(String, nullable=False)
      localFilename = Column(String)
      downloadState_id = Column(
          "downloadState",
          Integer,
          ForeignKey("DownloadState.id"),
          default=1,
          comment='Asset download state id'
      )
      downloadState = relationship("DownloadState")

    # pylint: disable=missing-class-docstring
    class EndpointType(Base, BasicTableMixin, HasIdMixin):
      __tablename__ = 'EndpointType'
      type = Column(String, nullable=False)

    # pylint: disable=missing-class-docstring
    class DeliveryType(Base, BasicTableMixin, HasIdMixin):
      __tablename__ = 'DeliveryType'
      type = Column(String, nullable=False)

    # pylint: disable=missing-class-docstring
    class AssetDownloadInfo(Base, BasicTableMixin, HasIdMixin):
      __tablename__ = 'AssetDownloadInfo'
      assetId = Column(
          Integer,
          ForeignKey("Asset.id"),
          nullable=False,
          index=True,
          comment='Asset row id',
      )
      endpoint = Column(String, nullable=False)
      endpointType = Column(
          Integer,
          ForeignKey("EndpointType.id"),
          nullable=False,
          index=True,
          comment='Endpoint type id'
      )
      responseContext = Column(String)
      deliveryType = Column(
          Integer,
          ForeignKey("DeliveryType.id"),
          nullable=False,
          index=True,
          comment='Delivery type id'
      )

    # just suck the version out
    with self.db_session() as session:
      self.version_info = VersionInfo.lookup1(session=session).version

    # references to table definitions
    self.download_state_map = DownloadState
    self.books = Book
    self.book_download_info = BookDownloadInfo
    self.requirement_level_map = RequirementLevel
    self.assets = Asset
    self.endpoint_type_map = EndpointType
    self.delivery_type_map = DeliveryType
    self.asset_download_info = AssetDownloadInfo

class KindleCommand(BaseCommand):
  ''' Command line for interacting with a Kindle filesystem tree.
  '''

  GETOPT_SPEC = 'K:'

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
    from .calibre import CalibreTree  # pylint: disable=import-outside-toplevel
    options = self.options
    with KindleTree(kindle_library=options.kindle_path) as kt:
      with CalibreTree(calibre_library=options.calibre_path) as cal:
        with stackattrs(options, kindle=kt, calibre=cal, verbose=True):
          yield

  def cmd_calibre_export(self, argv):
    ''' Usage: {cmd}
          Export AZW files to Calibre library.
    '''
    if argv:
      raise GetoptError("extra arguments: %r" % (argv,))
    options = self.options
    kindle = options.kindle
    calibre = options.calibre
    for subdir_name, kbook in kindle.items():
      dbid = kbook.tags.auto.calibre.dbid
      if dbid:
        print(subdir_name, "calibre.dbid:", dbid)
        continue
      azw_path = kbook.extpath('azw')
      dbid = calibre.add(azw_path)
      kbook.tags['calibre.dbid'] = dbid

  def cmd_calibre_import_dbids(self, argv):
    ''' Usage: {cmd}
          Import Calibre database ids by backtracking from Calibre
          `mobi-asin` identifier records.
    '''
    if argv:
      raise GetoptError("extra arguments: %r" % (argv,))
    options = self.options
    kindle = options.kindle
    calibre = options.calibre
    with calibre.db.db_session() as session:
      for book in calibre.db.books.lookup(session=session):
        with Pfx("%d: %s", book.id, book.path):
          print(book.path)
          asin = book.identifiers_as_dict().get('mobi-asin')
          if asin:
            kb = kindle.by_asin(asin)
            print(kb.path)
            dbid = kb.tags.auto.calibre.dbid
            if dbid:
              if dbid != book.id:
                warning("book dbid %s != kb calibre.dbid %s", book.id, dbid)
            else:
              print("kb %s + calibre.dbid=%r" % (asin, book.id))
              kb.tags['calibre.dbid'] = book.id

  def cmd_ls(self, argv):
    options = self.options
    kindle = options.kindle
    if argv:
      raise GetoptError("extra arguments: %r" % (argv,))
    for subdir_name, kbook in kindle.items():
      print(subdir_name)
      for tag in sorted(kbook.tags):
        print(" ", tag)

if __name__ == '__main__':
  sys.exit(KindleCommand(sys.argv).run())
