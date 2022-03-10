#!/usr/bin/env python3

''' A command line mode for the `cs.ebooks` stuff.
'''

import sys

from cs.cmdutils import BaseCommand

from .calibre import CalibreCommand
from .kindle import KindleCommand
from .mobi import MobiCommand

class EBooksCommand(BaseCommand):
  ''' Ebooks utility command.
  '''

  cmd_calibre = CalibreCommand
  cmd_kindle = KindleCommand
  cmd_mobi = MobiCommand

sys.exit(EBooksCommand(sys.argv).run())
