#!/usr/bin/python

from . import _Recording

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

TruncRecord = namedtuple('TruncRecord', 'wizOffset fileNum flags offset size')

def bytes0_to_str(bs0, encoding='utf8'):
  ''' Decode a NUL terminated chunk of bytes into a string.
  '''
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

class TVWiz_Header(RecordingMeta):

  @property
  def start_unixtime(self):
    return (self.mjd - 40587) * DAY + self.start

class TVWiz(_Recording):

  def __init__(self, wizdir):
    _Recording.__init__(self)
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

  @staticmethod
  def parse_header_data(data, offset=0):
    ''' Decode the data chunk from a TV or radio header chunk.
    '''
    h1, h2, h3, h4, h5, \
    lock, mediaType, inRec, unused \
      = TVWizFileHeader.unpack(data[offset:offset+TVWizFileHeader.size])
    # skip ahead to TSPoint information
    offset += 1024
    svcName, evtName, \
    mjd, pad, start, last, sec, lastOff \
      = TVWizTSPoint.unpack(data[offset:offset+TVWizTSPoint.size])
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
    return TVWiz_Header(lock=lock, mediaType=mediaType, inRec=inRec,
              svcName=svcName, evtName=evtName, episode=episode, synopsis=synopsis,
              mjd=mjd, start=start,
              playtime=last*10+sec, lastOff=lastOff)

  def read_header(self):
    with open(self.header_path, "rb") as hfp:
      data = hfp.read()
    return self.parse_header_data(data)

  @locked_property
  def metadata(self):
    return self.read_header()

  meta = header

  @staticmethod
  def tvwiz_parse_trunc(fp):
    ''' An iterator to yield TruncRecord tuples.
    '''
    while True:
      buf = fp.read(24)
      if len(buf) == 0:
        break
      if len(buf) != 24:
        raise ValueError("short buffer: %d bytes: %r" % (len(buf), buf))
      yield TruncRecord(*struct.unpack("<QHHQL", buf))

  def trunc_records(self):
    ''' Generator to yield TruncRecords for this TVWiz directory.
    '''
    with open(os.path.join(self.dirpath, "trunc"), "rb") as tfp:
      for trec in self.parse_trunc(tfp):
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

  def path_parts(self):
    M = self.metadata
    return M.evtName, M.episode, M.svcName

  def ffmpeg_metadata(self, format='mp4'):
    H = self.header
    return FFmpegMetaData(format,
                          title=( H.evtName
                                  if len(H.episode) == 0
                                  else '%s: %s' % (H.evtName, H.episode)
                                ),
                          show=H.evtName,
                          episode_id=H.episode,
                          synopsis=H.synopsis,
                          network=H.svcName,
                          comment='Transcoded from %r using ffmpeg. Recording date %s.'
                                  % (self.dirpath, H.start_dt_iso),
                         )
