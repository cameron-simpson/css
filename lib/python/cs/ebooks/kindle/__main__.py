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
from cs.cmdutils import BaseCommand, qvprint
from cs.context import contextif, stackattrs
from cs.fstags import uses_fstags, FSTags
from cs.lex import r, s
from cs.logutils import warning, error
from cs.pfx import Pfx
from cs.progress import progressbar
from cs.resources import RunState, uses_runstate

from ..common import EBooksCommonBaseCommand
from .classic import (
    KindleBookAssetDB,
    KindleTree,
    KINDLE_APP_OSX_DEFAULTS_DOMAIN,
    KINDLE_APP_OSX_DEFAULTS_CONTENT_PATH_SETTING,
    kindle_content_path,
)

def main(argv=None):
  ''' Kindle command line mode.
  '''
  return KindleCommand(argv).run()

class KindleCommand(EBooksCommonBaseCommand):
  ''' Command line for interacting with a Kindle filesystem tree.
  '''

  USAGE_FORMAT = '''Usage: {cmd} [options...] [subcommand [...]]
  Operate on a Kindle library.'''

  SUBCOMMAND_ARGV_DEFAULT = 'info'

  @contextmanager
  @uses_fstags
  def run_context(self, fstags: FSTags):
    ''' Prepare the `SQLTags` around each command invocation.
    '''
    with super().run_context():
      with self.options.kindle:
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
  @uses_runstate
  def cmd_export(self, argv, *, runstate: RunState):
    ''' Usage: {cmd} [-f] [ASINs...]
          Export AZW files to Calibre library.
          -f    Force: replace the AZW3 format if already present.
          ASINs Optional ASIN identifiers to export.
                The default is to export all books with no "calibre.dbid" fstag.
    '''
    self.popopts(argv, f='force')
    options = self.options
    calibre = options.calibre
    dedrm = options.dedrm
    kindle = options.kindle
    force = options.force
    asins = argv or sorted(kindle.asins())
    xit = 0
    qvprint("export", kindle.shortpath, "=>", calibre.shortpath)
    with calibre:
      with contextif(dedrm):
        for asin in progressbar(asins, f"export to {calibre}"):
          runstate.raiseif()
          with Pfx(asin):
            try:
              kbook = kindle[asin]
            except KeyError as e:
              warning("no Kindle book for ASIN %s: %s", r(asin), e)
              xit = 1
              continue
            try:
              kbook.export_to_calibre(
                  calibre=calibre,
                  dedrm=dedrm,
                  replace_format=force,
              )
            except ValueError as e:
              warning("export failed: %s", e)
              xit = 1
            except dedrm.DeDRMError as e:
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
    ''' Usage: {cmd} [ASINs...]
          Import Calibre book information into the fstags for a Kindle book.
          This will support doing searches based on stuff like
          titles which are, naturally, not presented in the Kindle
          metadata db.
    '''
    options = self.options
    options.popopts(argv)
    doit = options.doit
    calibre = options.calibre
    kindle = options.kindle
    asins = argv or sorted(kindle.asins())
    xit = 0
    for asin in progressbar(asins, f"import metadata from {calibre}"):
      runstate.raiseif()
      with Pfx(asin):
        kbook = kindle.by_asin(asin)
        cbooks = list(calibre.by_asin(asin))
        if not cbooks:
          # pylint: disable=expression-not-assigned
          qvprint("asin %s: no Calibre books" % (asin,))
          continue
        cbook = cbooks[0]
        if len(cbooks) > 1:
          # pylint: disable=expression-not-assigned
          qvprint(
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
              if first_update:
                qvprint(f"{asin}: update from {cbook}")
                first_update = False
              if old_value:
                qvprint(
                    f"  calibre.{tag_name}={tag_value!r}, was {old_value!r}"
                )
              else:
                qvprint(f"  calibre.{tag_name}={tag_value!r}")
              if doit:
                ctags[tag_name] = tag_value
    return xit

  def cmd_ls(self, argv):
    ''' Usage: {cmd} [-l]
          List the contents of the library.
          -l  Long mode.
    '''
    options = self.options
    kindle = options.kindle
    options.longmode = False
    options.popopts(argv, l='longmode')
    longmode = options.longmode
    if argv:
      raise GetoptError("extra arguments: %r" % (argv,))
    runstate = options.runstate
    print(kindle.fspath)
    for subdir_name, kbook in kindle.items():
      runstate.raiseif()
      with Pfx(kbook):
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

if __name__ == '__main__':
  sys.exit(main(sys.argv))
