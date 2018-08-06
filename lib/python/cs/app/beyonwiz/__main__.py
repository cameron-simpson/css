#!/usr/bin/python
#

''' Command line utility for working with Beyonwiz PVR devices.
'''

from __future__ import print_function
from getopt import GetoptError
import json
import os.path
import sys
from cs.logutils import setup_logging, warning, error
from cs.pfx import Pfx
from cs.x import X
from . import Recording
from .tvwiz import TVWiz
from .wizpnp import WizPnP

TRY_N = 32

USAGE = '''Usage:
    %s cat tvwizdirs...
        Write the video content of the named tvwiz directories to
        standard output as MPEG2 transport Stream, acceptable to
        ffmpeg's "mpegts" format.
    %s convert recording [start..end]... [output.mp4]
        Convert the video content of the named recording to
        the named output file (typically MP4, though the ffmpeg
        output format chosen is based on the extension).
        Most metadata are preserved.
        start..end: Optional start and end offsets in seconds, used
          to crop the recording output.
    %s mconvert recording...
        Convert multiple named recordings to automatically named .mp4 files
        in the current directory.
        Most metadata are preserved.
    %s meta recording...
        Report metadata for the supplied recordings.
    %s scan recording...
        Scan the data structures of the supplied recordings.
    %s stat tvwizdirs...
        Print some summary infomation for the named tvwiz directories.
    %s test
        Run unit tests.'''

def main(argv=None):
  ''' Command line main programme.
  '''
  return Main().main(argv)

class Main:

  def main(self, argv=None):
    if argv is None:
      argv = sys.argv
    args = list(argv)
    cmd = os.path.basename(args.pop(0))
    setup_logging(cmd)
    usage = USAGE % (cmd, cmd, cmd, cmd, cmd, cmd, cmd)

    badopts = False

    if not args:
      error("missing operation")
      badopts = True
    else:
      op = args.pop(0)
      with Pfx(op):
        try:
          opcmd = getattr(self, 'cmd_' + op)
        except AttributeError:
          error("unrecognised operation")
          badopts = True
        else:
          try:
            xit = opcmd(args)
          except GetoptError as e:
            error("%s", e)
            badopts = True
    if badopts:
      print(usage, file=sys.stderr)
      return 2
    return xit

  def cmd_cat(self, args):
    if not args:
      raise GetoptError("missing recordings")
    for arg in args:
      # NB: dup stdout so that close doesn't close real stdout
      stdout_bfd = os.dup(sys.stdout.fileno())
      stdout_bfp = os.fdopen(stdout_bfd, "wb")
      TVWiz(arg).copyto(stdout_bfp)
      stdout_bfp.close()

  def cmd_convert(self, args):
    if not args:
      raise GetoptError("missing recording")
    srcpath = args.pop(0)
    with Pfx(srcpath):
      # parse optional start..end arguments
      timespans = []
      while args and '..' in args[0]:
        try:
          start, end = args[0].split('..')
          start_s = float(start)
          end_s = float(end)
          if start_s > end_s:
            raise ValueError("start:%s > end:%s" % (start, end))
        except ValueError as e:
          X("FAIL %r: %s", args[0], e)
          pass
        else:
          args.pop(0)
          timespans.append( (start_s, end_s) )
      # collect optional dstpath
      if args:
        dstpath = args.pop(0)
      else:
        dstpath = None
      R = Recording(srcpath)
      xit = 0 if R.convert(dstpath, max_n=TRY_N, timespans=timespans) else 1
      return xit

  def cmd_mconvert(self, args):
    if not args:
      raise GetoptError("missing recordings")
    xit = 0
    for srcpath in args:
      with Pfx(srcpath):
        R = Recording(srcpath)
        if not R.convert(None, max_n=TRY_N):
          xit = 1
    return xit

  def cmd_meta(self, args):
    if not args:
      raise GetoptError("missing recordings")
    for filename in args:
      with Pfx(filename):
        R = Recording(filename)
        print(filename, R.metadata._asjson(), sep='\t')

  def cmd_scan(self, args):
    if not args:
      raise GetoptError("missing recordings")
    for arg in args:
      print(arg)
      total = 0
      chunkSize = 0
      chunkOff = 0
      for wizOffset, fileNum, flags, offset, size in TVWiz(arg).trunc_records():
        print("  wizOffset=%d, fileNum=%d, flags=%02x, offset=%d, size=%d" \
              % ( wizOffset, fileNum, flags, offset, size )
             )
        total += size
        if chunkOff != wizOffset:
          skip = wizOffset - chunkOff
          if chunkSize == 0:
            print("    %d skipped" % skip)
          else:
            print("    %d skipped, after a chunk of %d" % (skip, chunkSize))
          chunkOff = wizOffset
          chunkSize = 0
        chunkOff += size
        chunkSize += size
      if chunkOff > 0:
        print("    final chunk of %d" % chunkSize)
      print("  total %d" % total)

  def cmd_stat(self, args):
    if not args:
      raise GetoptError("missing recordings")
    for pathname in args:
      with Pfx(pathname):
        R = Recording(pathname)
        print(pathname)
        for json_line in R.metadata._asjson(indent="  ").split("\n"):
          if json_line:
            print(" ", json_line)

  def cmd_test(self, args):
    host = args.pop(0)
    print("host =", host, "args =", args)
    WizPnP(host).test()

if __name__ == '__main__':
  sys.exit(main(sys.argv))
