#!/usr/bin/python
#

from __future__ import print_function

''' Classes to support access to Beyonwiz TVWiz on disc data structures
    and to Beyonwiz devices via the net.
'''

DISTINFO = {
    'description': "Beyonwiz PVR and TVWiz recording utilities",
    'keywords': ["python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
        ],
    'requires': ['cs.logutils', 'cs.obj', 'cs.threads', 'cs.urlutils'],
    'entry_points': {
      'console_scripts': [
          'beyonwiz = cs.app.beyonwiz:main',
          ],
    },
}

import sys
import os
import os.path
from collections import namedtuple
import datetime
import json
import struct
from subprocess import Popen, PIPE
from threading import Lock, RLock
from types import SimpleNamespace as NS
from xml.etree.ElementTree import XML
from cs.logutils import Pfx, error, warning, info, setup_logging, X
from cs.obj import O
from cs.threads import locked_property
from cs.urlutils import URL

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
    %s header tvwizdirs...
        Print header information from the named tvwiz directories.
    %s mconvert tvwizdirs...
        Convert the video content of the named tvwiz directories to
        automatically named .mp4 files in the current directory.
        Most metadata are preserved.
    %s scan tvwizdirs...
        Scan the data structures of the named tvwiz directories.
    %s stat tvwizdirs...
        Print some summary infomation for the named tvwiz directories.
    %s test
        Run unit tests.'''

# constants related to headers
#
# See:
#  https://github.com/prl001/getWizPnP/blob/master/wizhdrs.h

# various constants sucked directly from getWizPnP/Beyonwiz/Recording/Header.pm
DAY = 24*60*60      # Seconds in a day
TVEXT = '.tvwiz'
TVHDR = 'header' + TVEXT
RADEXT = '.radwiz'
RADHDR = 'header' + RADEXT

MAX_TS_POINT = 8640
HDR_SIZE = 256 * 1024
MAX_BOOKMARKS = 64

HDR_MAIN_OFF = 0
HDR_MAIN_SZ = 1564
HDR_OFFSETS_OFF = 1564
HDR_OFFSETS_SIZE = (MAX_TS_POINT-1) * 8
HDR_BOOKMARKS_OFF = 79316
HDR_BOOKMARKS_SZ = 20 + MAX_BOOKMARKS * 8
HDR_EPISODE_OFF = 79856
HDR_EPISODE_SZ = 1 + 255
HDR_EXTINFO_OFF = 80114
HDR_EXTINFO_SZ = 2 + 1024

HEADER_DATA_OFF = HDR_MAIN_OFF
HEADER_DATA_SZ = HDR_EXTINFO_OFF + HDR_EXTINFO_SZ

# TVWizFileHeader: 5 unsigned shorts, then 4 bytes: lock, mediaType, inRec, unused
TVWizFileHeader = struct.Struct('<HHHHHBBBB')
# TOffset: unsigned long long lastOff, then 8640 unsigned long long fileOff
# TVWizTSPoint, offset 1024:
#    svcName    eg TV channel name
#    evtName    eg program title
#    mjd        Modified Julian Date
#                 http://tycho.usno.navy.mil/mjd.html
#    pad
#    start
#    last       play time = last*10 + sec
#    sec
#    lastOff
# followed by 8640 fileOff
TVWizTSPoint = struct.Struct('<256s256sHHLHHQ')

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
      elif op == "header":
        if len(args) < 1:
          error("missing tvwizdirs")
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
      tvwizdir, outpath = args
      TV = TVWiz(tvwizdir)
      xit = TV.convert(outpath)
    elif op == "header":
      for tvwizdir in args:
        with Pfx(tvwizdir):
          print(tvwizdir)
          TV = TVWiz(tvwizdir)
          print(repr(TV.header()))
    elif op == "mconvert":
      for tvwizdir in args:
        with Pfx(tvwizdir):
          ok = True
          TV = TVWiz(tvwizdir)
          H = TV.header()
          outleft = "{iso}--{evtName}--".format_map(H.__dict__)
          outright = "--{svcName}.mp4".format_map(H.__dict__)
          outmiddle = H.episode[:255-len(outleft)-len(outright)]
          outpath = ( outleft + outmiddle + outright ) \
                    .replace('/', '|') \
                    .replace(' ', '-') \
                    .replace('----', '--')
          if os.path.exists(outpath):
            outpfx, outext = os.path.splitext(outpath)
            ok = False
            for i in range(32):
              outpath2 = "%s--%d%s" % (outpfx, i+1, outext)
              if not os.path.exists(outpath2):
                outpath = outpath2
                ok = True
                break
            if not ok:
              error("file exists, and so do most -n flavours of it: %r", outpath)
              xit = 1
          if ok:
            try:
              ffxit = TV.convert(outpath)
            except ValueError as e:
              error("%s: %s", outpath, e)
              xit = 1
    elif op == "meta":
      for filename in args:
        with Pfx(filename):
          try:
            meta = get_metadata(filename)
          except ValueError as e:
            error(e)
            xit = 1
          else:
            print(filename, json.dumps(meta, sort_keys=True), sep='\t')
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
        H = TV.header()
        print(arg)
        print("  %s %s: %s, %s" % (H.svcName, H.dt_start.isoformat(' '), H.evtName, H.episode))
    elif op == "test":
      host = args.pop(0)
      print("host =", host, "args =", args)
      WizPnP(host).test()
    else:
      error("unsupported operation: %s" % op)
      xit = 2

  return xit

def get_metadata(target):
  if target.endswith('.ts') and os.path.isfile(target):
    metadata = TnMovie(target).metadata()
  elif target.endswith('.tvwiz') and os.path.isdir(target):
    metadata = meta_tvwizdir(target)
  else:
    raise ValueError('not a .ts file or a .tvwiz dir: %r' % (target,))
  return metadata

class TnMovie(O):
  ''' A class that knows about a modern Beyonwiz T2, T3 etc recording.
  '''

  def __init__(self, filename):
    ''' Initialise with `filename`, the recording's .ts file.
    '''
    if not filename.endswith('.ts'):
      raise ValueError('not a .ts file')
    self.filename = filename

  @property
  def meta_filename(self):
    return self.filename + '.meta'

  @property
  def cuts_filename(self):
    return self.filename + '.cuts'

  def metadata(self):
    ''' Information about the recording.
    '''
    meta = {}
    base, ext = os.path.splitext(os.path.basename(self.filename))
    fields = base.split(' - ', 2)
    if len(fields) != 3:
      warning('cannot parse into "time - channel - program": %r', base)
    else:
      time_field, channel, title = fields
      meta['channel'] = channel
      meta['title'] = title
      time_fields = time_field.split()
      if ( len(time_fields) != 2
        or not all(_.isdigit() for _ in time_fields)
        or len(time_fields[0]) != 8 or len(time_fields[1]) != 4
         ):
        warning('mailformed time field: %r', time_field)
      else:
        ymd, hhmm = time_fields
        meta['date'] = '-'.join( (ymd[:4], ymd[4:6], ymd[6:8]) )
        meta['start_time'] = ':'.join( (hhmm[:2], hhmm[2:4]) )
    mfname = self.meta_filename
    with Pfx(mfname):
      with open(mfname) as mfp:
        hdr = mfp.readline()
        title2 = mfp.readline().strip()
        if title2 and title2 != title:
          warning('override file title')
          meta['title_from_filename'] = title
          meta['title'] = title2
        synopsis = mfp.readline().strip()
        if synopsis:
          meta['synopsis'] = synopsis
        start_time_unix = mfp.readline().strip()
        if start_time_unix.isdigit():
          meta['start_time_unix'] = int(start_time_unix)
    return meta

TruncRecord = namedtuple('TruncRecord', 'wizOffset fileNum flags offset size')

def parse_trunc(fp):
  ''' An iterator to yield TruncRecord tuples.
  '''
  while True:
    buf = fp.read(24)
    if len(buf) == 0:
      break
    if len(buf) != 24:
      raise ValueError("short buffer: %d bytes: %r" % (len(buf), buf))
    yield TruncRecord(*struct.unpack("<QHHQL", buf))

def parse_header_data(data, offset=0):
  ''' Decode the data chunk from a TV or radio header chunk.
  '''
  h1, h2, h3, h4, h5, \
  lock, mediaType, inRec, unused = TVWizFileHeader.unpack(data[offset:offset+TVWizFileHeader.size])
  # skip ahead to TSPoint information
  offset += 1024
  svcName, evtName, \
  mjd, pad, start, last, sec, lastOff = TVWizTSPoint.unpack(data[offset:offset+TVWizTSPoint.size])
  unix_start = (mjd - 40587) * DAY + start
  dt_start = datetime.datetime.fromtimestamp(unix_start)
  svcName = bytes0_to_str(svcName)
  evtName = bytes0_to_str(evtName)
  # advance to file offsets
  offset += TVWizTSPoint.size
  fileOffs = []
  for i in range(0, 8640):
    fileOff, = struct.unpack('<Q', data[offset:offset+8])
    fileOffs.append(fileOff)
    offset += 8
  epi_b, offset = unrle(data[HDR_EPISODE_OFF:HDR_EPISODE_OFF+HDR_EPISODE_SZ], '<B')
  epi_b = epi_b.rstrip(b'\xff')
  syn_b, offset = unrle(data[HDR_EXTINFO_OFF:HDR_EXTINFO_OFF+HDR_EXTINFO_SZ], '<H')
  syn_b = syn_b.rstrip(b'\xff')
  episode = epi_b.decode('utf8', errors='replace')
  synopsis = syn_b.decode('utf8', errors='replace')
  return NS(lock=lock, mediaType=mediaType, inRec=inRec,
            svcName=svcName, evtName=evtName, episode=episode, synopsis=synopsis,
            mjd=mjd, start=start, unix_start=unix_start, dt_start=dt_start,
            iso=dt_start.isoformat(' '),
            playtime=last*10+sec, lastOff=lastOff)

def bytes0_to_str(bs0, encoding='utf8'):
  nulpos = bs0.find(0)
  if nulpos >= 0:
    bs0 = bs0[:nulpos]
  s = bs0.decode(encoding)
  return s

def unrle(data, fmt, offset=0):
  offset0 = offset
  S = struct.Struct(fmt)
  offset2 = offset + S.size
  length, = S.unpack(data[offset:offset2])
  offset = offset2
  offset2 += length
  subdata = data[offset:offset2]
  if length != len(subdata):
    warning("unrle(%r...): rle=%d but len(subdata)=%d", data[offset0:offset0+16], length, len(subdata))
  offset += len(subdata)
  return subdata, offset

def trailing_nul(bs):
  # strip trailing NULs
  bs = bs.rstrip(b'\x00')
  # locate preceeding NUL padded area
  start = bs.rfind(b'\x00')
  if start < 0:
    start = 0
  else:
    start += 1
  return start, bs[start:]

class TVWiz(O):
  def __init__(self, wizdir):
    self.dirpath = wizdir
    self.path_title, self.path_datetime = self._parse_path()

  def _parse_path(self):
    basis, ext = os.path.splitext(self.dirpath)
    if ext != '.tvwiz':
      warning("does not end with .tvwiz: %r", self.dirpath)
    title, daytext, timetext = basis.rsplit('_', 2)
    try:
      timetext, plustext = timetext.rsplit('+', 1)
    except ValueError:
      pass
    else:
      warning("discarding %r from timetext", "+" + plustext)
    title = title \
            .replace('_ ', ': ') \
            .replace('_s ', "'s ")
    to_parse = daytext + timetext
    dt = datetime.datetime.strptime(to_parse, '%b.%d.%Y%H.%M')
    return title, dt

  @property
  def header_path(self):
    return os.path.join(self.dirpath, TVHDR)

  def header(self):
    with open(self.header_path, "rb") as hfp:
      data = hfp.read()
    return parse_header_data(data)

  def trunc_records(self):
    ''' Generator to yield TruncRecords for this TVWiz directory.
    '''
    with open(os.path.join(self.dirpath, "trunc"), "rb") as tfp:
      for trec in parse_trunc(tfp):
        yield trec

  def data(self):
    ''' A generator that yields MPEG2 data from the stream.
    '''
    with Pfx("data(%s)", self.dirpath):
      lastFileNum = None
      for rec in self.trunc_records():
        wizOffset, fileNum, flags, offset, size  = rec
        if lastFileNum is None or lastFileNum != fileNum:
          if lastFileNum is not None:
            fp.close()
          fp = open(os.path.join(self.dirpath, "%04d" % (fileNum,)), "rb")
          filePos = 0
          lastFileNum = fileNum
        if filePos != offset:
          fp.seek(offset)
        while size > 0:
          rsize = min(size, 8192)
          buf = fp.read(rsize)
          assert len(buf) <= rsize
          if len(buf) == 0:
            error("%s: unexpected EOF", fp)
            break
          yield buf
          size -= len(buf)
      if lastFileNum is not None:
        fp.close()

  def copyto(self, output):
    ''' Transcribe the uncropped content to a file named by output.
    '''
    if type(output) is str:
      outpath = output
      with open(outpath, "wb") as output:
        self.copyto(output)
    else:
      for buf in self.data():
        output.write(buf)

  def convert(self, outpath, format=None):
    ''' Transcode video to `outpath` in FFMPEG `format`.
    '''
    if os.path.exists(outpath):
      raise ValueError("outpath exists")
    if format is None:
      _, ext = os.path.splitext(outpath)
      if not ext:
        raise ValueError("can't infer format from outpath, no extension")
      format = ext[1:]
    # prevent output path looking like option or URL
    if not os.path.isabs(outpath):
      outpath = os.path.join('.', outpath)
    H = self.header()
    ffmpeg_argv = [ 'ffmpeg', '-f', 'mpegts', '-i', '-',
                              '-f', format,
                              '-metadata', 'title='
                                           + ( H.evtName
                                               if len(H.episode) == 0
                                               else '%s: %s' % (H.evtName, H.episode)
                                             ),
                              '-metadata', 'show='+H.evtName,
                              '-metadata', 'episode_id='+H.episode,
                              '-metadata', 'synopsis='+H.synopsis,
                              '-metadata', 'network='+H.svcName,
                              '-metadata', 'comment=Transcoded from %r using ffmpeg. Recording date %s.' % (self.dirpath, H.iso),
                              outpath]
    info("running: %r", ffmpeg_argv)
    P = Popen(ffmpeg_argv, stdin=PIPE)
    self.copyto(P.stdin)
    P.stdin.close()
    xit = P.wait()
    if xit != 0:
      warning("ffmpeg failed, exit status %d", xit)
    return xit


class WizPnP(O):
  ''' Class to access a pre-T3 beyonwiz over HTTP.
  '''

  def __init__(self, host, port=None):
    if port is None:
      port = 49152
    self.host = host
    self.port = port
    self.base = URL('http://%s:%d/' % (host, port), None)
    self._lock = RLock()

  def test(self):
    print(self.tvdevicedesc_URL)
    print(self._tvdevicedesc_XML)
    print(self.specVersion)
    for label, path in self.index:
      print(label, path)

  def url(self, subpath):
    U = URL(self.base + subpath, self.base)
    print("url(%s) = %s" % (subpath, U))
    return U

  @locked_property
  def tvdevicedesc_URL(self):
    return self.url('tvdevicedesc.xml')

  @locked_property
  def _tvdevicedesc_XML(self):
    return XML(self.tvdevicedesc_URL.content)

  @locked_property
  def specVersion(self):
    xml = self._tvdevicedesc_XML
    specVersion = xml[0]
    major, minor = specVersion
    return int(major.text), int(minor.text)

  @locked_property
  def index_txt(self):
    return self.url('index.txt').content

  @locked_property
  def index(self):
    idx = []
    for line in self.index_txt.split('\n'):
      if line.endswith('\r'):
        line = line[:-1]
      if len(line) == 0:
        continue
      try:
        label, path = line.split('|', 1)
        print("label =", label, "path =", path)
      except ValueError:
        print("bad index line:", line)
      else:
        idx.append( (label, os.path.dirname(path)) )
    return idx

  def tvwiz_header(self, path):
    ''' Fetch the bytes of the tvwiz header file for the specified recording path.
    '''
    return self.url(os.path.join(path, 'header.tvwiz'))

if __name__ == '__main__':
  sys.exit(main(sys.argv))
