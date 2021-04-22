#!/usr/bin/env python3

''' Command line implementation for the cs.cloud module.
'''

import os
import sys
from threading import RLock
from types import SimpleNamespace
from cs.cmdutils import BaseCommand
from cs.pfx import Pfx
from cs.progress import Progress
from cs.upd import print  # pylint: disable=redefined-builtin
from cs.threads import locked
from . import CloudArea

def main(argv=None):
  ''' Create a `CloudCommand` instance and call its main method.
  '''
  return CloudCommand().run(argv)

class CloudCommand(BaseCommand):
  ''' A main programme instance.
  '''

  GETOPTS_SPEC = 'A:'
  USAGE_FORMAT = r'''Usage: {cmd} [-A cloud_area] subcommand [...]
      cloud_area    A cloud storage area of the form prefix://bucket/subpath.
                    Default from the $CS_CLOUD_AREA environment variable.
  '''

  # pylint: disable=too-few-public-methods
  class OPTIONS_CLASS(SimpleNamespace):
    ''' Options namespace with convenience methods.
    '''

    def __init__(self, **kw):
      super().__init__(**kw)
      self._lock = RLock()

    @property
    @locked
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
  def apply_options(options, opts):
    ''' Apply main command line options.
    '''
    for opt, val in opts:
      with Pfx(opt):
        if opt == '-A':
          options.cloud_area_path = val
        else:
          raise RuntimeError("unimplemented option")

  @staticmethod
  def cmd_download(argv, options):
    ''' Usage: {cmd} area_subpath dst
    '''
    area_subpath, dst = argv
    CAF = options.cloud_area[area_subpath]
    label = f"{CAF} => {dst}"
    with Pfx("%r", dst):
      with open(dst, 'wb') as f:
        P = Progress(name=label)
        with P.bar(report_print=True):
          bfr, dl_result = CAF.download_buffer(progress=P)
          for bs in bfr:
            f.write(bs)
          bfr.close()
      print(label + ':')
      for k, v in sorted(dl_result.items()):
        print(" ", k, v)

  @staticmethod
  def cmd_upload(argv, options):
    ''' Usage: upload src area_subpath
          Upload the file src to cloud area subpath dst.
    '''
    src, area_subpath = argv
    CAF = options.cloud_area[area_subpath]
    label = f"{src} => {CAF}"
    P = Progress(name=label)
    with P.bar(report_print=True):
      upload_result = CAF.upload_filename(src, progress=P)
    print("uploaded =>", repr(upload_result))

if __name__ == '__main__':
  sys.exit(main(sys.argv))
