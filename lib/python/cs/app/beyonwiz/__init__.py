#!/usr/bin/env python3

r'''
Beyonwiz PVR and TVWiz recording utilities.

Classes to support access to Beyonwiz TVWiz and Enigma2 on disc data
structures and to access Beyonwiz devices via the net.
'''

from abc import ABC, abstractmethod
import datetime
import json
import os.path
from os.path import (
    exists as existspath,
    isabs as isabspath,
    isdir as isdirpath,
    isfile as isfilepath,
    join as joinpath,
    splitext,
)
from threading import Lock
from types import SimpleNamespace as NS

from cs.deco import strable
from cs.ffmpegutils import (
    MetaData as FFmpegMetaData,
    convert as ffconvert,
)
from cs.fileutils import crop_name
from cs.fs import HasFSPath
from cs.fstags import HasFSTagsMixin
from cs.logutils import error
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
        'cs.ffmpegutils',
        'cs.binary',
        'cs.deco',
        'cs.fs',
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

# TODO: moved to cs.sqltags, can we obviate its use entirely in this package?
@pfx
def jsonable(value):
  ''' Return a JSON encodable version of `value`. '''
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
      raise AttributeError(attr)  # pylint: disable=raise-missing-from

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
  ''' Factory function returning a TVWiz or Enigma2 `_Recording` object.
  '''
  if isdirpath(path) and path.endswith('.tvwiz'):
    from .tvwiz import TVWiz  # pylint: disable=import-outside-toplevel
    return TVWiz(path)
  if isfilepath(path) and path.endswith('.ts'):
    from .enigma2 import Enigma2  # pylint: disable=import-outside-toplevel
    return Enigma2(path)
  if not existspath(path):
    # see if we were given a prefix from command line filename completion
    if path.endswith('.'):
      return Recording(path + 'ts')
    return Recording(path + '.ts')
  raise ValueError("don't know how to open recording %r" % (path,))

class _Recording(ABC, HasFSPath, HasFSTagsMixin):
  ''' Base class for video recordings.
  '''

  def __init__(self, fspath, fstags=None):
    HasFSPath.__init__(self, fspath)
    self._fstags = fstags
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

  # pylint: disable=redefined-builtin
  def filename(self, format=None, *, ext):
    ''' Compute a filename from `format` with extension `ext`.

        If `format` is omitted it defaults to `self.DEFAULT_FILENAME_BASIS`.
    '''
    if format is None:
      format = self.DEFAULT_FILENAME_BASIS
    if not ext.startswith('.'):
      ext = '.' + ext
    md = self.metadata
    full_filename = md.format_as(format).replace('\r', '_').replace('\n', '_')
    return crop_name(full_filename + ext, ext=ext)

  @abstractmethod
  def data(self):
    ''' Stub method for the raw video data method.
    '''
    raise NotImplementedError('data')

  @strable(open_func=lambda filename: open(filename, 'wb'))  # pylint: disable=consider-using-with
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
    basis, ext = splitext(path)
    for i in range(max_n):
      path2 = "%s--%d%s" % (basis, i + 1, ext)
      if not existspath(path2):
        return path2
    raise ValueError(
        "no available --0..--%d variations: %r" % (max_n - 1, path)
    )

  # pylint: disable=too-many-branches,too-many-locals
  def convert(
      self,
      dstpath,
      *,
      doit=True,
      dstfmt=None,
      max_n=None,
      timespans=(),
      extra_opts=None,
      overwrite=False,
      srcpath=None,
      use_data=False,
      acodec=None,
      vcodec=None,
  ):
    ''' Transcode video to `dstpath` in FFMPEG compatible `dstfmt`.
    '''
    fstags = self.fstags
    if dstfmt is None:
      dstfmt = DEFAULT_MEDIAFILE_FORMAT
    if use_data:
      assert srcpath is None
      if timespans:
        raise ValueError(
            "%d timespans but do_copyto is true" % (len(timespans,))
        )
    else:
      if srcpath is None:
        srcpath = self.fspath
      # stop srcpath looking like a URL
      if not isabspath(srcpath):
        srcpath = joinpath('.', srcpath)
    if dstpath is None:
      dstpath = self.filename(ext=dstfmt)
    elif dstpath.endswith('/'):
      dstpath += self.filename(ext=dstfmt)
    elif isdirpath(dstpath):
      dstpath = joinpath(dstpath, self.filename(ext=dstfmt))
    # stop dstpath looking like a URL
    if not isabspath(dstpath):
      dstpath = joinpath('.', dstpath)
    ok = True
    with Pfx(dstpath):
      if existspath(dstpath):
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
      if existspath(dstpath):
        raise ValueError("dstpath exists")
      if dstfmt is None:
        _, ext = splitext(dstpath)
        if not ext:
          raise ValueError(
              "can't infer output format from dstpath, no extension"
          )
        dstfmt = ext[1:]
      if doit:
        with fstags:
          metatags = list(self.metadata.as_tags(prefix='beyonwiz'))
          fstags[dstpath].update(metatags)
          fstags.sync()
    if not doit:
      print(srcpath)
      print("  =>", dstpath)
    ffconvert(
        srcpath,
        dstpath=dstpath,
        doit=doit,
        conversions=None,
        metadata=self.ffmetadata(dstfmt),
        timespans=timespans,
        overwrite=overwrite,
        acodec=acodec,
        vcodec=vcodec,
    )
    return True

  def ffmpeg_metadata(self, dstfmt=None):
    ''' Return a new `FFmpegMetaData` containing our metadata.
    '''
    if dstfmt is None:
      dstfmt = DEFAULT_MEDIAFILE_FORMAT
    M = self.metadata
    comment = f'Transcoded from {self.fspath!r} using ffmpeg.'
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

  def ffmetadata(self, dstfmt: str) -> dict:
    ''' Compute the metadata for the output format
        which may be passed with the input arguments.
    '''
    M = self.metadata
    with Pfx("metadata for dstformat %r", dstfmt):
      for ffmeta, beymeta in FFMPEG_METADATA_MAPPINGS[dstfmt].items():
      ffmeta_kw = dict(
          comment=f'Transcoded from {self.fspath!r} using ffmpeg.'
      )
        with Pfx("%r->%r", beymeta, ffmeta):
          if beymeta is None:
            continue
          if isinstance(beymeta, str):
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
    return ffmeta_kw
