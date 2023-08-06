#!/usr/bin/python

'''
TVWiz (pre-T3 Beyonwiz devices) specific support.
'''

from collections import namedtuple
from datetime import datetime
from os.path import (
    basename,
    join as joinpath,
    splitext,
)
import struct
from tempfile import NamedTemporaryFile
from typing import Tuple

from cs.binary import BinaryMultiStruct, BinarySingleStruct
from cs.buffer import CornuCopyBuffer
from cs.deco import promote
from cs.fileutils import datafrom
from cs.logutils import warning, error
from cs.pfx import pfx, pfx_call
from cs.tagset import TagSet
from cs.threads import locked_property

from . import _Recording

# constants related to headers
#
# See:
#  https://github.com/prl001/getWizPnP/blob/master/wizhdrs.h

# various constants sucked directly from getWizPnP/Beyonwiz/Recording/Header.pm
DAY = 24 * 60 * 60  # Seconds in a day
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
HDR_OFFSETS_SIZE = (MAX_TS_POINT - 1) * 8
HDR_BOOKMARKS_OFF = 79316
HDR_BOOKMARKS_SZ = 20 + MAX_BOOKMARKS * 8
HDR_EPISODE_OFF = 79856
HDR_EPISODE_SZ = 1 + 255
HDR_EXTINFO_OFF = 80114
HDR_EXTINFO_SZ = 2 + 1024

HEADER_DATA_OFF = HDR_MAIN_OFF
HEADER_DATA_SZ = HDR_EXTINFO_OFF + HDR_EXTINFO_SZ

# TVWizFileHeader: 5 unsigned shorts, then 4 bytes: lock, mediaType, inRec, unused
#
# from wizhdrs.h above:
#
# struct TVWizFileHeader { /* Offest 0 */
#     ushort      hidden[5];
#     uchar       lock;
#     uchar       mediaType;
#     uchar       inRec;
#     uchar       unused;
# };
#
TVWizFileHeader = BinaryMultiStruct(
    'TVWizFileHeader',
    '<5HBBBB',
    'hidden1 hidden2 hidden3 hidden4 hidden5 lock media_type in_rec unused',
)

# TOffset: unsigned long long last_offset, then 8640 unsigned long long fileOff
# TVWizTSPoint, offset 1024:
#    service_name       eg TV channel name
#    event_name         eg program title
#    mod_julian_date    Modified Julian Date
#                       http://tycho.usno.navy.mil/mod_julian_date.html
#    pad
#    start
#    last               play time = last*10 + sec
#    sec
#    last_offset
#    followed by 8640 fileOff
class TVWizTSPoint(BinaryMultiStruct(
    'TVWizTSPoint',
    '<256s256sHHLHHQ',
    'service_name_bs0 event_name_bs0 mod_julian_date pad start last sec last_offset',
)):

  @property
  def event_name(self):
    return bytes0_to_str(self.event_name_bs0)

  @property
  def service_name(self):
    return bytes0_to_str(self.service_name_bs0)

  @property
  def start_unixtime(self):
    return (self.mod_julian_date - 40587) * DAY + self.start

TVWizFileOffset = BinarySingleStruct(
    'TVWizFileOffset', '<Q', field_name='offset'
)

TruncRecord = namedtuple('TruncRecord', 'wizOffset fileNum flags offset size')

class TVWiz_Header(namedtuple(
    'TVWiz_Header',
    'file_header event_header file_offsets episode synopsis',
)):

  @classmethod
  @promote
  def parse(cls, bfr: CornuCopyBuffer) -> 'TVWiz_Header':
    bs_1024 = bfr.take(1024)
    fhdr = TVWizFileHeader.parse(bs_1024)
    evhdr = TVWizTSPoint.parse(bfr)
    file_offsets = [TVWizFileOffset.parse(bfr).value for _ in range(8640)]

    assert HDR_EPISODE_OFF > bfr.offset
    bfr.skipto(HDR_EPISODE_OFF)
    epi_bs = unrle(bfr, '<B').rstrip(b'\xff')
    episode = epi_bs.decode('utf8', errors='replace')

    assert HDR_EXTINFO_OFF > bfr.offset
    bfr.skipto(HDR_EXTINFO_OFF)
    syn_bs = unrle(bfr, '<H').rstrip(b'\xff')
    synopsis = syn_bs.decode('utf8', errors='replace')

    # sometimes the description seems to be in the episode
    # sometimes not
    if not synopsis:
      synopsis = episode
      if len(episode.split()) > 10:
        episode = ''

    return cls(
        file_header=fhdr,
        event_header=evhdr,
        file_offsets=file_offsets,
        episode=episode,
        synopsis=synopsis,
    )

def bytes0_to_str(bs0, encoding='utf8'):
  ''' Decode a NUL terminated chunk of bytes into a string.
  '''
  nulpos = bs0.find(0)
  if nulpos >= 0:
    bs0 = bs0[:nulpos]
  s0 = bs0.decode(encoding)
  return s0

def unrle(bfr: CornuCopyBuffer, fmt):
  ''' Decode a TVWiz run length encoded record. UNUSED.
  '''
  S = struct.Struct(fmt)
  length_bs = bfr.take(S.size)
  length, = S.unpack(length_bs)
  data = bfr.take(length)
  return data

class TVWiz(_Recording):
  ''' A TVWiz specific _Recording for pre-T3 Beyonwiz devices.
  '''
  DEFAULT_FILENAME_BASIS = '{series_name:lc}--{file_dt}--{service_name:lc}--beyonwiz--{description:lc}'

  FFMPEG_METADATA_MAPPINGS = {

      # available metadata for MP4 files
      'mp4': {
          'album': None,
          'album_artist': None,
          'author': None,
          'comment': None,
          'composer': None,
          'copyright': None,
          'description': lambda tags: tags.description,
          'episode_id': None,
          'genre': None,
          'grouping': None,
          'lyrics': None,
          'network': lambda tags: tags.service_name,
          'show': lambda tags: tags.series_name,
          'synopsis': lambda tags: tags.description,
          'title': lambda tags: tags.series_name,
          'track': None,
          'year': lambda tags: tags.file_dt.year,
      }
  }

  def __init__(self, wizdir):
    _Recording.__init__(self, wizdir)
    self.srcfmt = 'mpegts'
    self.path_title, self.path_datetime = self._parse_path()

  @property
  def headerpath(self):
    ''' The filesystem path of the header file. '''
    return self.pathto(TVHDR)

  def convert(self, dstpath, extra_opts=None, **kw):
    ''' Wrapper for _Recording.convert which requests audio conversion to AAC.
    '''
    tvwiz_extra_opts = [
        '-c:a',
        'aac',  # convert all audio to AAC
    ]
    if extra_opts:
      tvwiz_extra_opts.extend(extra_opts)
    with NamedTemporaryFile(prefix=basename(self.fspath) + '--',
                            suffix='.ts') as T:
      for bs in self.video_data():
        T.write(bs)
      T.flush()
      return super().convert(
          dstpath, srcpath=T.name, extra_opts=tvwiz_extra_opts, **kw
      )

  def _parse_path(self) -> Tuple[str, datetime]:
    basis, ext = splitext(basename(self.fspath))
    if ext != '.tvwiz':
      warning("does not end with .tvwiz: %r", self.fspath)
    title, daytext, timetext = basis.rsplit('_', 2)
    try:
      timetext, plustext = timetext.rsplit('+', 1)
    except ValueError:
      pass
    else:
      warning("discarding %r from timetext", "+" + plustext)
    title = title.replace('_ ', ': ').replace('_s ', "'s ")
    to_parse = daytext + timetext
    dt = pfx_call(datetime.strptime, to_parse, '%b.%d.%Y%H.%M')
    return title, dt

  @locked_property
  def metadata(self) -> TagSet:
    ''' The decoded metadata.
    '''
    tvhdr = TVWiz_Header.parse(self.headerpath)
    file_title, file_dt = self._parse_path()
    tags = TagSet(
        file_title=file_title,
        file_dt=file_dt,
        series_name=tvhdr.event_header.event_name,
        description=tvhdr.synopsis,
        start_unixtime=tvhdr.event_header.start_unixtime,
        service_name=tvhdr.event_header.service_name,
    )
    episode = tvhdr.episode
    try:
      episode_num = int(episode)
    except ValueError:
      tags.update(episode_title=episode)
    else:
      tags.update(episode=episode_num)
    return tags

  @staticmethod
  def tvwiz_parse_trunc(fp):
    ''' An iterator to yield TruncRecord tuples.
    '''
    while True:
      buf = fp.read(24)
      if not buf:
        break
      if len(buf) != 24:
        raise ValueError("short buffer: %d bytes: %r" % (len(buf), buf))
      yield TruncRecord(*struct.unpack("<QHHQL", buf))

  def trunc_records(self):
    ''' Generator to yield TruncRecords for this TVWiz directory.
    '''
    with open(joinpath(self.fspath, "trunc"), "rb") as tfp:
      for trec in self.tvwiz_parse_trunc(tfp):
        yield trec

  def video_filenames(self):
    ''' The video filenames in lexical order. '''
    return sorted(self.fnmatch('[0-9][0-9][0-9][0-9]'))

  def video_pathnames(self):
    ''' The filesystem paths of the video data files in lexical order. '''
    return [self.pathto(filename) for filename in self.video_filenames()]

  def video_data(self):
    ''' Generator yielding `bytes` instances from the video files. '''
    for path in self.video_pathnames():
      with open(path, 'rb') as vf:
        yield from datafrom(vf)

  @pfx
  def data(self):
    ''' A generator that yields MPEG2 data from the stream.
    '''
    # TODO: yield from a buffer, cropped?
    vf = None
    lastFileNum = None
    for rec in self.trunc_records():
      wizOffset, fileNum, flags, offset, size = rec
      if lastFileNum is None or lastFileNum != fileNum:
        if lastFileNum is not None:
          vf.close()
        vf = open(self.pathto("%04d" % (fileNum,)), "rb")
        filePos = 0
        lastFileNum = fileNum
      if filePos != offset:
        vf.seek(offset)
      while size > 0:
        rsize = min(size, 8192)
        buf = vf.read(rsize)
        assert len(buf) <= rsize
        if not buf:
          error("%s: unexpected EOF", vf)
          break
        yield buf
        size -= len(buf)
    if lastFileNum is not None:
      vf.close()
