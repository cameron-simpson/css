#!/usr/bin/env python3

''' Utilities for working with EBooks.
'''

from contextlib import contextmanager
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
    Column,
    Integer,
    String,
    ForeignKey,
)
from sqlalchemy.orm import relationship
from typeguard import typechecked

from cs.cmdutils import BaseCommand
from cs.context import stackattrs
from cs.fileutils import shortpath
from cs.fstags import FSTags
from cs.lex import cutsuffix
from cs.logutils import error, info
from cs.pfx import Pfx, pfx, pfx_call
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
    ''' The associated KindleBookAssetDB ORM,
        instantiated on demand'
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

if __name__ == '__main__':
  with KindleTree() as kindle:
    for book in kindle.values():
      print(book.subdir_name, book)
      phl = book.phl_xml()
      if phl is not None:
        print(etree.tostring(phl, pretty_print=True).decode())
