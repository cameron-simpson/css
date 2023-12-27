#!/usr/bin/env python3

''' Support for Kindle libraries.
'''

from contextlib import contextmanager
from dataclasses import dataclass, field
import filecmp
from getopt import GetoptError
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
from typing import Optional

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
from cs.cmdutils import BaseCommand
from cs.context import stackattrs
from cs.deco import cachedmethod, fmtdoc
from cs.fileutils import shortpath
from cs.fs import FSPathBasedSingleton, HasFSPath
from cs.fstags import FSTags, uses_fstags
from cs.lex import cutsuffix, s
from cs.logutils import warning, error
from cs.obj import SingletonMixin
from cs.pfx import Pfx, pfx_call
from cs.progress import progressbar
from cs.psutils import run
from cs.resources import MultiOpenMixin
from cs.sqlalchemy_utils import (
    ORM,
    BasicTableMixin,
    HasIdMixin,
    RelationProxy,
)
from cs.upd import Upd, print  # pylint: disable=redefined-builtin

from .dedrm import DeDRMWrapper, DEDRM_PACKAGE_PATH_ENVVAR

def main(argv=None):
  ''' Kindle command line mode.
  '''
  return KindleCommand(argv).run()

KINDLE_LIBRARY_ENVVAR = 'KINDLE_LIBRARY'

KINDLE_APP_OSX_DEFAULTS_DOMAIN = 'com.amazon.Kindle'
KINDLE_APP_OSX_DEFAULTS_CONTENT_PATH_SETTING = 'User Settings.CONTENT_PATH'
KINDLE_APP_OSX_DEFAULTS_CONTENT_PATH = (
    '~/Library/Containers/com.amazon.Kindle/Data/Library/'
    'Application Support/Kindle/My Kindle Content'
)

# The default location of the Kindle content.
# On MacOS ("darwin") this is KINDLE_APP_OSX_DEFAULTS_CONTENT_PATH.
# Otherwise use the made up path ~/media/kindle/My Kindle Content,
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

@fmtdoc
def default_kindle_library():
  ''' Return the default kindle library content path from `${KINDLE_LIBRARY_ENVVAR}`
      otherwise fall back to `kindle_content_path()`.
    '''
  path = os.environ.get(KINDLE_LIBRARY_ENVVAR, None)
  if path is not None:
    return path
  return kindle_content_path()

class KindleTree(FSPathBasedSingleton, MultiOpenMixin):
  ''' Work with a Kindle ebook tree.

      This actually knows very little about Kindle ebooks or its rather opaque database.
      This is mostly to aid keeping track of state using `cs.fstags`.
  '''

  CONTENT_DIRNAME = 'My Kindle Content'

  FSPATH_FACTORY = default_kindle_library
  FSPATH_ENVVAR = KINDLE_LIBRARY_ENVVAR

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

      @typechecked
      @require(lambda subdir_name: os.sep not in subdir_name)
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
      def export_to_calibre(
          self,
          calibre,
          *,
          dedrm=None,
          doit=True,
          replace_format=False,
          force=False,
          quiet=False,
          verbose=False,
      ):
        ''' Export this Kindle book to a Calibre instance,
            return `(cbook,added)`
            being the `CalibreBook` and whether the Kindle book was added
            (books are not added if the format is already present).

            Parameters:
            * `calibre`: the `CalibreTree`
            * `dedrm`: optional `DeDRMWrapper` instance
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
          quiet or print("new book <=", shortpath(bookpath))
          dbid = calibre.add(bookpath, dedrm=dedrm, doit=doit, quiet=quiet)
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
            for fmtk in 'AZW3', 'AZW', 'KCR', 'MOBI':
              fmtpath = cbook.formatpath(fmtk)
              extpath = self.extpath(fmtk.lower())
              if not existspath(extpath):
                continue
              if fmtpath and existspath(fmtpath):
                if filecmp.cmp(fmtpath, azwpath):
                  # pylint: disable=expression-not-assigned
                  verbose and print(
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

  def __str__(self):
    return "%s:%s" % (type(self).__name__, shortpath(self.fspath))

  def __repr__(self):
    return "%s(%r)" % (type(self).__name__, self.fspath)

  @contextmanager
  @uses_fstags
  def startup_shutdown(self, fstags: FSTags):
    ''' Context manager to obtain and release resources.
    '''
    with fstags:
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
    ''' Return the `KindleBook` for the ebook subdirectory named `subdir_name`.
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

  @dataclass
  class Options(BaseCommand.Options):
    ''' Set up the default values in `options`.
    '''

    def _kindle_path():
      try:
        # pylint: disable=protected-access
        kindle_path = KindleTree._resolve_fspath(None)
      except ValueError:
        kindle_path = None
      return kindle_path

    kindle_path: Optional[str] = field(default_factory=_kindle_path)

    def _calibre_path():
      from .calibre import CalibreTree  # pylint: disable=import-outside-toplevel
      try:
        # pylint: disable=protected-access
        calibre_path = CalibreTree._resolve_fspath(None)
      except ValueError:
        calibre_path = None
      return calibre_path

    calibre_path: Optional[str] = field(default_factory=_calibre_path)
    dedrm_package_path: Optional[str] = field(
        default_factory=lambda: os.environ.get(DEDRM_PACKAGE_PATH_ENVVAR)
    )

  def apply_opt(self, opt, val):
    ''' Apply a command line option.
    '''
    options = self.options
    if opt == '-C':
      options.calibre_path = val
    elif opt == '-K':
      db_subpaths = (
          KindleBookAssetDB.DB_FILENAME,
          joinpath(KindleTree.CONTENT_DIRNAME, KindleBookAssetDB.DB_FILENAME),
      )
      for db_subpath in db_subpaths:
        db_fspath = joinpath(val, db_subpath)
        if existspath(db_fspath):
          break
      else:
        raise GetoptError(
            "cannot find db at %s" % (" or ".join(map(repr, db_subpaths)),)
        )
      options.kindle_path = dirname(db_fspath)
    else:
      super().apply_opt(opt, val)

  @contextmanager
  @uses_fstags
  def run_context(self, fstags: FSTags):
    ''' Prepare the `SQLTags` around each command invocation.
    '''
    from .calibre import CalibreTree  # pylint: disable=import-outside-toplevel
    with super().run_context():
      options = self.options
      dedrm = (
          DeDRMWrapper(options.dedrm_package_path)
          if options.dedrm_package_path else None
      )

      with KindleTree(options.kindle_path) as kt:
        with CalibreTree(options.calibre_path) as cal:
          with stackattrs(options, kindle=kt, calibre=cal, dedrm=dedrm):
            with fstags:
              yield

  def cmd_app_path(self, argv):
    ''' Usage: {cmd} [content-path]
          Report or set the content path for the Kindle application.
    '''
    if not argv:
      print(kindle_content_path())
      return 0
    content_path = self.poparg(
        argv,
        lambda arg: arg,
        "content-path",
        lambda path: path == 'DEFAULT' or isdirpath(path),
        "content-path should be DEFAULT or an existing directory",
    )
    if argv:
      raise GetoptError("extra arguments: %r" % (argv,))
    if content_path == 'DEFAULT':
      content_path = kindle_content_path()
    if sys.platform == 'darwin':
      defaults = OSXDomainDefaults(KINDLE_APP_OSX_DEFAULTS_DOMAIN)
      defaults[KINDLE_APP_OSX_DEFAULTS_CONTENT_PATH_SETTING] = content_path
    else:
      error(
          f'cannot set Kindle default content path on sys.platform=={sys.platform!r}'
      )
      return 1
    return 0

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
    dedrm = options.dedrm
    doit = options.doit
    force = options.force
    quiet = options.quiet
    verbose = options.verbose
    asins = argv or sorted(kindle.asins())
    xit = 0
    quiet or print("export", kindle.shortpath, "=>", calibre.shortpath)
    for asin in progressbar(asins, f"export to {calibre}"):
      if runstate.cancelled:
        break
      with Pfx(asin):
        kbook = kindle.by_asin(asin)
        try:
          kbook.export_to_calibre(
              calibre,
              dedrm=dedrm,
              doit=doit,
              force=force,
              replace_format=force,
              quiet=quiet,
              verbose=verbose,
          )
        except (dedrm.DeDRMError, ValueError) as e:
          warning("export failed: %s", e)
          xit = 1
        except Exception as e:
          warning("kbook.export_to_calibre: e=%s", s(e))
          raise
    if runstate.cancelled:
      xit = 1
    return xit

  def cmd_import_tags(self, argv):
    ''' Usage: {cmd} [-nqv] [ASINs...]
          Import Calibre book information into the fstags for a Kindle book.
          This will support doing searches based on stuff like
          titles which are, naturally, not presented in the Kindle
          metadata db.
    '''
    options = self.options
    kindle = options.kindle
    calibre = options.calibre
    runstate = options.runstate
    self.popopts(argv, options, n='-doit', q='quiet', v='verbose')
    doit = options.doit
    quiet = options.quiet
    verbose = options.verbose
    asins = argv or sorted(kindle.asins())
    xit = 0
    for asin in progressbar(asins, f"import metadata from {calibre}"):
      if runstate.cancelled:
        break
      with Pfx(asin):
        kbook = kindle.by_asin(asin)
        cbooks = list(calibre.by_asin(asin))
        if not cbooks:
          # pylint: disable=expression-not-assigned
          verbose and print("asin %s: no Calibre books" % (asin,))
          continue
        cbook = cbooks[0]
        if len(cbooks) > 1:
          # pylint: disable=expression-not-assigned
          quiet or print(
              f'asin {asin}: multiple Calibre books,',
              f'dbids {[cb.dbid for cb in cbooks]!r}; choosing {cbook}'
          )
        ktags = kbook.tags
        ctags = ktags.subtags('calibre')
        import_tags = dict(
            title=cbook.title,
            authors=sorted([author.name for author in cbook.authors]),
            dbfspath=calibre.fspath,
            dbid=cbook.dbid,
            identifiers=cbook.identifiers,
            tags=cbook.tags,
        )
        first_update = True
        for tag_name, tag_value in sorted(import_tags.items()):
          with Pfx("%s=%r", tag_name, tag_value):
            old_value = ctags.get(tag_name)
            if old_value != tag_value:
              if not quiet:
                if first_update:
                  print(f"{asin}: update from {cbook}")
                  first_update = False
                if old_value:
                  print(
                      f"  calibre.{tag_name}={tag_value!r}, was {old_value!r}"
                  )
                else:
                  print(f"  calibre.{tag_name}={tag_value!r}")
              if doit:
                ctags[tag_name] = tag_value
    if runstate.cancelled:
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
    ''' Usage: {cmd} [-l]
          List the contents of the library.
          -l  Long mode.
    '''
    options = self.options
    kindle = options.kindle
    options.longmode = False
    self.popopts(argv, options, l='longmode')
    longmode = options.longmode
    if argv:
      raise GetoptError("extra arguments: %r" % (argv,))
    print(kindle.fspath)
    for subdir_name, kbook in kindle.items():
      line1 = [subdir_name]
      title = kbook.tags.auto.calibre.title
      if title:
        line1.append(title)
      authors = kbook.tags.auto.calibre.authors
      if authors:
        line1.append(','.join(authors))
      print(*line1)
      if longmode:
        if kbook.type != 'kindle.ebook':
          print("  type =", kbook.type)
        if kbook.revision is not None:
          print("  revision =", kbook.revision)
        if kbook.sampling:
          print("  sampling =", kbook.sampling)
        for tag in sorted(kbook.tags.as_tags()):
          if tag.name not in ('calibre.title', 'calibre.authors'):
            print(" ", tag)

if __name__ == '__main__':
  sys.exit(main(sys.argv))
