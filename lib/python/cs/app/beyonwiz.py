#!/usr/bin/python
#

''' Classes to support access to Beyonwiz TVWiz data structures
    and Beyonwiz devices via the net.
'''

from __future__ import print_function
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
    %s convert tvwizdir output.mp4
    %s header tvwizdirs...
    %s scan tvwizdirs...
    %s stat tvwizdirs...
    %s test'''

# constants related to headers
#
# See:
#  https://github.com/prl001/getWizPnP/blob/master/wizhdrs.h

# header filenames
TVHDR = 'header.tvwiz';
RADHDR = 'header.radwiz';

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
  usage = USAGE % (cmd, cmd, cmd, cmd, cmd, cmd)

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
      if outpath.startswith('-'):
        warning("invalid outpath, may not commence with dash: %r", outpath)
        return 2
      outprefix, outext = os.path.splitext(outpath)
      if not outext:
        warning("no extension on outpath, cannot infer output format for mmfpeg: %r", outpath)
        return 2
      ffmpeg_argv = ['ffmpeg', '-f', 'mpegts', '-i', '-', '-f', outext[1:], outpath]
      TV = TVWiz(tvwizdir)
      info("running: %r", ffmpeg_argv)
      P = Popen(ffmpeg_argv, stdin=PIPE)
      TV.copyto(P.stdin)
      P.stdin.close()
      xit = P.wait()
      if xit != 0:
        warning("ffmpeg failed, exit status %d", xit)
      return xit
    elif op == "header":
      for arg in args:
        print(arg)
        TV = TVWiz(arg)
        print(repr(TV.header()))
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
  unix_start = (mjd - 40587) * 24 * 3600 + start
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
  # hack: presume episode and synopsis are the first data after any following NULs
  tail = data[offset:].lstrip(b'\x00')
  epi_b = b''
  syn_b = b''
  if len(tail) > 0:
    epi_b, offset = unrle(tail, '<B')
    epi_b = epi_b.rstrip(b'\xff')
    tail = tail[offset:].lstrip(b'\x00')
    if len(tail) > 0:
      syn_b, offset = unrle(tail, '<H')
      syn_b = syn_b.rstrip(b'\xff')
      tail = tail[offset:].lstrip(b'\x00')
  if len(tail) > 0:
    warning("unparsed data (%d bytes) after synopsis: %r...", len(tail), tail[:64])
  episode = epi_b.decode('utf8', errors='replace')
  synopsis = syn_b.decode('utf8', errors='replace')
  return NS(lock=lock, mediaType=mediaType, inRec=inRec,
            svcName=svcName, evtName=evtName, episode=episode, synopsis=synopsis,
            mjd=mjd, start=start, unix_start=unix_start, dt_start=dt_start,
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
    title = title \
            .replace('_ ', ': ') \
            .replace('_s ', "'s ")
    dt = datetime.datetime.strptime(daytext+timetext, '%b.%d.%Y%H.%M')
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
      with open(output, "wb") as out:
        self.copyto(out)
    else:
      for buf in self.data():
        output.write(buf)

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
