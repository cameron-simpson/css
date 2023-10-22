#!/usr/bin/env python
#

''' Command line utility for working with Beyonwiz PVR devices.
'''

from __future__ import print_function
from getopt import GetoptError
import json
import os
from os.path import (
    isdir as isdirpath,
    isfile as isfilepath,
    islink as islinkpath,
)
from pprint import pformat
import shutil
import sys

from cs.cmdutils import BaseCommand
from cs.ffmpegutils import ffprobe
from cs.logutils import warning
from cs.pfx import Pfx, pfx_call

from . import Recording, DEFAULT_MEDIAFILE_FORMAT
from .tvwiz import TVWiz
from .wizpnp import WizPnP

TRY_N = 32

def main(argv=None):
  ''' Main command line.
  '''
  return BWizCmd(argv).run()

class BWizCmd(BaseCommand):
  ''' Command line handler.
  '''

  def cmd_cat(self, argv):
    ''' Output the tvwiz transport stream data.

        Usage: {cmd} tvwizdirs...
          Write the video content of the named tvwiz directories to
          standard output as MPEG2 transport Stream, acceptable to
          ffmpeg's "mpegts" format.
    '''
    if not argv:
      raise GetoptError("missing tvwizdirs")
    for arg in argv:
      # NB: dup stdout so that close doesn't close real stdout
      stdout_bfd = os.dup(sys.stdout.fileno())
      stdout_bfp = os.fdopen(stdout_bfd, "wb")
      TVWiz(arg).copyto(stdout_bfp)
      stdout_bfp.close()
    return 0

  # pylint: disable=too-many-branches,too-many-locals
  def cmd_convert(self, argv):
    ''' Convert a recording to MP4.

        Usage: {cmd} [-n] [-a:afmt] [-v:vfmt] [--rm] [start..end]... recording [output.mp4]
          Convert the video content of the named recording to
          the named output file (typically MP4, though the ffmpeg
          output format chosen is based on the extension).
          Most metadata are preserved.
          start..end: Optional start and end offsets in seconds, used
            to crop the recording output.
    '''
    badopts = False
    doit = True
    acodec = None
    vcodec = None
    remove_source = False
    # parse options
    while argv:
      arg0 = argv.pop(0)
      with Pfx(arg0):
        if arg0 == '--':
          break
        if not arg0.startswith('-') or len(arg0) == 1:
          argv.insert(0, arg0)
          break
        if arg0 == '-n':
          doit = False
        elif arg0.startswith('-a:'):
          acodec = arg0[3:]
        elif arg0.startswith('-v:'):
          acodec = arg0[3:]
        elif arg0 == '--rm':
          remove_source = True
        else:
          warning('unexpected option')
          badopts = True
    # parse optional start..end arguments
    timespans = []
    while argv:
      range_arg = argv[0]
      with Pfx("timespan %r", range_arg):
        try:
          start, end = range_arg.split('..')
          start_s = float(start)
          end_s = float(end)
        except ValueError:
          break
        argv.pop(0)
        if start_s > end_s:
          warning("start:%s > end:%s", start, end)
          badopts = True
        timespans.append((start_s, end_s))
    if not argv:
      raise GetoptError("missing recording")
    srcpath = argv.pop(0)
    # collect optional dstpath
    if argv:
      dstpath = argv.pop(0)
    else:
      dstpath = None
    if argv:
      warning("extra arguments: %s", ' '.join(argv))
      badopts = True
    if badopts:
      raise GetoptError("bad invocation")
    R = Recording(srcpath)
    if not R.convert(
        dstpath,
        doit=doit,
        acodec=acodec,
        vcodec=vcodec,
        max_n=TRY_N,
        timespans=timespans,
    ):
      return 1
    if remove_source:
      if doit:
        with Pfx("remove %s", R.fspath):
          if islinkpath(R.fspath) or isfilepath(R.fspath):
            pfx_call(os.remove, R.fspath)
          elif isdirpath(R.fspath):
            pfx_call(shutil.rmtree, R.fspath)
          else:
            warning("cannot remove, not a file or directory")
            return 1
      else:
        print("remove", R.fspath)

  def cmd_ffprobe(self, argv):
    ''' Run `ffprobe` against a file.

        Usage: {cmd} media-file
          Probe media-file with "ffprobe" and print the result.
    '''
    filename, = argv
    probed = ffprobe(filename)
    print(json.dumps(probed, sort_keys=True, indent=2))

  def cmd_meta(self, argv):
    ''' Usage: {cmd} recording...
          Report metadata for the supplied recordings.
    '''
    if not argv:
      raise GetoptError("missing recordings")
    for filename in argv:
      with Pfx(filename):
        R = Recording(filename)
        print(filename)
        print(pformat(R.metadata))
        print(R.DEFAULT_FILENAME_BASIS)
        print("=>", R.filename(ext=DEFAULT_MEDIAFILE_FORMAT))
    return 0

  def cmd_scan(self, argv):
    ''' Scan a TVWiz directory.

        Usage: {cmd} recording...
          Scan the data structures of the supplied recordings.
    '''
    if not argv:
      raise GetoptError("missing tvwizdirs")
    for tvwizdir in argv:
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

  def cmd_stat(self, argv):
    ''' Report information about a recording.

        Usage: {cmd} tvwizdirs...
          Print some summary infomation for the named tvwiz directories.
    '''
    if not argv:
      raise GetoptError("missing recordings")
    for pathname in argv:
      R = Recording(pathname)
      print(pathname)
      for json_line in R.metadata._asjson(indent="  ").split("\n"):
        if json_line:
          print(" ", json_line)
    return 0

  def cmd_test(self, argv):
    ''' Usage: {cmd}
          Run unit tests.
    '''
    host = argv.pop(0)
    print("host =", host, "argv =", argv)
    WizPnP(host).test()
    return 0

if __name__ == '__main__':
  sys.exit(main(sys.argv))
