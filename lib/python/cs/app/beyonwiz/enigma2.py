#!/usr/bin/python
#
# Classes for modern Beyonwiz T2, T3 etc.
#   - Cameron Simpson <cs@cskk.id.au>
#

''' Beyonwiz Enigma2 support, their modern recording format.
'''

import errno
from collections import namedtuple
import datetime
import os.path
from cs.binary import BinaryMultiValue
from cs.buffer import CornuCopyBuffer
from cs.logutils import warning
from cs.pfx import Pfx, pfx_method
from cs.py3 import datetime_fromisoformat
from cs.tagset import TagSet
from cs.threads import locked_property
from cs.x import X
from . import _Recording, RecordingMetaData

# an "access poiint" record from the .ap file
Enigma2APInfo = BinaryMultiValue('Enigma2APInfo', '>QQ', 'pts offset')

# a "cut" record from the .cuts file
Enigma2Cut = BinaryMultiValue('Enigma2Cut', '>QL', 'pts type')

class Enigma2(_Recording):
  ''' Access Enigma2 recordings, such as those used on the Beyonwiz T3, T4 etc devices.
      File format information from:
        https://github.com/oe-alliance/oe-alliance-enigma2/blob/master/doc/FILEFORMAT
  '''

  DEFAULT_FILENAME_BASIS = '{meta.title_lc}--{file.channel_lc}--beyonwiz--{file.datetime}--{meta.description_lc}'

  def __init__(self, tspath):
    _Recording.__init__(self, tspath)
    self.srcfmt = 'mpegts'
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
    tags = TagSet()
    tags.update(self.read_meta(), prefix='meta')
    tags.update(self.filename_metadata(), prefix='file')
    ##tags.update( self.read_cuts(),prefix='cuts')
    ##tags.update({'ap': self.read_ap()})
    return tags

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
        isodate = (
            ymd[:4] + '-' + ymd[4:6] + '-' + ymd[6:8] + 'T' + hhmm[:2] + ':' +
            hhmm[2:4] + ':00'
        )
        fmeta['datetime'] = datetime_fromisoformat(isodate)
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

  @staticmethod
  def scanpath(path, packet_type):
    ''' Generator yielding packets from `path`,
        issues a warning if the file is short or missing.
    '''
    with Pfx(path):
      try:
        with open(path, 'rb') as f:
          bfr = CornuCopyBuffer.from_file(f)
          try:
            yield from packet_type.scan(bfr)
          except EOFError as e:
            warning("short file: %s", e)
      except OSError as e:
        if e.errno == errno.ENOENT:
          warning("cannot open: %s", e)
        else:
          raise
      except EOFError as e:
        warning("short file: %s", e)

  @pfx_method
  def read_ap(self):
    ''' Read offsets and PTS information from a recording's .ap associated file.
        Return a list of APInfo named tuples.
    '''
    return list(self.scanpath(self.appath, Enigma2APInfo))

  @pfx_method
  def read_cuts(self):
    ''' Read the edit cuts and return a list of `Enigma2.CutInfo`s.
    '''
    return list(self.scanpath(self.cutpath, Enigma2Cut))

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
