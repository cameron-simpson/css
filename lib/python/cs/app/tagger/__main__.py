#!/usr/bin/env python3

from contextlib import contextmanager
from getopt import GetoptError
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
