#!/usr/bin/env python3

''' Command line mode (CLI) for cs.ebooks.kindle.
'''

from contextlib import contextmanager
from dataclasses import dataclass, field
from getopt import GetoptError
from os.path import (
    dirname,
    exists as existspath,
    isdir as isdirpath,
    join as joinpath,
)
import sys
from typing import Optional

from cs.app.osx.defaults import DomainDefaults as OSXDomainDefaults
from cs.cmdutils import BaseCommand
from cs.context import contextif, stackattrs
from cs.fstags import uses_fstags, FSTags
from cs.lex import s
from cs.logutils import warning, error
from cs.pfx import Pfx
from cs.progress import progressbar
from cs.resources import RunState, uses_runstate

from .classic import (
    KindleBookAssetDB,
    KindleTree,
    KINDLE_APP_OSX_DEFAULTS_DOMAIN,
    KINDLE_APP_OSX_DEFAULTS_CONTENT_PATH_SETTING,
    kindle_content_path,
)
from ..dedrm import DeDRMWrapper

def main(argv=None):
  ''' Kindle command line mode.
  '''
  return KindleCommand(argv).run()

class KindleCommand(BaseCommand):
  ''' Command line for interacting with a Kindle filesystem tree.
  '''

  GETOPT_SPEC = 'C:K:'

  USAGE_FORMAT = '''Usage: {cmd} [-C calibre_library] [-K kindle-library-path] [subcommand [...]]
  Operate on a Kindle library.
  Options:
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
      from ..calibre import CalibreTree  # pylint: disable=import-outside-toplevel
      try:
        # pylint: disable=protected-access
        calibre_path = CalibreTree._resolve_fspath(None)
      except ValueError:
        calibre_path = None
      return calibre_path

    calibre_path: Optional[str] = field(default_factory=_calibre_path)
    dedrm_package_path: Optional[str] = None

    COMMON_OPT_SPECS = dict(
        C_='calibre_path',
        K_='kindle_path',
        **BaseCommand.Options.COMMON_OPT_SPECS,
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
    from ..calibre import CalibreTree  # pylint: disable=import-outside-toplevel
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
              with contextif(options.dedrm):
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
  @uses_runstate
  def cmd_export(self, argv, *, runstate: RunState):
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
    options.popopts(argv, f='force')
    kindle = options.kindle
    calibre = options.calibre
    dedrm = options.dedrm
    doit = options.doit
    force = options.force
    quiet = options.quiet
    verbose = options.verbose
    asins = argv or sorted(kindle.asins())
    xit = 0
    quiet or print("export", kindle.shortpath, "=>", calibre.shortpath)
    for asin in progressbar(asins, f"export to {calibre}"):
      runstate.raiseif()
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
    return xit

  def cmd_keys(self, argv):
    ''' Usage: {cmd} [base|import|json|print]
          Shortcut to \"dedrm kindlekeys\".
    '''
    from ..dedrm import DeDRMCommand
    return DeDRMCommand([self.options.cmd, 'kindlekeys', *argv]).run()

  @uses_runstate
  def cmd_import_tags(self, argv, *, runstate: RunState):
    ''' Usage: {cmd} [-nqv] [ASINs...]
          Import Calibre book information into the fstags for a Kindle book.
          This will support doing searches based on stuff like
          titles which are, naturally, not presented in the Kindle
          metadata db.
    '''
    options = self.options
    kindle = options.kindle
    calibre = options.calibre
    self.popopts(argv, options, n='-doit', q='quiet', v='verbose')
    doit = options.doit
    quiet = options.quiet
    verbose = options.verbose
    asins = argv or sorted(kindle.asins())
    xit = 0
    for asin in progressbar(asins, f"import metadata from {calibre}"):
      runstate.raiseif()
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
    runstate = options.runstate
    print(kindle.fspath)
    for subdir_name, kbook in kindle.items():
      runstate.raiseif()
      line1 = [subdir_name]
      title = kbook.tags.auto.calibre.title
      if title:
        line1.append(title)
      calibre_tags = kbook.tags.auto.calibre
      authors = calibre_tags.authors
      if authors:
        line1.extend(('-', ','.join(authors)))
      if kbook.sampling:
        line1.append(f'({kbook.sampling})')
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

sys.exit(main(sys.argv))
