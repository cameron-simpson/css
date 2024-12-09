#!/usr/bin/env python3

''' A command line mode for the `cs.ebooks` stuff.
'''

import sys

from cs.cmdutils import BaseCommand

from .apple import AppleBooksCommand
from .calibre import CalibreCommand
from .common import EBooksCommonBaseCommand
from .dedrm import DeDRMCommand, DeDRMWrapper, DEDRM_PACKAGE_PATH_ENVVAR
from .kindle.__main__ import KindleCommand
from .kobo import KoboCommand
from .mobi import MobiCommand
from .pdf import PDFCommand

def main(argv):
  ''' The `ebooks` command line mode.
  '''
  return EBooksCommand(argv).run()

class EBooksCommand(EBooksCommonBaseCommand):
  ''' Ebooks utility command.
  '''

  cmd_apple = AppleBooksCommand
  cmd_calibre = CalibreCommand
  cmd_dedrm = DeDRMCommand
  cmd_kindle = KindleCommand
  cmd_kobo = KoboCommand
  cmd_mobi = MobiCommand
  cmd_pdf = PDFCommand

if __name__ == '__main__':
  sys.exit(main(sys.argv))
