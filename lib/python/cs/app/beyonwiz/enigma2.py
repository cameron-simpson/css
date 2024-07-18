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
import os
from os.path import basename, isfile as isfilepath, splitext

from icontract import require

from cs.binary import BinaryMultiStruct
from cs.buffer import CornuCopyBuffer
from cs.logutils import warning
from cs.pfx import Pfx, pfx_call, pfx_method
from cs.py3 import datetime_fromisoformat
from cs.tagset import TagSet
from cs.threads import locked_property

from . import _Recording

# an "access point" record from the .ap file
Enigma2APInfo = BinaryMultiStruct('Enigma2APInfo', '>QQ', 'pts offset')

# a "cut" record from the .cuts file
Enigma2Cut = BinaryMultiStruct('Enigma2Cut', '>QL', 'pts type')

class Enigma2(_Recording):
  ''' Access Enigma2 recordings, such as those used on the Beyonwiz T3, T4 etc devices.
      File format information from:
        https://github.com/oe-alliance/oe-alliance-enigma2/blob/master/doc/FILEFORMAT
  '''

  DEFAULT_FILENAME_BASIS = (
      '{meta.title:lc}--{file.datetime:lc}--{file.channel:lc}'
      '--beyonwiz--{meta.description:lc}'
  )

  FFMPEG_METADATA_MAPPINGS = {

      # available metadata for MP4 files
      'mp4': {
          'album': None,
          'album_artist': None,
          'author': None,
          'comment': None,
          'composer': None,
          'copyright': None,
          'description': 'meta.description',
          'episode_id': None,
          'genre': None,
          'grouping': None,
          'lyrics': None,
          'network': 'file.channel',
          'show': 'meta.title',
          'synopsis': 'meta.description',
          'title': 'meta.title',
          'track': None,
          'year': lambda tags: tags['file.datetime'].year,
      }
  }

  @require(lambda tspath: tspath.endswith('.ts'))
  def __init__(self, tspath: str):
    _Recording.__init__(self, tspath)
    self.srcfmt = 'mpegts'
    self.tspath = tspath
    tsbase = splitext(basename(tspath))[0]
    self.appath = tspath + '.ap'
    self.cutpath = tspath + '.cuts'
    self.eitpath = tsbase + '.eit'
    self.metapath = tspath + '.meta'
    self.scpath = tspath + '.sc'

  @property
  def fspaths(self):
    ''' All the filesystem paths associated with the recording.
    '''
    return (
        self.tspath,
        self.appath,
        self.cutpath,
        self.eitpath,
        self.metapath,
        self.scpath,
    )

  @pfx_method
  def remove(self, *, doit=False):
    ''' Remove all the files associated with this recording.
    '''
    for fspath in self.fspaths:
      with Pfx(fspath):
        print("remove", fspath)
        if doit:
          pfx_call(os.remove, fspath)
        else:
          if not isfilepath(fspath):
            warning("not a file")

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
        with open(path, 'r', encoding='utf8') as metafp:
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
  def metadata(self) -> TagSet:
    ''' The metadata associated with this recording as a `TagSet`.

        meta.* comes from `self.read_meta()`.
        file.* comes from `self.filename_metadata()`.
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
    base, _ = splitext(basename(path))
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
    basis, ext = splitext(self.tspath)
    if ext != '.ts':
      warning("does not end with .ts: %r", self.tspath)
    basis = basename(basis)
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
