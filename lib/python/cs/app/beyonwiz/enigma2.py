#!/usr/bin/python
#
# Classes for modern Beyonwiz T2, T3 etc.
#   - Cameron Simpson <cs@cskk.id.au>
#

import errno
from collections import namedtuple
import datetime
import os.path
import struct
from cs.logutils import warning
from cs.pfx import Pfx
from cs.py.func import prop
from cs.threads import locked_property
from cs.x import X
from . import _Recording, RecordingMetaData

class Enigma2MetaData(RecordingMetaData):

  def __init__(self, raw):
    RecordingMetaData.__init__(self, raw)
    raw_meta = raw['meta']
    raw_file = raw['file']
    self.series_name = raw_meta['title']
    self.description = raw_meta['description']
    self.start_unixtime = raw_meta['start_unixtime']
    channel = raw_file['channel']
    if channel:
      self.source_name = channel
    self.tags.update(raw_meta['tags'])

class Enigma2(_Recording):
  ''' Access Enigma2 recordings, such as those used on the Beyonwiz T3, T4 etc devices.
      File format information from:
        https://github.com/oe-alliance/oe-alliance-enigma2/blob/master/doc/FILEFORMAT
  '''

  def __init__(self, tspath):
    _Recording.__init__(self, tspath)
    self.tspath = tspath
    self.metapath = tspath + '.meta'
    self.appath = tspath + '.ap'
    self.cutpath = tspath + '.cuts'

  def read_meta(self):
    ''' Read the .meta file and return the contents as a dict.
    '''
    path = self.metapath
    data = {
        'pathname': path,
        'tags': set(),
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
    return data

  @locked_property
  def metadata(self):
    ''' The metadata associated with this recording.
    '''
    return Enigma2MetaData(
        {
            'meta': self.read_meta(),
            'file': self.filename_metadata(),
        }
    )

  def filename_metadata(self):
    ''' Information about the recording inferred from the filename.
    '''
    path = self.tspath
    fmeta = {'pathname': path}
    base, _ = os.path.splitext(os.path.basename(path))
    fields = base.split(' - ', 2)
    if len(fields) != 3:
      warning('cannot parse into "time - channel - program": %r', base)
    else:
      time_field, channel, title = fields
      fmeta['channel'] = channel
      fmeta['title'] = title
      time_fields = time_field.split()
      if (len(time_fields) != 2 or not all(_.isdigit() for _ in time_fields)
          or len(time_fields[0]) != 8 or len(time_fields[1]) != 4):
        warning('mailformed time field: %r', time_field)
      else:
        ymd, hhmm = time_fields
        fmeta['datetime'] = datetime.datetime.strptime(
            ymd + hhmm, '%Y%m%d%H%M'
        )
        fmeta['start_time'] = ':'.join((hhmm[:2], hhmm[2:4]))
    return fmeta

  def _parse_path(self):
    basis, ext = os.path.splitext(self.tspath)
    if ext != '.ts':
      warning("does not end with .ts: %r", self.tspath)
    basis = os.path.basename(basis)
    ymd, hm, _, channel, _, title = basis.split(' ', 5)
    dt = datetime.datetime.strptime(ymd + hm, '%Y%m%d%H%M')
    return title, dt, channel

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
              warning(
                  "incomplete read (%d bytes) at offset %d", len(data),
                  apfp.tell() - len(data)
              )
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
              warning(
                  "incomplete read (%d bytes) at offset %d", len(data),
                  cutfp.tell() - len(data)
              )
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
          if not chunk:
            break
          yield chunk
