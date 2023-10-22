#!/usr/bin/env python3

''' A command line mode for the `cs.ebooks` stuff.
'''

import sys

from cs.cmdutils import BaseCommand

from .apple import AppleBooksCommand
from .calibre import CalibreCommand
from .dedrm import DeDRMCommand
from .kindle import KindleCommand
from .kobo import KoboCommand
from .mobi import MobiCommand

class EBooksCommand(BaseCommand):
  ''' Ebooks utility command.
  '''

  cmd_apple = AppleBooksCommand
  cmd_calibre = CalibreCommand
  cmd_dedrm = DeDRMCommand
  cmd_kindle = KindleCommand
  cmd_kobo = KoboCommand
  cmd_mobi = MobiCommand

sys.exit(EBooksCommand(sys.argv).run())
