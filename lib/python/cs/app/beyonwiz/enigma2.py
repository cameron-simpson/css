#!/usr/bin/python
#
# Classes for modern Beyonwiz T2, T3 etc.
#   - Cameron Simpson <cs@zip.com.au>
#

from . import _Recording, RecordingMeta
import os.path
from collections import namedtuple
import datetime
from cs.app.ffmpeg import MetaData as FFmpegMetaData
from cs.logutils import warning, Pfx
from cs.threads import locked_property

class Enigma2Meta(RecordingMeta):
  pass

class Enigma2(_Recording):
  ''' Access Enigma2 recordings, such as those used on the Beyonwiz T3, T4 etc devices.
      File format information from:
        https://github.com/oe-alliance/oe-alliance-enigma2/blob/master/doc/FILEFORMAT
  '''

  def __init__(self, tspath):
    _Recording.__init__(self)
    self.tspath = tspath
    self.metapath = tspath + '.meta'
    self.appath = tspath + '.ap'
    self.cutpath = tspath + '.cuts'
    self.path_title, self.path_datetime, self.path_channel = self._parse_path()

  def _parse_path(self):
    basis, ext = os.path.splitext(self.tspath)
    if ext != '.ts':
      warning("does not end with .ts: %r", self.tspath)
    basis = os.path.basename(basis)
    ymd, hm, _, channel, _, title = basis.split(' ', 5)
    dt = datetime.datetime.strptime(ymd + hm, '%Y%m%d%H%M')
    return title, dt, channel

  @locked_property
  def metadata(self):
    ''' Return the meta information from a recording's .meta associated file.
    '''
    path = self.metapath
    data = {
        'service_ref': None,
        'title': self.path_title,
        'description': None,
        'channel': self.path_channel,
        # start time of recording as a UNIX time
        'start_unixtime': None,
        'tags': set(),
        # length in PTS units (1/9000s)
        'length_pts': None,
        'filesize': None,
      }
    with Pfx("meta %r", path):
      try:
        with open(path) as metafp:
          data['service_ref'] = metafp.readline().rstrip()
          data['title'] = metafp.readline().rstrip()
          data['description'] = metafp.readline().rstrip()
          data['start_unixtime'] = int(metafp.readline().rstrip())
          data['tags'].update(metafp.readline().strip().split())
          data['length_pts'] = int(metafp.readline().rstrip())
          data['filesize'] = int(metafp.readline().rstrip())
      except OSError as e:
        if e.errno == errno.ENOENT:
          warning("cannot open: %s", e)
        else:
          raise
      return Enigma2Meta(**data)
  
  @property
  def start_dt_iso(self):
    return self.metadata.start_dt_iso

  def filename_metadata(self):
    ''' Information about the recording inferred from the filename.
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

  def path_parts(self):
    ''' The 3 components contributing to the .convertpath() method.
        The middle component may be trimmed to fit into a legal filename.
    '''
    M = self.metadata
    return M.title, '-'.join(M.tags), M.channel

  def ffmpeg_metadata(self, outfmt='mp4'):
    M = self.metadata
    comment = 'Transcoded from %r using ffmpeg. Recording date %s.' \
              % (self.tspath, M.start_dt_iso)
    if M.tags:
      comment += ' tags={%s}' % (','.join(sorted(M.tags)),)
    return FFmpegMetaData(outfmt,
                          title=M.title,
                          show=M.title,
                          description=M.description,
                          synopsis=M.description,
                          network=M.channel,
                          comment=comment,
                         )

  APInfo = namedtuple('APInfo', 'pts offset')
  CutInfo = namedtuple('CutInfo', 'pts type')

  def read_ap(self):
    ''' Read offsets and PTS information from a recording's .ap associated file.
        Return a list of APInfo named tuples.
    '''
    path = self.appath
    apdata = []
    with Pfx("read_ap %r", path):
      try:
        with open(path, 'rb') as apfp:
          while True:
            data = apfp.read(16)
            if not data:
              break
            if len(data) < 16:
              warning("incomplete read (%d bytes) at offset %d",
                      len(data), apfp.tell() - len(data))
              break
            pts, offset = struct.unpack('>QQ', data)
            apdata.append(Enigma2.APInfo(pts, offset))
      except OSError as e:
        if e.errno == errno.ENOENT:
          warning("cannot open: %s", e)
        else:
          raise
      return apdata

  def read_cuts(self):
    path = self.cutpath
    cuts = []
    with Pfx("read_cuts %r", path):
      try:
        with open(path, 'rb') as cutfp:
          while True:
            data = cutfp.read(12)
            if not data:
              break
            if len(data) < 12:
              warning("incomplete read (%d bytes) at offset %d",
                      len(data), cutfp.tell() - len(data))
              break
            pts, cut_type = struct.unpack('>QL', data)
            cuts.append(Enigma2.CutInfo(pts, cut_type))
      except OSError as e:
        if e.errno == errno.ENOENT:
          warning("cannot open: %s", e)
        else:
          raise
    X("cuts = %r", cuts)
    return cuts

  def data(self):
    ''' A generator that yields MPEG2 data from the stream.
    '''
    bufsize = 65536
    with Pfx("data(%s)", self.tspath):
      with open(self.tspath, 'rb') as tsfp:
        while True:
          chunk = tsfp.read(bufsize)
          if len(chunk) == 0:
            break
          yield chunk
