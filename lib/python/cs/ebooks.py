#!/usr/bin/env python3

''' Utilities for working with EBooks.
'''

from contextlib import contextmanager
from datetime import datetime, timezone
from getopt import GetoptError
from glob import glob
import os
from os.path import (
    basename,
    exists as existspath,
    expanduser,
    isfile as isfilepath,
    join as joinpath,
    relpath,
    splitext,
)
import sys
from tempfile import TemporaryDirectory
from threading import Lock
from zipfile import ZipFile, ZIP_STORED

from icontract import require
try:
  from lxml import etree
except ImportError:
  import xml.etree.ElementTree as etree
import mobi
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
)
from sqlalchemy.orm import declared_attr, relationship
from typeguard import typechecked

from cs.cmdutils import BaseCommand
from cs.context import stackattrs
from cs.fileutils import shortpath
from cs.fstags import FSTags
from cs.lex import cutsuffix
from cs.logutils import error, warning, info
from cs.pfx import Pfx, pfx, pfx_call
from cs.psutils import run
from cs.resources import MultiOpenMixin
from cs.sqlalchemy_utils import (
    ORM,
    BasicTableMixin,
    HasIdMixin,
)
from cs.threads import locked_property

class Mobi:
  ''' Work with an existing MOBI ebook file.
  '''

  def __init__(self, mobipath):
    if not isfilepath(mobipath):
      raise ValueError("mobipath %r is not a file" % (mobipath,))
    self.path = mobipath

  def __repr__(self):
    return "%s(%r)" % (type(self).__name__, repr(self.path))

  __str__ = __repr__

  @pfx
  def extract(self, dirpath=None):
    ''' Extract the contents of the MOBI file into a directory.
        Return `(dirpath,rfilepath)` where `dirpath` is the extracted
        file tree and `filepath` is the relative pathname of the
        primary epub, html or pdf file depending on the mobi type.
    '''
    if dirpath is not None and existspath(dirpath):
      raise ValueError("dirpath %r already exists" % (dirpath,))
    # divert stdout because the mobi library sends some warnings etc to stdout
    with stackattrs(sys, stdout=sys.stderr):
      tmpdirpath, filepath = pfx_call(mobi.extract, self.path)
    rfilepath = relpath(filepath, tmpdirpath)
    if dirpath is None:
      dirpath = tmpdirpath
    else:
      pfx_call(os.rename, tmpdirpath, dirpath)
    return dirpath, rfilepath

  @contextmanager
  def extracted(self):
    ''' Context manager version of `extract()` which extracts the
        MOBI into a temporary directory and yields the resulting
        `(dirpath,rfilepath)` as for `extract()`.
    '''
    with TemporaryDirectory(prefix='%s.extracted-' % (type(self).__name__,),
                            suffix='-%s' %
                            (self.path.replace(os.sep, '_'),)) as T:
      dirpath, rfilepath = self.extract(dirpath=joinpath(T, 'extracted'))
      yield dirpath, rfilepath

  @pfx
  def make_cbz(self, cbzpath=None):
    ''' Create a CBZ file from the images in the MOBI file.
        Return the path to the created CBZ file.
    '''
    if cbzpath is None:
      mobibase, mobiext = splitext(basename(self.path))
      cbzpath = mobibase + '.cbz'
    if existspath(cbzpath):
      raise ValueError("CBZ path %r already exists" % (cbzpath,))
    with self.extracted() as df:
      dirpath, rfilepath = df
      imagepaths = sorted(glob(joinpath(dirpath, 'mobi8/OEBPS/Images/*.*')))
      info("write %s", cbzpath)
      try:
        with pfx_call(ZipFile, cbzpath, 'x', compression=ZIP_STORED) as cbz:
          for imagepath in imagepaths:
            pfx_call(cbz.write, imagepath, arcname=basename(imagepath))
      except FileExistsError as e:
        error("CBZ already eixsts: %r: %s", cbzpath, e)
        return 1
      except Exception:
        if existspath(cbzpath):
          pfx_call(os.unlink, cbzpath)
        raise
    return cbzpath

class Mobi2CBZCommand(BaseCommand):
  ''' Command line implementation for `mobi2cbz`.
  '''

  USAGE_FORMAT = r'''Usage: {cmd} mobipath [cbzpath]
    Unpack a MOBI file and construct a CBZ file.
    Prints the path of the CBZ file to the output.'''

  @staticmethod
  def main(argv):
    ''' `mobi2cbz` command line implementation.
    '''
    if not argv:
      raise GetoptError("missing mobipath")
    mobipath = argv.pop(0)
    mobibase, mobiext = splitext(basename(mobipath))
    if argv:
      cbzpath = argv.pop(0)
    else:
      cbzpath = mobibase + '.cbz'
    if argv:
      raise GetoptError("extra arguments after cbzpath: %r" % (argv,))
    if not existspath(mobipath):
      raise GetoptError("mobipath does not exist: %r" % (mobipath,))
    if existspath(cbzpath):
      raise GetoptError("CBZ already exists: %r" % (cbzpath,))
    MB = Mobi(mobipath)
    outcbzpath = MB.make_cbz(cbzpath)
    print(outcbzpath)

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

class CalibreTree(MultiOpenMixin):

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
      ##linktable.__name__ = f'{left_name.title()}s{right_name.title()}sLink'
      X("%s: %r", linktable, dir(linktable))
      return linktable

    # pylint: disable=missing-class-docstring
    class Authors(Base, _CalibreTable):
      __tablename__ = 'authors'
      name = Column(String, nullable=False, unique=True)
      sort = Column(String)
      link = Column(String, nullable=False, default="")

    # pylint: disable=missing-class-docstring
    class Books(Base, _CalibreTable):
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

      def identifiers_as_dict(self):
        ''' Return a `dict` mapping identifier types to values.
        '''
        return {
            identifier.type: identifier.val
            for identifier in self.identifiers
        }

    class BooksAuthorsLink(Base, _linktable('book', 'author')):
      pass

    class Languages(Base, _CalibreTable):
      __tablename__ = 'languages'
      lang_code = Column(String, nullable=False, unique=True)
      item_order = Column(Integer, nullable=False, default=1)

    class Identifiers(Base, _CalibreTable):
      __tablename__ = 'identifiers'
      book_id = Column("book", ForeignKey('books.id'), nullable=False)
      type = Column(String, nullable=False, default="isbn")
      val = Column(String, nullable=None)

    Authors.book_links = relationship(
        BooksAuthorsLink, back_populates="author"
    )
    Books.author_links = relationship(BooksAuthorsLink, back_populates="book")
    Books.identifiers = relationship(Identifiers, back_populates="book")
    Identifiers.book = relationship(Books, back_populates="identifiers")

    # references to table definitions
    self.authors = Authors
    self.books = Books

class KindleCommand(BaseCommand):
  ''' Command line for interacting with a Kindle filesystem tree.
  '''

  GETOPT_SPEC = 'K:'

  USAGE_FORMAT = '''Usage: {cmd} [-K kindle-library-path] subcommand [...]
  -C calibre_library
    Specify calibre library location, default from ${CALIBRE_LIBRARY_ENVVAR}:
    {CALIBRE_LIBRARY_DEFAULT}
  -K kindle_library
    Specify kindle library location, default from ${KINDLE_LIBRARY_ENVVAR}:
    {KINDLE_LIBRARY_DEFAULT}'''

  USAGE_KEYWORDS = {
      'CALIBRE_LIBRARY_ENVVAR': CalibreTree.CALIBRE_LIBRARY_ENVVAR,
      'CALIBRE_LIBRARY_DEFAULT': CalibreTree.CALIBRE_LIBRARY_DEFAULT,
      'KINDLE_LIBRARY_ENVVAR': KindleTree.KINDLE_LIBRARY_ENVVAR,
      'KINDLE_LIBRARY_DEFAULT': KindleTree.KINDLE_LIBRARY_DEFAULT,
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
      calibre.add(kbook.extpath('azw'))

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

if __name__ == '__main__':
  sys.exit(KindleCommand(sys.argv).run())
  calibre = CalibreTree()
  db = calibre.db
  with db.db_session() as session:
    for book in db.books.lookup(session=session):
      print(book.title)
      print(" ", book.path)
      for identifier in book.identifiers:
        print("%s=%s" % (identifier.type, identifier.val))
      for author_link in book.author_links:
        print(" ", author_link.author.name)
  ##sys.exit(KindleCommand(sys.argv).run())
