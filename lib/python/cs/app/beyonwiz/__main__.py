#!/usr/bin/python
#

from __future__ import print_function

import sys
import os.path
import json
from cs.logutils import setup_logging, warning, error, Pfx
from . import Recording

USAGE = '''Usage:
    %s cat tvwizdirs...
        Write the video content of the named tvwiz directories to
        standard output as MPEG2 transport Stream, acceptable to
        ffmpeg's "mpegts" format.
    %s convert tvwizdir output.mp4
        Convert the video content of the named tvwiz directory to
        the named output file (typically MP4, though he ffmpeg
        output format chosen is based on the extension). Most
        metadata are preserved.
    %s mconvert recording...
        Convert the video content of the named recording to an
        automatically named .mp4 files in the current directory.
        Most metadata are preserved.
    %s meta recording...
        Report metadata for the supplied recordings.
    %s scan tvwizdirs...
        Scan the data structures of the named tvwiz directories.
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
          error("missing tvwizdir")
          badopts = True
        if len(args) < 2:
          error("missing output.mp4")
          badopts = True
        if len(args) > 2:
          warning("extra arguments after output: %s", " ".join(args))
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
      srcpath, dstpath = args
      R = Recording(srcpath)
      xit = R.convert(dstpath)
    elif op == "mconvert":
      for srcpath in args:
        with Pfx(srcpath):
          ok = True
          R = Recording(srcpath)
          dstpath = R.convertpath()
          if os.path.exists(dstpath):
            dstpfx, dstext = os.path.splitext(dstpath)
            ok = False
            for i in range(32):
              dstpath2 = "%s--%d%s" % (dstpfx, i+1, dstext)
              if not os.path.exists(dstpath2):
                dstpath = dstpath2
                ok = True
                break
            if not ok:
              error("file exists, and so do most --n flavours of it: %r", dstpath)
              xit = 1
          if ok:
            try:
              ffxit = R.convert(dstpath)
            except ValueError as e:
              error("%s: %s", dstpath, e)
              xit = 1
    elif op == "meta":
      for filename in args:
        with Pfx(filename):
          R = Recording(filename)
          print(filename, R.metadata.as_json(), sep='\t')
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
      for arg in args:
        TV = TVWiz(arg)
        H = TV.header
        print(arg)
        print("  %s %s: %s, %s" % (H.svcName, H.start_dt.isoformat(' '), H.evtName, H.episode))
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
