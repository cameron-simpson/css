#!/usr/bin/env python3

''' Support for Kindle CLassic (their former app) libraries.
'''

from contextlib import contextmanager
import filecmp
import os
from os.path import (
    dirname,
    expanduser,
    exists as existspath,
    isdir as isdirpath,
    isfile as isfilepath,
    join as joinpath,
)
import sys
from typing import Mapping

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

from cs.app.osx.defaults import DomainDefaults as OSXDomainDefaults
from cs.cmdutils import qvprint
from cs.deco import (
    cachedmethod,
    fmtdoc,
    uses_cmd_options,
)
from cs.fileutils import shortpath
from cs.fs import HasFSPath
from cs.fstags import FSTags, uses_fstags
from cs.lex import cutsuffix
from cs.logutils import warning
from cs.obj import SingletonMixin
from cs.pfx import Pfx, pfx_call
from cs.psutils import run
from cs.sqlalchemy_utils import (
    ORM,
    BasicTableMixin,
    HasIdMixin,
    RelationProxy,
)
from cs.upd import Upd, print  # pylint: disable=redefined-builtin

from . import KINDLE_LIBRARY_ENVVAR
from ..common import AbstractEbooksTree

KINDLE_APP_OSX_DEFAULTS_DOMAIN = 'com.amazon.Kindle'
KINDLE_APP_OSX_DEFAULTS_CONTENT_PATH_SETTING = 'User Settings.CONTENT_PATH'
KINDLE_APP_OSX_DEFAULTS_CONTENT_PATH = (
    '~/Library/Containers/com.amazon.Kindle/Data/Library/'
    'Application Support/Kindle/My Kindle Content'
)

# The default location of the Kindle content
# if there is no enviroment variable
# and, on MacOS/darwin, no defaults setting.
# On MacOS ("darwin") this is KINDLE_APP_OSX_DEFAULTS_CONTENT_PATH.
# Otherwise use the made up path ~/media/kindle/My Kindle Content
# which is where I'm putting my personal Kindle stuff.
KINDLE_CONTENT_DEFAULT_PATH = {
    'darwin': KINDLE_APP_OSX_DEFAULTS_CONTENT_PATH,
}.get(sys.platform, '~/media/kindle/My Kindle Content')

@fmtdoc
def kindle_content_path():
  ''' Return the default Kindle content path.
      On MacOS this will look up
      `{KINDLE_APP_OSX_DEFAULTS_DOMAIN}[{KINDLE_APP_OSX_DEFAULTS_CONTENT_PATH_SETTING}]`
      if present.
      Otherwise it returns `KINDLE_CONTENT_DEFAULT_PATH`
      (`{KINDLE_CONTENT_DEFAULT_PATH!r}`).
  '''
  if sys.platform == 'darwin':
    # use the app settings if provided
    defaults = OSXDomainDefaults(KINDLE_APP_OSX_DEFAULTS_DOMAIN)
    path = defaults.get(KINDLE_APP_OSX_DEFAULTS_CONTENT_PATH_SETTING)
    if path is not None:
      return path
  return expanduser(KINDLE_CONTENT_DEFAULT_PATH)

class KindleTree(AbstractEbooksTree):
  ''' Work with a Kindle ebook tree.

      This actually knows very little about Kindle ebooks or its rather opaque database.
      This is mostly to aid keeping track of state using `cs.fstags`.
  '''

  CONTENT_DIRNAME = 'My Kindle Content'

  FSPATH_DEFAULT = KINDLE_CONTENT_DEFAULT_PATH
  FSPATH_ENVVAR = KINDLE_LIBRARY_ENVVAR

  @classmethod
  @fmtdoc
  def FSPATH_DEFAULT(cls):
    ''' Return the default Kindle content path.
        On MacOS this will look up
        `{KINDLE_APP_OSX_DEFAULTS_DOMAIN}[{KINDLE_APP_OSX_DEFAULTS_CONTENT_PATH_SETTING}]`
        if present.
        Otherwise it returns `KINDLE_CONTENT_DEFAULT_PATH`
        (`{KINDLE_CONTENT_DEFAULT_PATH!r}`).
    '''
    if sys.platform == 'darwin':
      # use the app settings if provided
      defaults = OSXDomainDefaults(KINDLE_APP_OSX_DEFAULTS_DOMAIN)
      path = defaults.get(KINDLE_APP_OSX_DEFAULTS_CONTENT_PATH_SETTING)
      if path is None:
        path = expanduser(KINDLE_APP_OSX_DEFAULTS_CONTENT_PATH)
    else:
      path = expanduser(KINDLE_CONTENT_DEFAULT_PATH)
    return path

  SUBDIR_SUFFIXES = '_EBOK', '_EBSP'

  @classmethod
  def fspath_normalised(cls, fspath: str):
    ''' Normalise `fspath` by locating the book database file.
      '''
    db_subpaths = (
        KindleBookAssetDB.DB_FILENAME,
        joinpath(cls.CONTENT_DIRNAME, KindleBookAssetDB.DB_FILENAME),
    )
    for db_subpath in db_subpaths:
      db_fspath = joinpath(fspath, db_subpath)
      if existspath(db_fspath):
        break
    else:
      raise ValueError(
          f'{cls.__name__}: normalise {fspath!r}'
          f': cannot find db at any of {" or ".join(map(repr, db_subpaths))}'
      )
    # resolve the directory containing the book database
    return super().fspath_normalised(dirname(db_fspath))

  def __init__(self, fspath=None):
    if hasattr(self, '_bookrefs'):
      return
    super().__init__(fspath=fspath)
    self._bookrefs = {}

    # define the proxy classes
    class KindleBook(
        SingletonMixin,
        RelationProxy(
            self.db.books,
            [
                'asin',
                'type',
                'revision',
                'sampling',
            ],
            id_column='asin',
            missing=lambda relp, field: None,
        ),
        HasFSPath,
    ):
      ''' A reference to a Kindle library book subdirectory.
      '''

      # pylint: disable=unused-argument
      @classmethod
      def _singleton_key(cls, tree: KindleTree, subdir_name):
        ''' The singleton key is `(tree,subdir_name
        '''
        return id(tree), subdir_name

      @require(lambda subdir_name: os.sep not in subdir_name)
      @typechecked
      def __init__(self, tree: KindleTree, subdir_name: str):
        ''' Initialise this book subdirectory reference.

            Parameters:
            * `tree`: the `Kindletree` containing the subdirectory
            * `subdir_name`: the subdirectory name
        '''
        if 'tree' not in self.__dict__:
          self.tree = tree
          self.subdir_name = subdir_name
          super().__init__(self.asin)

      def __str__(self):
        return "%s[%s]:%s" % (
            self.tree, self.asin, ",".join(map(str, self.tags))
        )

      def __repr__(self):
        return "%s(%r,%r)" % (type(self).__name__, self.tree, self.subdir_name)

      @property
      def fspath(self):
        ''' The filesystem path of this book subdirectory.
        '''
        return self.tree.pathto(self.subdir_name)

      @property
      def asin(self):
        ''' The ASIN of this book subdirectory, normalised to upper case.
        '''
        for suffix in '_EBOK', '_EBSP':
          prefix = cutsuffix(self.subdir_name, suffix)
          if prefix is not self.subdir_name:
            return prefix.upper()
        raise ValueError(
            "subdir_name %r does not end with _EBOK or _BSP" %
            (self.subdir_name,)
        )

      @property
      def amazon_url(self):
        ''' The Amazon product page for this book.
        '''
        # https://www.amazon.com.au/Wonder-Woman-2016-xx-Liars-ebook/dp/B097KMW2VY/
        title = self.tags.get('calibre.title',
                              'title').replace(' ', '-').replace('/', '-')
        return f'https://www.amazon.com.au/{title}/dp/{self.asin}/'

      def listdir(self):
        ''' Return a list of the names inside the subdirectory,
              or an empty list if the subdirectory is not present.
          '''
        try:
          return os.listdir(self.fspath)
        except FileNotFoundError:
          return []

      def extpath(self, ext):
        ''' Return the filesystem path to the booknamed file
            within the book subdirectory.
        '''
        return self.pathto(self.subdir_name + '.' + ext)

      @property
      @uses_fstags
      def tags(self, fstags: FSTags):
        ''' The `FSTags` for this book subdirectory.
        '''
        return fstags[self.fspath]

      def asset_names(self):
        ''' Return the names of files within the subdirectory
            whose names start with `self.subdir_name+'.'`.
        '''
        prefix_ = self.subdir_name
        return [name for name in self.listdir() if name.startswith(prefix_)]

      def subpath(self, name):
        ''' The filesystem path of `name` within this subdirectory.
        '''
        return joinpath(self.fspath, name)

      def phl_xml(self):
        ''' Decode the `.phl` XML file if present and return an XML `ElementTree`.
            Return `None` if the file is not present.

            This file seems to contain popular highlights in the
            `popular/content/annotation` tags.
          '''
        phl_path = self.subpath(self.subdir_name + '.phl')
        try:
          with pfx_call(open, phl_path, 'rb') as f:
            xml_bs = f.read()
        except FileNotFoundError:
          return None
        with Pfx(phl_path):
          return pfx_call(etree.fromstring, xml_bs)

      # pylint: disable=too-many-branches
      @uses_cmd_options(
          calibre=None,
          dedrm=None,
          doit=True,
          force=False,
          quiet=False,
          verbose=False,
      )
      def export_to_calibre(
          self,
          *,
          calibre,
          dedrm,
          replace_format=False,
          doit,
          force,
          quiet,
          verbose,
      ):
        ''' Export this Kindle book to a Calibre instance,
            return `(cbook,added)`
            being the `CalibreBook` and whether the Kindle book was added
            (books are not added if the format is already present).

            Parameters:
            * `calibre`: optional `CalibreTree`, default from the command line options
            * `dedrm`: optional `DeDRMWrapper`, default from the command line options
            * `doit`: optional flag, default `True`;
              if false just recite planned actions
            * `force`: optional flag, default `False`;
              if true pull the AZW file even if an AZW format already exists
            * `replace_format`: if true, export even if the `AZW3`
              format is already present
            * `quiet`: default `False`, do not print nonwarnings
            * `verbose`: default `False`, print all actions or nonactions
        '''
        azwpath = self.extpath('azw')
        kcrpath = self.extpath('kcr')
        if isfilepath(azwpath):
          bookpath = azwpath
        elif isfilepath(kcrpath):
          bookpath = kcrpath
        else:
          raise ValueError("no AZW or KCR file: %r, %r" % (azwpath, kcrpath))
        added = False
        cbooks = list(calibre.by_asin(self.asin))
        if not cbooks:
          # new book
          # pylint: disable=expression-not-assigned
          qvprint("new book <=", shortpath(bookpath))
          dbid = calibre.add(bookpath, dedrm=dedrm, doit=doit, quiet=quiet)
          if dbid is None:
            added = not doit
            cbook = None
          else:
            added = True
            calibre.refresh_dbid(dbid)
            cbook = calibre[dbid]
            qvprint(" ", cbook)
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
            for fmtk in 'AZW3', 'AZW', 'KCR', 'MOBI':
              fmtpath = cbook.formatpath(fmtk)
              extpath = self.extpath(fmtk.lower())
              if not existspath(extpath):
                continue
              if fmtpath and existspath(fmtpath):
                if filecmp.cmp(fmtpath, azwpath):
                  # pylint: disable=expression-not-assigned
                  qvprint(
                      cbook, fmtk, shortpath(fmtpath), '=', shortpath(azwpath)
                  )
                  return cbook, False
              # remaining logic is in CalibreBook.pull_format
              cbook.pull_format(
                  extpath,
                  doit=doit,
                  force=force,
                  quiet=quiet,
                  verbose=verbose
              )
        return cbook, added

    self.KindleBook = KindleBook

  @contextmanager
  @uses_fstags
  def startup_shutdown(self, fstags: FSTags):
    ''' Context manager to obtain and release resources.
    '''
    with fstags:
      with self.db:
        yield

  @property
  @cachedmethod
  def db(self):
    ''' The associated `KindleBookAssetDB` ORM,
        instantiated on demand.
    '''
    return KindleBookAssetDB(self)

  def dbshell(self):
    ''' Interactive db shell.
    '''
    return self.db.shell()

  def subdir_for_asin(self, asin: str) -> str:
    ''' Return the subdirectory name for `asin`,
        or `asin.upper()+self.SUBDIR_SUFFIXES[0]` if nothing is found.
    '''
    ASIN = asin.upper()
    # convert ASIN_EBOK to ASIN, handy for copy/paste of subdir name from listing
    for suffix in self.SUBDIR_SUFFIXES:
      subdir_name = cutsuffix(ASIN, suffix)
      if subdir_name is not ASIN:
        ASIN = subdir_name
        break
    for suffix in self.SUBDIR_SUFFIXES:
      subdir_name = ASIN + suffix
      if isdirpath(self.pathto(subdir_name)):
        return subdir_name
    return ASIN + self.SUBDIR_SUFFIXES[0]

  def get_library_books_mapping(self) -> Mapping:
    ''' Return a mapping of library primary keys to library book instances.
    '''
    book_map = {}
    for asin in self.asins():
      try:
        book = self._bookrefs[asin]
      except KeyError:
        book = self._bookrefs[asin] = self.KindleBook(
            self, self.subdir_for_asin(asin)
        )
      book_map[asin] = book
    return book_map

  def is_book_subdir(self, subdir_name):
    ''' Test whether `subdir_name` is a Kindle ebook subdirectory basename.
    '''
    return subdir_name.endswith(self.SUBDIR_SUFFIXES)

  def book_subdir_names(self):
    ''' Return a list of the individual ebook subdirectory names.
    '''
    return [
        dirbase for dirbase in os.listdir(self.fspath)
        if self.is_book_subdir(dirbase)
    ]

  def asins(self):
    ''' Return a `set` of the `books.asin` column values.
    '''
    db = self.db
    books = db.books
    with db.session() as session:
      return set(asin for asin, in session.query(books.asin))

  def __getitem__(self, asin: str):
    ''' Return a `KindleBook` for the supplied `asin`.
    '''
    ASIN = asin.upper()
    # convert ASIN_EBOK to ASIN, handy for copy/paste of subdir name from listing
    for suffix in self.SUBDIR_SUFFIXES:
      subdir_name = cutsuffix(ASIN, suffix)
      if subdir_name is not ASIN:
        ASIN = subdir_name
        break
    return super().__getitem__(ASIN)

# pylint: disable=too-many-instance-attributes
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
    return self.tree.pathto(self.DB_FILENAME)

  def shell(self):
    ''' Interactive db shell.
    '''
    print("sqlite3", self.db_path)
    with Upd().above():
      run(['sqlite3', self.db_path], stdin=0, check=True)
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
          'Book sample state, often blank, "Sample" for a sample download',
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
    with self.session() as session:
      self.version_info = VersionInfo.lookup1(session=session).version

    # references to table definitions
    self.download_state_map = DownloadState
    DownloadState.orm = self
    self.books = Book
    Book.orm = self
    self.book_download_info = BookDownloadInfo
    BookDownloadInfo.orm = self
    self.requirement_level_map = RequirementLevel
    RequirementLevel.orm = self
    self.assets = Asset
    Asset.orm = self
    self.endpoint_type_map = EndpointType
    EndpointType.orm = self
    self.delivery_type_map = DeliveryType
    DeliveryType.orm = self
    self.asset_download_info = AssetDownloadInfo
    AssetDownloadInfo.orm = self
