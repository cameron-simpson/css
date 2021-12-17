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

import ffmpeg

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

DEFAULT_MEDIAFILE_FORMAT = 'mp4'

FFMPEG_METADATA_MAPPINGS = {

    # available metadata for MP4 files
    'mp4': {
        'album': None,
        'album_artist': None,
        'author': None,
        'comment': None,
        'composer': None,
        'copyright': None,
        'description': None,
        'episode_id': None,
        'genre': None,
        'grouping': None,
        'lyrics': None,
        'network': lambda M: M['file.channel'],
        'show': lambda M: M['meta.title'],
        'synopsis': lambda M: M['meta.description'],
        'title': lambda M: M['meta.title'],
        'track': None,
        'year': None,
    }
}

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
    md = self.metadata
    full_filename = self.metadata.format_as(format
                                            ).replace('\r',
                                                      '_').replace('\n', '_')
    return crop_name(full_filename + ext, ext=ext)

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
      self,
      dstpath,
      *,
      dstfmt=None,
      max_n=None,
      timespans=(),
      extra_opts=None,
      overwrite=False,
      use_data=False,
  ):
    ''' Transcode video to `dstpath` in FFMPEG compatible `dstfmt`.
    '''
    if dstfmt is None:
      dstfmt = DEFAULT_MEDIAFILE_FORMAT
    if use_data:
      srcpath = None
      if timespans:
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
        metatags = list(self.metadata.as_tags(prefix='beyonwiz'))
        fstags[dstpath].update(metatags)
        fstags.sync()
    # compute the metadata for the output format
    # which may be passed with the input arguments
    M = self.metadata
    with Pfx("metadata for dstformat %r", dstfmt):
      ffmeta_kw = dict(comment=f'Transcoded from {self.path!r} using ffmpeg.')
      for ffmeta, beymeta in FFMPEG_METADATA_MAPPINGS[dstfmt].items():
        with Pfx("%r->%r", beymeta, ffmeta):
          if beymeta is None:
            continue
          elif isinstance(beymeta, str):
            ffmetavalue = M.get(beymeta, '')
          elif callable(beymeta):
            ffmetavalue = beymeta(M)
          else:
            raise RuntimeError(
                "unsupported beymeta %s:%r" %
                (type(beymeta).__name__, beymeta)
            )
          assert isinstance(ffmetavalue, str), (
              "ffmetavalue should be a str, got %s:%r" %
              (type(ffmetavalue).__name__, ffmetavalue)
          )
          ffmeta_kw[ffmeta] = beymeta(M)
    # set up the initial source path, options and metadata
    ffinopts = {
        'loglevel': 'repeat+error',
        'strict': None,
        ##'2': None,
    }
    ff = ffmpeg.input(srcpath, **ffinopts)
    if timespans:
      ffin = ff
      ff = ffmpeg.concat(
          *map(
              lambda timespan: ffin.trim(start=timespan[0], end=timespan[1]),
              timespans
          )
      )
    ff = ff.output(
        dstpath,
        format=dstfmt,
        metadata=list(map('='.join, ffmeta_kw.items()))
    )
    if overwrite:
      ff = ff.overwrite_output()
    print(ff.get_args())
    ff.run()
    return ok

  def ffmpeg_metadata(self, dstfmt=None):
    ''' Return a new `FFmpegMetaData` containing our metadata.
    '''
    if dstfmt is None:
      dstfmt = DEFAULT_MEDIAFILE_FORMAT
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
