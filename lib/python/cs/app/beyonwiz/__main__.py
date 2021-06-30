#!/usr/bin/env python
#

''' Command line utility for working with Beyonwiz PVR devices.
'''

from __future__ import print_function
from getopt import GetoptError
import os.path
from pprint import pformat
import sys
from cs.cmdutils import BaseCommand
from cs.pfx import Pfx
from . import Recording, DEFAULT_FORMAT
from .tvwiz import TVWiz
from .wizpnp import WizPnP

TRY_N = 32

def main(argv=None, cmd=None):
  ''' Main command line.
  '''
  return BWizCmd(argv, cmd=cmd).run()

class BWizCmd(BaseCommand):
  ''' Command line handler.
  '''

  def cmd_cat(self, args):
    ''' Output the tvwiz transport stream data.

        Usage: {cmd} tvwizdirs...
          Write the video content of the named tvwiz directories to
          standard output as MPEG2 transport Stream, acceptable to
          ffmpeg's "mpegts" format.
    '''
    if not args:
      raise GetoptError("missing tvwizdirs")
    for arg in args:
      # NB: dup stdout so that close doesn't close real stdout
      stdout_bfd = os.dup(sys.stdout.fileno())
      stdout_bfp = os.fdopen(stdout_bfd, "wb")
      TVWiz(arg).copyto(stdout_bfp)
      stdout_bfp.close()
    return 0

  def cmd_convert(self, args):
    ''' Convert a recording to MP4.

        Usage: {cmd} recording [start..end]... [output.mp4]
          Convert the video content of the named recording to
          the named output file (typically MP4, though the ffmpeg
          output format chosen is based on the extension).
          Most metadata are preserved.
          start..end: Optional start and end offsets in seconds, used
            to crop the recording output.
    '''
    if not args:
      raise GetoptError("missing recording")
    srcpath = args.pop(0)
    badopts = False
    with Pfx(srcpath):
      # parse optional start..end arguments
      timespans = []
      while (args and args[0] and args[0].isdigit() and args[-1].isdigit()
             and '..' in args[0]):
        with Pfx(args[0]):
          try:
            start, end = args[0].split('..')
            start_s = float(start)
            end_s = float(end)
          except ValueError:
            # parse fails, not a range argument
            break
          else:
            # use this argument as a timespan
            args.pop(0)
            if start_s > end_s:
              warning("start:%s > end:%s", start, end)
              badopts = True
            timespans.append((start_s, end_s))
      # collect optional dstpath
      if args:
        dstpath = args.pop(0)
      else:
        dstpath = None
      if args:
        warning("extra arguments: %s", ' '.join(args))
        badopts = True
      if badopts:
        raise GetoptError("bad invocation")
      R = Recording(srcpath)
      return 0 if R.convert(dstpath, max_n=TRY_N, timespans=timespans) else 1

  def cmd_mconvert(self, args):
    ''' Usage: {cmd} recording...
          Convert multiple named recordings to automatically named .mp4 files
          in the current directory.
          Most metadata are preserved.
    '''
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
    ''' Usage: {cmd} recording...
          Report metadata for the supplied recordings.
    '''
    if not args:
      raise GetoptError("missing recordings")
    for filename in args:
      with Pfx(filename):
        R = Recording(filename)
        print(filename)
        print(pformat(R.metadata))
        print(R.DEFAULT_FILENAME_BASIS)
        print(R.filename(ext=DEFAULT_FORMAT))
    return 0

  def cmd_scan(self, args):
    ''' Scan a TVWiz directory.

        Usage: {cmd} recording...
          Scan the data structures of the supplied recordings.
    '''
    if not args:
      raise GetoptError("missing tvwizdirs")
    for tvwizdir in args:
      print(tvwizdir)
      total = 0
      chunkSize = 0
      chunkOff = 0
      for wizOffset, fileNum, flags, offset, size in TVWiz(tvwizdir
                                                           ).trunc_records():
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
    return 0

  def cmd_stat(self, args):
    ''' Report information about a recording.

        Usage: {cmd} tvwizdirs...
          Print some summary infomation for the named tvwiz directories.
    '''
    if not args:
      raise GetoptError("missing recordings")
    for pathname in args:
      R = Recording(pathname)
      print(pathname)
      for json_line in R.metadata._asjson(indent="  ").split("\n"):
        if json_line:
          print(" ", json_line)
    return 0

  def cmd_test(self, args):
    ''' Usage: {cmd}
          Run unit tests.
    '''
    host = args.pop(0)
    print("host =", host, "args =", args)
    WizPnP(host).test()
    return 0

if __name__ == '__main__':
  sys.exit(main(sys.argv, cmd=__package__))
