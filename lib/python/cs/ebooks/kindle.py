#!/usr/bin/env python3

''' Support for Kindle libraries.
'''

from contextlib import contextmanager
import filecmp
from getopt import getopt, GetoptError
import os
from os.path import (
    isdir as isdirpath,
    isfile as isfilepath,
    join as joinpath,
)
from subprocess import run
import sys

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
from cs.deco import cachedmethod
from cs.fileutils import shortpath
from cs.fs import FSPathBasedSingleton, HasFSPath
from cs.fstags import FSTags
from cs.lex import cutsuffix
from cs.logutils import warning
from cs.obj import SingletonMixin
from cs.pfx import Pfx, pfx_call
from cs.progress import progressbar
from cs.resources import MultiOpenMixin
from cs.sqlalchemy_utils import (
    ORM,
    BasicTableMixin,
    HasIdMixin,
    RelationProxy,
    proxy_on_demand_field,
)
from cs.upd import Upd, print  # pylint: disable=redefined-builtin

def main(argv=None):
  ''' Kindle command line mode.
  '''
  return KindleCommand(argv).run()

class KindleTree(FSPathBasedSingleton, MultiOpenMixin):
  ''' Work with a Kindle ebook tree.

      This actually knows very little about Kindle ebooks or its rather opaque database.
      This is mostly to aid keeping track of state using `cs.fstags`.
  '''

  FSPATH_DEFAULT = '~/Library/Containers/com.amazon.Kindle/Data/Library/Application Support/Kindle/My Kindle Content'
  FSPATH_ENVVAR = 'KINDLE_LIBRARY'

  SUBDIR_SUFFIXES = '_EBOK', '_EBSP'

  def __init__(self, fspath=None):
    if hasattr(self, '_bookrefs'):
      return
    super().__init__(fspath=fspath)
    self._bookrefs = {}

    # define the proxy classes
    class KindleBook(SingletonMixin, RelationProxy(self.db.books, [
        'asin',
        'type',
        'revision',
        'sampling',
    ], id_column='asin'), HasFSPath):
      ''' A reference to a Kindle library book subdirectory.
      '''

      # pylint: disable=unused-argument
      @classmethod
      def _singleton_key(cls, tree: KindleTree, subdir_name):
        ''' The singleton key is `(tree,subdir_name
        '''
        return id(tree), subdir_name

      # pylint: super-init-not-called
      @typechecked
      @require(lambda subdir_name: os.sep not in subdir_name)
      def __init__(self, tree: KindleTree, subdir_name: str):
        ''' Initialise this book subdirectory reference.

            Parameters:
            * `tree`: the `Kindletree` containing the subdirectory
            * `subdir_name`: the subdirectory name
        '''
        if 'tree' in self.__dict__:
          if db_book is not None:
            self.refresh_from_db(db_row=db_book)
        else:
          self.tree = tree
          self.subdir_name = subdir_name
          super().__init__(self.asin)

      def __str__(self):
        return "%s[%s]:%s" % (self.tree, self.subdir_name, self.tags)

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
      def tags(self):
        ''' The `FSTags` for this book subdirectory.
        '''
        return self.tree.fstags[self.fspath]

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
        ''' Export this Kindle book to a Calibre instance,
            return `(cbook,added)`
            being the `CalibreBook` and whether the Kinble book was added
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
        azwpath = self.extpath('azw')
        if not isfilepath(azwpath):
          raise ValueError("no AZW file: %r" % (azwpath,))
        added = False
        cbooks = list(calibre.by_asin(self.asin))
        if not cbooks:
          # new book
          quiet or print("new book <=", shortpath(azwpath))
          dbid = calibre.add(azwpath, doit=doit, quiet=quiet)
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
            for fmtk in 'AZW3', 'AZW', 'MOBI':
              fmtpath = cbook.formatpath(fmtk)
              if fmtpath and filecmp.cmp(fmtpath, azwpath):
                quiet or print(
                    cbook, fmtk, shortpath(fmtpath), '=', shortpath(azwpath)
                )
                return cbook, False
            # remaining logic is in CalibreBook.pull_format
            cbook.pull_format(
                azwpath, doit=doit, force=force, quiet=quiet, verbose=verbose
            )
        return cbook, added

    self.KindleBook = KindleBook

  def __str__(self):
    return "%s:%s" % (type(self).__name__, shortpath(self.fspath))

  def __repr__(self):
    return "%s(%r)" % (type(self).__name__, self.fspath)

  @contextmanager
  def startup_shutdown(self):
    ''' Context manager to obtain and release resources.
    '''
    with FSTags() as fstags:
      with stackattrs(self, fstags=fstags):
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

  def by_asin(self, asin):
    ''' Return a `KindleBook` for the supplied `asin`.
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
        return self[subdir_name]
    return self[ASIN + self.SUBDIR_SUFFIXES[0]]

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
      book = self._bookrefs[subdir_name] = self.KindleBook(self, subdir_name)
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

class KindleCommand(BaseCommand):
  ''' Command line for interacting with a Kindle filesystem tree.
  '''

  GETOPT_SPEC = 'C:K:'

  USAGE_FORMAT = '''Usage: {cmd} [-C calibre_library] [-K kindle-library-path] [subcommand [...]]
  -C calibre_library
    Specify calibre library location.
  -K kindle_library
    Specify kindle library location.'''

  SUBCOMMAND_ARGV_DEFAULT = 'info'

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
    with KindleTree(options.kindle_path) as kt:
      with CalibreTree(options.calibre_path) as cal:
        with stackattrs(options, kindle=kt, calibre=cal):
          yield

  def cmd_dbshell(self, argv):
    ''' Usage: {cmd}
          Start an interactive database prompt.
    '''
    if argv:
      raise GetoptError("extra arguments: %r" % (argv,))
    return self.options.kindle.dbshell()

  # pylint: disable=too-many-locals
  def cmd_export(self, argv):
    ''' Usage: {cmd} [-fnqv] [ASINs...]
          Export AZW files to Calibre library.
          -f    Force: replace the AZW3 format if already present.
          -n    No action, recite planned actions.
          -q    Quiet: report only warnings.
          -v    Verbose: report more information about actions and inaction.
          ASINs Optional ASIN identifiers to export.
                The default is to export all books with no "calibre.dbid" fstag.
    '''
    options = self.options
    kindle = options.kindle
    calibre = options.calibre
    runstate = options.runstate
    self.popopts(argv, options, f='force', n='-doit', q='quiet', v='verbose')
    doit = options.doit
    force = options.force
    quiet = options.quiet
    verbose = options.verbose
    if not argv:
      argv = sorted(kindle.asins())
    xit = 0
    for asin in progressbar(argv, "export"):
      if runstate.cancelled:
        break
      with Pfx(asin):
        kbook = kindle.by_asin(asin)
        try:
          cbook, added = kbook.export_to_calibre(
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
    return xit

  def cmd_info(self, argv):
    ''' Usage: {cmd}
          Report basic information.
    '''
    if argv:
      raise GetoptError("extra arguments: %r" % (argv,))
    print("kindle", self.options.kindle.shortpath)
    print("calibre", self.options.calibre.shortpath)

  def cmd_ls(self, argv):
    ''' Usage: {cmd}
          List the contents of the librayr.
    '''
    options = self.options
    kindle = options.kindle
    if argv:
      raise GetoptError("extra arguments: %r" % (argv,))
    print(kindle.fspath)
    for subdir_name, kbook in kindle.items():
      print(subdir_name, " ".join(map(str, sorted(kbook.tags))))
      if kbook.type != 'kindle.ebook':
        print("  type =", kbook.type)
      if kbook.revision is not None:
        print("  revision =", kbook.revision)
      if kbook.sampling:
        print("  sampling =", kbook.sampling)

if __name__ == '__main__':
  sys.exit(main(sys.argv))
