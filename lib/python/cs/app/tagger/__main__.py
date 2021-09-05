#!/usr/bin/env python3

from collections import defaultdict
from contextlib import contextmanager
from getopt import GetoptError, getopt
import os
from os.path import (
    exists as existspath,
    isdir as isdirpath,
    isfile as isfilepath,
    join as joinpath,
)
from pprint import pprint
import sys
from cs.cmdutils import BaseCommand
from cs.context import stackattrs
from cs.fstags import FSTags
from cs.logutils import warning
from cs.pfx import Pfx, pfxprint
from . import Tagger
from .gui import TaggerGUI

def main(argv=None):
  ''' Command line for the tagger.
  '''
  return TaggerCommand(argv).run()

class TaggerCommand(BaseCommand):
  ''' Tagger command line implementation.
  '''

  SUBCOMMAND_ARGV_DEFAULT = 'gui'

  @contextmanager
  def run_context(self):
    ''' Set up around commands.
    '''
    options = self.options
    with FSTags() as fstags:
      tagger = Tagger(fstags=fstags)
      with stackattrs(options, tagger=tagger):
        yield

  @staticmethod
  def _autofile(path, *, no_link, tagger):
    ''' Wrapper for `Tagger.file_by_tags` which reports actions.
    '''
    if not no_link and not existspath(path):
      warning("no such path, skipped")
      linked_to = []
    else:
      linked_to = tagger.file_by_tags(path, no_link=no_link)
      if linked_to:
        for linked in linked_to:
          pfxprint('=>', linked)
      else:
        ##pfxprint('not filed')
        pass
    return linked_to

  def cmd_autofile(self, argv):
    ''' Usage: {cmd} pathnames...
          Link pathnames to destinations based on their tags.
          -d    Treat directory pathnames like file - file the
                directory, not its contents.
                (TODO: we file by linking - this needs a rename.)
          -n    No link (default). Just print filing actions.
          -r    Recurse. Required to autofile a directory tree.
          -y    Link: file 
    '''
    direct = False
    recurse = False
    no_link = True
    opts, argv = getopt(argv, 'dnry')
    for opt, val in opts:
      with Pfx(opt):
        if opt == '-d':
          direct = True
        elif opt == 'n':
          no_link = True
        elif opt == '-r':
          recurse = True
        elif opt == '-y':
          no_link = False
        else:
          raise RuntimeError("unimplemented option")
    if not argv:
      raise GetoptError("missing pathnames")
    tagger = self.options.tagger
    fstags = tagger.fstags
    for path in argv:
      with Pfx(path):
        if direct or not isdirpath(path):
          self._autofile(path, no_link=no_link, tagger=tagger)
        elif not recurse:
          pfxprint("not autofiling directory, use -r for recursion")
        else:
          for subpath, dirnames, filenames in os.walk(path):
            with Pfx(subpath):
              # order the descent
              dirnames[:] = sorted(
                  dname for dname in dirnames
                  if dname and not dname.startswith('.')
              )
              tagged = fstags[subpath]
              if 'tagger.skip' in tagged:
                # prune this directory tree
                dirnames[:] = []
                continue
              for filename in sorted(filenames):
                with Pfx(filename):
                  filepath = joinpath(subpath, filename)
                  if not isfilepath(filepath):
                    pfxprint("not a regular file, skipping")
                    continue
                  self._autofile(filepath, no_link=no_link, tagger=tagger)

  def cmd_derive(self, argv):
    ''' Usage: {cmd} dirpaths...
          Derive an autofile mapping of tags to directory paths
          from the directory paths suppplied.
    '''
    if not argv:
      raise GetoptError("missing dirpaths")
    tagger = self.options.tagger
    mapping = defaultdict(list)
    tag_names = 'abn', 'invoice', 'vendor'
    for path in argv:
      print("scan", path)
      mapping = tagger.generate_auto_file_map(path, tag_names, mapping)
    pprint(mapping)

  def cmd_gui(self, argv):
    ''' Usage: {cmd} pathnames...
          Run a GUI to tag pathnames.
    '''
    if not argv:
      raise GetoptError("missing pathnames")
    with TaggerGUI(self.options.tagger, argv) as gui:
      gui.run()

if __name__ == '__main__':
  sys.exit(main(sys.argv))
