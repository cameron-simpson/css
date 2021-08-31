#!/usr/bin/env python3

from collections import defaultdict
from contextlib import contextmanager
from getopt import GetoptError
from pprint import pprint
import sys
from cs.cmdutils import BaseCommand
from cs.context import stackattrs
from cs.fstags import FSTags
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

  def cmd_autofile(self, argv):
    ''' Usage: {cmd} pathnames...
          Link pathnames to destinations based on their tags.
    '''
    if not argv:
      raise GetoptError("missing pathnames")
    tagger = self.options.tagger
    for path in argv:
      print("autofile", path)
      linked_to = tagger.file_by_tags(path)
      print("  linked to", repr(linked_to))

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
