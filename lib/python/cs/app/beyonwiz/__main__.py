#!/usr/bin/python
#

from __future__ import print_function

import sys
import os.path
import json
from cs.logutils import setup_logging, warning, error
from cs.pfx import Pfx
from cs.x import X
from . import Recording

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

def main(argv):
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
      if op == "cat":
        if len(args) < 1:
          error("missing tvwizdirs")
          badopts = True
      elif op == "convert":
        if len(args) < 1:
          error("missing recording")
          badopts = True
      elif op == "mconvert":
        if len(args) < 1:
          error("missing tvwizdirs")
          badopts = True
      elif op == "meta":
        if len(args) < 1:
          error("missing .ts files or tvwizdirs")
          badopts = True
      elif op == "scan":
        if len(args) < 1:
          error("missing tvwizdirs")
          badopts = True
      elif op == "stat":
        if len(args) < 1:
          error("missing tvwizdirs")
          badopts = True
      elif op == "test":
        pass
      else:
        error("unrecognised operation")
        badopts = True

  if badopts:
    print(usage, file=sys.stderr)
    return 2

  xit = 0

  with Pfx(op):
    if op == "cat":
      for arg in args:
        # NB: dup stdout so that close doesn't close real stdout
        stdout_bfd = os.dup(sys.stdout.fileno())
        stdout_bfp = os.fdopen(stdout_bfd, "wb")
        TVWiz(arg).copyto(stdout_bfp)
        stdoutp.bfp.close()
    elif op == "convert":
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
    elif op == "mconvert":
      xit = 0
      for srcpath in args:
        with Pfx(srcpath):
          R = Recording(srcpath)
          if not R.convert(None, max_n=TRY_N):
            xit = 1
    elif op == "meta":
      for filename in args:
        with Pfx(filename):
          R = Recording(filename)
          print(filename, R.metadata._asjson(), sep='\t')
    elif op == "scan":
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
    elif op == "stat":
      for pathname in args:
        R = Recording(pathname)
        print(pathname)
        for json_line in R.metadata._asjson(indent="  ").split("\n"):
          if json_line:
            print(" ", json_line)
    elif op == "test":
      host = args.pop(0)
      print("host =", host, "args =", args)
      WizPnP(host).test()
    else:
      error("unsupported operation: %s" % op)
      xit = 2

  return xit

if __name__ == '__main__':
  sys.exit(main(sys.argv))
