#!/usr/bin/env python3

r'''
Beyonwiz PVR and TVWiz recording utilities.

Classes to support access to Beyonwiz TVWiz and Enigma2 on disc data
structures and to access Beyonwiz devices via the net.
'''

from abc import ABC, abstractmethod
import datetime
import errno
import json
import os.path
from os.path import join as joinpath, isdir as isdirpath
import re
from threading import Lock
from types import SimpleNamespace as NS
from cs.app.ffmpeg import (
    multiconvert as ffmconvert,
    MetaData as FFmpegMetaData,
    ConversionSource as FFSource,
)
from cs.deco import strable
from cs.fileutils import crop_name
from cs.fstags import HasFSTagsMixin
from cs.logutils import info, warning, error
from cs.mediainfo import EpisodeInfo
from cs.pfx import Pfx, pfx, pfx_method
from cs.py.func import prop
from cs.tagset import Tag

DISTINFO = {
    'keywords': ["python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.app.ffmpeg',
        'cs.binary',
        'cs.deco',
        'cs.fstags',
        'cs.logutils',
        'cs.mediainfo',
        'cs.pfx',
        'cs.py.func',
        'cs.tagset',
    ],
    'entry_points': {
        'console_scripts': [
            'beyonwiz = cs.app.beyonwiz:main',
        ],
    },
}

DEFAULT_FORMAT = 'mp4'

# UNUSED
def trailing_nul(bs):
  ''' Strip trailing `NUL`s
  '''
  bs = bs.rstrip(b'\x00')
  # locate preceeding NUL padded area
  start = bs.rfind(b'\x00')
  if start < 0:
    start = 0
  else:
    start += 1
  return start, bs[start:]

@pfx
def jsonable(value):
  if isinstance(value, (int, str, float)):
    return value
  # mapping?
  if hasattr(value, 'items') and callable(value.items):
    # mapping
    return {field: jsonable(subvalue) for field, subvalue in value.items()}
  if isinstance(value, (set, tuple, list)):
    return [jsonable(subvalue) for subvalue in value]
  if isinstance(value, datetime.datetime):
    return value.isoformat(' ')
  try:
    d = value._asdict()
  except AttributeError:
    pass
  else:
    return jsonable(d)
  raise TypeError('not JSONable value for %s:%r' % (type(value), value))

class MetaJSONEncoder(json.JSONEncoder):
  ''' `json.JSONEncoder` sublass with handlers for `set` and `datetime`.
  '''

  def default(self, o):
    if isinstance(o, set):
      return sorted(o)
    if isinstance(o, datetime.datetime):
      return o.isoformat(' ')
    return json.JSONEncoder.default(self, o)

class RecordingMetaData(NS):
  ''' Base class for recording metadata.
  '''

  @pfx_method
  def __init__(self, raw):
    self.raw = raw
    self.episodeinfo = EpisodeInfo()
    self.tags = set()

  def __getattr__(self, attr):
    try:
      return self.raw[attr]
    except KeyError:
      raise AttributeError(attr)

  def as_dict(self):
    ''' Return the metadata as a `dict`.
    '''
    d = dict(self.__dict__)
    d["start_dt_iso"] = self.start_dt_iso
    return d

  def as_json(self, indent=None):
    ''' Return the metadat as JSON.
    '''
    return MetaJSONEncoder(indent=indent).encode(self._asdict())

  @pfx_method
  def as_tags(self, prefix=None):
    ''' Generator yielding the metadata as `Tag`s.
    '''
    yield from (Tag(tag, prefix=prefix) for tag in self.tags)
    yield from self.episodeinfo.as_tags(prefix=prefix)
    for rawkey, rawvalue in self.raw.items():
      yield Tag(rawkey, jsonable(rawvalue), prefix=prefix)

  @property
  def start_dt(self):
    ''' Start of recording as a datetime.datetime.
    '''
    return datetime.datetime.fromtimestamp(self.start_unixtime)

  @property
  def start_dt_iso(self):
    ''' Start of recording in ISO8601 format.
    '''
    return self.start_dt.isoformat(' ')

def Recording(path):
  ''' Factory function returning a TVWiz or Enigma2 _Recording object.
  '''
  if path.endswith('.tvwiz'):
    from .tvwiz import TVWiz
    return TVWiz(path)
  if path.endswith('.ts'):
    from .enigma2 import Enigma2
    return Enigma2(path)
  raise ValueError("don't know how to open recording %r" % (path,))

class _Recording(ABC, HasFSTagsMixin):
  ''' Base class for video recordings.
  '''

  PATH_FIELDS = (
      'series_name', 'episode_info_part', 'episode_name', 'tags_part',
      'source_name', 'start_dt_iso', 'description'
  )

  def __init__(self, path, fstags=None):
    self._fstags = fstags
    self.path = path
    self._lock = Lock()

  def __getattr__(self, attr):
    if attr in (
        'description',
        'episodeinfo',
        'series_name',
        'source_name',
        'start_dt',
        'start_dt_iso',
        'start_unixtime',
        'tags',
        'title',
    ):
      return getattr(self.metadata, attr)
    raise AttributeError(attr)

  def filename(self, format=None, *, ext):
    ''' Compute a filename from `format` with extension `ext`.

        If `format` is omitted it defaults to `self.DEFAULT_FILENAME_BASIS`.
    '''
    if format is None:
      format = self.DEFAULT_FILENAME_BASIS
    if not ext.startswith('.'):
      ext = '.' + ext
    return crop_name(
        format.format_map(self.metadata.ns()
                          ).replace('\r', '_').replace('\n', '_') + ext,
        ext=ext
    )

  @abstractmethod
  def data(self):
    ''' Stub method for the raw video data method.
    '''
    raise NotImplementedError('data')

  @strable(open_func=lambda filename: open(filename, 'wb'))
  def copyto(self, output):
    ''' Transcribe the uncropped content to a file named by output.
        Requires the .data() generator method to yield video data chunks.
    '''
    for buf in self.data():
      output.write(buf)

  @prop
  def tags_part(self):
    ''' A filename component representing the metadata tags.
    '''
    return '+'.join(self.tags)

  @prop
  def episode_info_part(self):
    ''' A filename component representing the episode info.
    '''
    return str(self.metadata.episodeinfo)

  def converted_path(self, outext):
    ''' Generate the output filename with parts separated by '--'.
    '''
    parts = []
    for field in self.PATH_FIELDS:
      part = getattr(self, field, None)
      if part:
        part = str(part).lower().replace('/', '|').replace(' ', '-')
        part = re.sub('--+', '-', part)
        parts.append(part)
    filename = '--'.join(parts)
    filename = filename[:250 - (len(outext) + 1)]
    filename += '.' + outext
    return filename

  # TODO: move into cs.fileutils?
  @staticmethod
  def choose_free_path(path, max_n=32):
    ''' Find an available unused pathname based on `path`.
        Raises ValueError in none is available.
    '''
    basis, ext = os.path.splitext(path)
    for i in range(max_n):
      path2 = "%s--%d%s" % (basis, i + 1, ext)
      if not os.path.exists(path2):
        return path2
    raise ValueError(
        "no available --0..--%d variations: %r" % (max_n - 1, path)
    )

  def convert(
      self, dstpath, dstfmt=None, max_n=None, timespans=(), extra_opts=None
  ):
    ''' Transcode video to `dstpath` in FFMPEG `dstfmt`.
    '''
    if dstfmt is None:
      dstfmt = DEFAULT_FORMAT
    if not timespans:
      timespans = ((None, None),)
    srcfmt = 'mpegts'
    do_copyto = hasattr(self, 'data')
    if do_copyto:
      srcpath = None
      if len(timespans) > 1:
        raise ValueError(
            "%d timespans but do_copyto is true" % (len(timespans,))
        )
    else:
      srcpath = self.path
      # stop path looking like a URL
      if not os.path.isabs(srcpath):
        srcpath = os.path.join('.', srcpath)
    if dstpath is None:
      dstpath = self.filename(ext=dstfmt)
    elif dstpath.endswith('/'):
      dstpath += self.filename(ext=dstfmt)
    elif isdirpath(dstpath):
      dstpath = joinpath(dstpath, self.filename(ext=dstfmt))
    # stop path looking like a URL
    if not os.path.isabs(dstpath):
      dstpath = os.path.join('.', dstpath)
    ok = True
    with Pfx(dstpath):
      if os.path.exists(dstpath):
        ok = False
        if max_n is not None:
          try:
            dstpath = self.choose_free_path(dstpath, max_n)
          except ValueError as e:
            error("file exists: %s", e)
          else:
            ok = True
        else:
          error("file exists")
      if not ok:
        return ok
      if os.path.exists(dstpath):
        raise ValueError("dstpath exists")
      if dstfmt is None:
        _, ext = os.path.splitext(dstpath)
        if not ext:
          raise ValueError(
              "can't infer output format from dstpath, no extension"
          )
        dstfmt = ext[1:]
      fstags = self.fstags
      with fstags:
        fstags[dstpath].update(self.metadata.as_tags(prefix='beyonwiz'))
      ffmeta = self.ffmpeg_metadata(dstfmt)
      sources = []
      for start_s, end_s in timespans:
        sources.append(FFSource(srcpath, srcfmt, start_s, end_s))
      P, ffargv = ffmconvert(sources, dstpath, dstfmt, ffmeta, overwrite=False)
      info("running %r", ffargv)
      if do_copyto:
        # feed .copyto data to FFmpeg
        try:
          self.copyto(P.stdin)
        except OSError as e:
          if e.errno == errno.EPIPE:
            warning("broken pipe writing to ffmpeg")
            ok = False
          else:
            raise
        P.stdin.close()
      xit = P.wait()
      if xit == 0:
        ok = True
      else:
        warning("ffmpeg failed, exit status %d", xit)
        ok = False
      return ok

  def ffmpeg_metadata(self, dstfmt=None):
    ''' Return a new `FFmpegMetaData` containing our metadata.
    '''
    if dstfmt is None:
      dstfmt = DEFAULT_FORMAT
    M = self.metadata
    comment = f'Transcoded from {self.path!r} using ffmpeg.'
    recording_dt = M.get('file.datetime')
    if recording_dt:
      comment += f' Recording date {recording_dt.isoformat()}.'
    if M.tags:
      comment += ' tags=' + ','.join(sorted(M.tags))
    ## unused ## episode_marker = str(M.episodeinfo)
    return FFmpegMetaData(
        dstfmt,
        title=M['meta.title'],
        show=M['meta.title'],
        synopsis=M['meta.description'],
        network=M['file.channel'],
        comment=comment,
    )
