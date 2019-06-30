#!/usr/bin/python
#

''' Commandline main programme.
'''

from __future__ import print_function
from getopt import GetoptError
import json
import os.path
import sys
from cs.cmdutils import BaseCommand
from cs.logutils import setup_logging, warning, error
from cs.pfx import Pfx
from cs.x import X
from . import Recording
from .tvwiz import TVWiz
from .wizpnp import WizPnP

TRY_N = 32

def main(argv=None):
  ''' Main command line.
  '''
  if argv is None:
    argv = sys.argv
  setup_logging(os.path.basename(argv[0]))
  return BWizCmd().run(argv)

class BWizCmd(BaseCommand):
  ''' Command line handler.
  '''

  GETOPT_SPEC = ''

  USAGE_FORMAT = '''Usage:
      {cmd} cat tvwizdirs...
          Write the video content of the named tvwiz directories to
          standard output as MPEG2 transport Stream, acceptable to
          ffmpeg's "mpegts" format.
      {cmd} convert recording [start..end]... [output.mp4]
          Convert the video content of the named recording to
          the named output file (typically MP4, though the ffmpeg
          output format chosen is based on the extension).
          Most metadata are preserved.
          start..end: Optional start and end offsets in seconds, used
            to crop the recording output.
      {cmd} mconvert recording...
          Convert multiple named recordings to automatically named .mp4 files
          in the current directory.
          Most metadata are preserved.
      {cmd} meta recording...
          Report metadata for the supplied recordings.
      {cmd} scan recording...
          Scan the data structures of the supplied recordings.
      {cmd} stat tvwizdirs...
          Print some summary infomation for the named tvwiz directories.
      {cmd} test
          Run unit tests.'''

  @staticmethod
  def cmd_cat(args, options, cmd):
    ''' Output the tvwiz transport stream data.
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

  @staticmethod
  def cmd_convert(args, options, cmd):
    ''' Convert a recording to MP4.
    '''
    if not args:
      raise GetoptError("missing recording")
    srcpath = args.pop(0)
    with Pfx(srcpath):
      # parse optional start..end arguments
      timespans = []
      while (args and args[0] and args[0].isdigit() and args[-1].isdigit()
             and '..' in args[0]):
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
          # use this argument as a timespan
          args.pop(0)
          timespans.append((start_s, end_s))
      # collect optional dstpath
      if args:
        dstpath = args.pop(0)
      else:
        dstpath = None
      if args:
        raise GetoptError("extra arguments: %s" % (' '.join(args),))
      R = Recording(srcpath)
      return 0 if R.convert(dstpath, max_n=TRY_N, timespans=timespans) else 1

  @staticmethod
  def mconvert(args, options, cmd):
    ''' Convert multiple recordings to MP4.
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

  @staticmethod
  def cmd_meta(args, options, cmd):
    ''' Report metadata about recordings.
    '''
    if not args:
      raise GetoptError("missing recordings")
    for filename in args:
      with Pfx(filename):
        R = Recording(filename)
        print(filename, R.metadata._asjson(), sep='\t')
    return 0

  @staticmethod
  def scan(args, options, cmd):
    ''' Scan a TVWiz directory.
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

  @staticmethod
  def cmd_stat(args, options, cmd):
    ''' Report information about a recording.
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

  @staticmethod
  def cmd_test(args, options, cmd):
    ''' Run the self tests.
    '''
    host = args.pop(0)
    print("host =", host, "args =", args)
    WizPnP(host).test()
    return 0

if __name__ == '__main__':
  sys.exit(main(sys.argv))
