#!/usr/bin/python
#
# Convenience facilities for using FFmpeg (ffmpeg.org).
#   - Cameron Simpson <cs@cskk.id.au> 30oct2016
#

'''
Convenience facilities for using FFmpeg (ffmpeg.org),
with invocation via `ffmpeg-python`.
'''

from collections import namedtuple
from dataclasses import dataclass
import json
from os.path import (
    isfile as isfilepath,
)
import shlex
import sys
from typing import Any, List, Optional, Tuple

import ffmpeg
from typeguard import typechecked

from cs.fstags import FSTags, uses_fstags
from cs.logutils import warning
from cs.mappings import attrable
from cs.pfx import Pfx, pfx_call
from cs.psutils import pipefrom, print_argv
from cs.tagset import TagSet

DISTINFO = {
    'keywords': ["python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.pfx',
        'cs.tagset',
        'ffmpeg-python',
        ##'git+https://github.com/kkroening/ffmpeg-python.git@master#egg=ffmpeg-python',
    ],
}

class MetaData(TagSet):
  ''' Object containing fields which may be supplied to ffmpeg's -metadata option.
  '''

  FIELDNAMES = {
      'mp4': [
          'album',
          'album_artist',
          'author',
          'comment',
          'composer',
          'copyright',
          'description',
          'episode_id',
          'genre',
          'grouping',
          'lyrics',
          'network',
          'show',
          'synopsis',
          'title',
          'track',
          'year',
      ],
  }

  # pylint: disable=redefined-builtin
  def __init__(self, format, **kw):
    super().__init__()
    try:
      allowed_fields = MetaData.FIELDNAMES[format]
    except KeyError:
      # pylint: disable=raise-missing-from
      raise ValueError("unsupported target format %r" % (format,))
    self.__dict__.update(format=format, allow_fields=allowed_fields)
    for k, v in kw.items():
      if k not in allowed_fields:
        raise ValueError("format %r does not support field %r" % (format, k))
      self[k] = v

  def options(self):
    ''' Compute the FFmpeg -metadata option strings and return as a list.
    '''
    opts = []
    for field_name, value in self.items():
      if value is not None:
        opts.extend(('-metadata', '='.join((field_name, value))))
    return opts

# source specification
#
# src: input source.
#   If `src` is None, pass '-' to ffmpeg(1) as the input path and
#     attach a pipe to its standard input.
#   If `src` is a string it is considered to be a filename and
#     passed to ffmpeg's -i option.
#     Otherwise `src` is considered to be an open file and is attached
#     to ffmpeg's standard input.
#   `srcfmt`: FFmpeg format string. It is required if `src` is None or
#     `src` is an open file.
#
ConversionSource = namedtuple('ConversionSource', 'src srcfmt start_s end_s')

@dataclass(init=False)
class FFmpegSource:
  ''' A representation of an `ffmpeg` input source. '''

  source: Any
  timespans: List[Tuple]

  def __init__(self, source, timespans=()):
    self.source = source
    self.timespans = list(timespans)

  def add_as_input(self, ff):
    ''' Add as an input to `ff`.
        Return `None` if `self.source` is a pathname,
        otherwise return the file descriptor of the data source.

        Note: because we rely on `ff.input('pipe:')` for nonpathnames,
        you can only use a nonpathname `FFmpegSource` for one of the inputs.
        This is not checked.
    '''
    src = self.source
    if isinstance(src, int):
      fd = src
      ff.input('pipe:')
    elif isinstance(src, str):
      try:
        host, hostpath = src.split(':', 1)
      except ValueError:
        if not isfilepath(src):
          # pylint: disable=raise-missing-from
          raise ValueError("not a file: %r" % (src,))
        ff.input(src)
        fd = None
      else:
        if '/' in host:
          if not isfilepath(src):
            raise ValueError("not a file: %r" % (src,))
          ff.input(src)
          fd = None
        else:
          # get input from an ssh pipeline
          P = pipefrom(
              ['ssh', '-b', '--', host, f'exec cat {shlex.quote(hostpath)}'],
              text=False,
          )
          fd = P.stdout.fileno()
          ff = ff.input('pipe:')
          timespans = src.timespans
          if timespans:
            ffin = ff
            ff = ffmpeg.concat(
                *map(
                    lambda timespan: ffin.
                    trim(start=timespan[0], end=timespan[1]), timespans
                )
            )
    return fd

  @classmethod
  def promote(cls, source):
    ''' Promote `source` to an `FFmpegSource`. '''
    if not isinstance(source, cls):
      source = cls(source)
    return source

# A mapping of ffmpeg codec_name values to default converted names.
# If there's no entry here, use copy mode.
DEFAULT_CONVERSIONS = {
    'mp2': 'aac',
    'mpeg2video': 'h264',
}
DEFAULT_MEDIAFILE_FORMAT = 'mp4'

# pylint: disable=too-many-branches,too-many-locals,too-many-statements
@uses_fstags
@typechecked
def convert(
    *srcs,
    dstpath: str,
    doit=True,
    dstfmt=None,
    fstags: FSTags,
    conversions=None,
    metadata: Optional[dict] = None,
    timespans=(),
    overwrite=False,
    acodec=None,
    vcodec=None,
    extra_opts=None,
):
  ''' Transcode video to `dstpath` in FFMPEG compatible `dstfmt`.
  '''
  if conversions is None:
    conversions = DEFAULT_CONVERSIONS
  if metadata is None:
    metadata = {}
  srcs = [FFmpegSource.promote(src) for src in srcs]
  if not srcs:
    raise ValueError("no srcs")
  srcpath = srcs[0].source
  if dstfmt is None:
    dstfmt = DEFAULT_MEDIAFILE_FORMAT
  # set up the initial source path, options and metadata
  ffinopts = {
      'loglevel': 'repeat+error',
      ##'strict': None,
      ##'2': None,
  }
  # choose output formats
  probed = ffprobe(srcpath)
  for i, stream in enumerate(probed.streams):
    codec_type = stream.get('codec_type', 'unknown')
    codec_key = stream.get('codec_name', stream.codec_tag)
    with Pfx("stream[%d]: %s/%s", i, codec_type, codec_key, print=True):
      if codec_type not in ('audio', 'video'):
        warning("not audio or video, skipping")
        continue
      try:
        new_codec = conversions[codec_key]
      except KeyError:
        warning("no conversion, skipping")
      else:
        warning("convert to %r", new_codec)
        if codec_type == 'audio' and acodec is None:
          acodec = new_codec
        elif codec_type == 'video' and vcodec is None:
          vcodec = new_codec
        else:
          warning(
              "no option to convert streams of type %r, ignoring new_codec=%r",
              codec_type, new_codec
          )
  ffmeta_kw = dict(probed.format.get('tags', {}))
  ffmeta_kw.update(metadata)
  # construct ffmpeg command
  ff = ffmpeg.input(srcpath, **ffinopts)
  if timespans:
    ffin = ff
    ff = ffmpeg.concat(
        *map(
            lambda timespan: ffin.trim(start=timespan[0], end=timespan[1]),
            timespans
        )
    )
  output_opts = {
      'nostdin': None,
      'c:a': acodec or 'copy',
      'c:v': vcodec or 'copy',
  }
  if sys.stdout.isatty():
    output_opts.update(stats=None)
  if extra_opts:
    output_opts.update(extra_opts)
  ff = ff.output(
      dstpath,
      format=dstfmt,
      metadata=list(map('='.join, ffmeta_kw.items())),
      **output_opts,
  )
  if overwrite:
    ff = ff.overwrite_output()
  ff_args = ff.get_args()
  if doit:
    print_argv('ffmpeg', *ff_args)
    fstags[dstpath]['ffmpeg.argv'] = ['ffmpeg', *ff_args]
    fstags.sync()
    ff.run()
  else:
    print_argv('ffmpeg', *ff_args, fold=True)

def ffprobe(input_file, *, doit=True, ffprobe_exe='ffprobe', quiet=False):
  ''' Run `ffprobe -print_format json` on `input_file`,
      return format, stream, program and chapter information
      as an `AttrableMapping` (a `dict` subclass).
  '''
  argv = [
      ffprobe_exe, '-v', '0', '-print_format', 'json', '-show_format',
      '-show_streams', '-show_programs', '-show_chapters', '-i', input_file
  ]
  if not doit:
    print_argv(*argv, indent="+ ", end=" |\n")
    return {}
  P = pipefrom(argv, quiet=quiet, text=True)
  probed = pfx_call(json.loads, pfx_call(P.stdout.read))
  return attrable(probed)
