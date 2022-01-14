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
  return CloudCommand(argv).run()

class CloudCommand(BaseCommand):
  ''' A main programme instance.
  '''

  GETOPTS_SPEC = 'A:'
  USAGE_FORMAT = r'''Usage: {cmd} [-A cloud_area] subcommand [...]
    -A cloud_area   A cloud storage area of the form prefix://bucket/subpath.
                    Default from the $CS_CLOUD_AREA environment variable.
  '''
  SUBCOMMAND_ARGV_DEFAULT = 'stat'

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

  def apply_defaults(self):
    self.options.cloud_area_path = os.environ.get('CS_CLOUD_AREA')

  def apply_opt(self, opt, val):
    ''' Handle an individual global command line option.
    '''
    if opt == '-A':
      self.options.cloud_area_path = val
    else:
      raise RuntimeError("unimplemented option")

  def cmd_stat(self, argv):
    ''' Usage: {cmd}
          Report the current settings.
    '''
    if argv:
      raise GetoptError("extra arguments: %r", argv)
    CAF = self.options.cloud_area
    print("Cloud area:")
    print("  cloud", CAF.cloud)
    print("  bucket_name", CAF.bucket_name)
    print("  basepath", CAF.basepath)

  @staticmethod
  def cmd_download(self, argv):
    ''' Usage: {cmd} area_subpath dst
    '''
    options = self.options
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

  def cmd_upload(self, argv):
    ''' Usage: upload src area_subpath
          Upload the file src to cloud area subpath dst.
    '''
    options = self.options
    src, area_subpath = argv
    CAF = options.cloud_area[area_subpath]
    label = f"{src} => {CAF}"
    P = Progress(name=label)
    with P.bar(report_print=True):
      upload_result = CAF.upload_filename(src, progress=P)
    print("uploaded =>", repr(upload_result))

if __name__ == '__main__':
  sys.exit(main(sys.argv))
