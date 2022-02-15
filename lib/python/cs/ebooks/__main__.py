#!/usr/bin/env python3

''' A command line mode for the `cs.ebooks` stuff.
'''

import sys

from cs.cmdutils import BaseCommand

from .kindle import KindleCommand

class EBooksCommand(BaseCommand):
  ''' Ebooks utility command.
  '''

  cmd_kindle = KindleCommand

sys.exit(EBooksCommand(sys.argv).run())
