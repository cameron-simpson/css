#!/usr/bin/env python3

from getopt import getopt, GetoptError
import os
import sys
from threading import RLock
from types import SimpleNamespace
from cs.cmdutils import BaseCommand
from cs.progress import Progress
from cs.upd import Upd, print
from cs.threads import locked_property
from . import CloudArea

def main(argv=None):
  ''' Create a VTCmd instance and call its main method.
  '''
  return CloudCmd().run(argv)

class CloudCmd(BaseCommand):
  ''' A main programme instance.
  '''

  USAGE_FORMAT = r'''Usage: {cmd} [-A cloud_area] subcommand [...]
      cloud_area    A cloud storage area of the form prefix://bucket/subpath.
  '''

  class OPTIONS_CLASS(SimpleNamespace):

    def __init__(self, **kw):
      super().__init__(**kw)
      self._lock = RLock()

    @locked_property
    def cloud_area(self):
      ''' The `CloudArea`.
      '''
      if not self.cloud_area_path:
        raise ValueError(
            "no cloud area specified; requires -A option or $CS_CLOUD_AREA"
        )
      return CloudArea.from_cloudpath(self.cloud_area_path)

  @staticmethod
  def apply_defaults(options):
    options.cloud_area_path = os.environ.get('CS_CLOUD_AREA')

  @staticmethod
  def cmd_upload(argv, options):
    ''' Usage: upload src dst
          Upload the file src to cloud area subpath dst.
    '''
    src, dst = argv
    CAF = options.cloud_area[dst]
    label = f"{src} => {CAF}"
    P = Progress(name=label)
    with P.bar(report_print=True):
      upload_result = CAF.upload_filename(src, progress=P)
    print("uploaded =>", repr(upload_result))

if __name__ == '__main__':
  sys.exit(main(sys.argv))
