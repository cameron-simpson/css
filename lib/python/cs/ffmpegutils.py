#!/usr/bin/env python3
#
# Convenience facilities for using FFmpeg (ffmpeg.org).
# - Cameron Simpson <cs@cskk.id.au> 30oct2016
#

'''
Convenience facilities for using FFmpeg (ffmpeg.org),
with invocation via `ffmpeg-python`.
'''

from collections import namedtuple
from dataclasses import dataclass
import json
import os
from os.path import (
    isdir as isdirpath,
    isfile as isfilepath,
)
import shlex
from subprocess import CompletedProcess
import sys
from typing import Any, Iterable, List, Mapping, Optional, Tuple, Union

import ffmpeg
from typeguard import typechecked

from cs.dockerutils import DockerRun
from cs.fstags import FSTags, uses_fstags
from cs.lex import cutprefix
from cs.logutils import warning
from cs.mappings import attrable
from cs.pfx import Pfx, pfx, pfx_call
from cs.psutils import pipefrom, print_argv
from cs.tagset import TagSet

__version__ = '20240201'

DISTINFO = {
    'keywords': ["python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
    ],
    'entry_points': {
        'console_scripts':
        ['ffmpeg-docker = cs.ffmpegutils:main_ffmpeg_docker'],
    },
    'install_requires': [
        'cs.dockerutils',
        'cs.fstags',
        'cs.lex',
        'cs.logutils',
        'cs.mappings',
        'cs.pfx',
        'cs.psutils',
        'cs.tagset',
        'ffmpeg-python',
        ##'git+https://github.com/kkroening/ffmpeg-python.git@master#egg=ffmpeg-python',
        'typeguard',
    ],
}

FFMPEG_EXE_DEFAULT = 'ffmpeg'
FFMPEG_EXE_ENVVAR = 'FFMPEG_EXE'

# executable and image for use with docker
FFMPEG_DOCKER_EXE_DEFAULT = '/usr/local/bin/ffmpeg'
FFMPEG_DOCKER_IMAGE_DEFAULT = 'linuxserver/ffmpeg'

def main_ffmpeg_docker(argv=None):
  ''' The `ffm[peg-docker` command line implementation.
  '''
  if argv is None:
    argv = sys.argv
  sys.exit(ffmpeg_docker(*sys.argv[1:]).returncode)

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
          'disc',
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
    ##try:
    ##  allowed_fields = MetaData.FIELDNAMES[format]
    ##except KeyError:
    ##  # pylint: disable=raise-missing-from
    ##  ##raise ValueError("unsupported target format %r" % (format,))
    ##  allowed_fields = MetaData.FIELDNAMES['mp4']
    ##self.__dict__.update(format=format, allowed_fields=allowed_fields)
    for k, v in kw.items():
      ##if k not in allowed_fields:
      ##  raise ValueError("format %r does not support field %r" % (format, k))
      self[k] = v

  def options(self):
    ''' Compute the FFmpeg -metadata option strings and return as a list.
    '''
    opts = []
    for field_name, value in self.items():
      if value is not None:
        opts.extend(('-metadata', f'{field_name}={value}'))
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
    'audio/aac_latm': 'aac',
    'audio/mp2': 'aac',
    'audio/pcm_s16le': 'aac',
    'video/mpeg2video': 'h264',
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
    ffmpeg_exe=None,
    fstags: FSTags,
    conversions=None,
    metadata: Optional[dict] = None,
    timespans=(),
    overwrite=False,
    acodec=None,
    vcodec=None,
    extra_opts=None,
) -> List[str]:
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
  if ffmpeg_exe is None:
    ffmpeg_exe = os.environ.get(FFMPEG_EXE_ENVVAR, FFMPEG_EXE_DEFAULT)
  # set up the initial source path, options and metadata
  ffinopts = {
      'loglevel': 'repeat+error',
      ##'strict': None,
      ##'2': None,
  }
  # choose output formats
  if acodec is None or vcodec is None:
    probed = ffprobe(srcpath)
    for i, stream in enumerate(probed.streams if doit else ()):
      codec_type = stream.get('codec_type', 'unknown')
      codec_key = stream.get('codec_name', stream.codec_tag)
      conv_key = f'{codec_type}/{codec_key}'
      with Pfx("stream[%d]: %s", i, conv_key):
        if (codec_type == 'audio' and acodec is None
            or codec_type == 'video' and vcodec is None):
          try:
            new_codec = conversions[conv_key]
          except KeyError:
            ##warning("no conversion, skipping")
            pass
          else:
            warning("convert to %r", new_codec)
            if codec_type == 'audio':
              if acodec is None:
                acodec = new_codec
            elif codec_type == 'video':
              if vcodec is None:
                vcodec = new_codec
            else:
              warning(
                  "no option to convert streams of type %s/%s, ignoring new_codec=%r",
                  codec_type, codec_key, new_codec
              )

  ffmeta_kw = dict(probed.format.get('tags', {}) if doit else {})
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
      metadata=[f'{k}={v}' for k, v in ffmeta_kw.items()],
      **output_opts,
  )
  if overwrite:
    ff = ff.overwrite_output()
  ff_args = ff.get_args()
  ff_argv = [ffmpeg_exe, *ff_args]
  if doit:
    print_argv(*ff_argv)
    fstags[dstpath]['ffmpeg.argv'] = ff_argv
    fstags.sync()
    ff.run()
  else:
    print_argv(*ff_argv, fold=True)
  return ff_argv

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

@pfx
@typechecked
def ffmpeg_docker(
    *ffmpeg_args: Iterable[str],
    docker_run_opts: Optional[Union[List[str], Mapping]] = None,
    doit: Optional[bool] = None,
    quiet: Optional[bool] = None,
    ffmpeg_exe: Optional[str] = None,
    docker_exe: Optional[str] = None,
    image: Optional[str] = None,
    outputpath: str = '.',
) -> Optional[CompletedProcess]:
  ''' Invoke `ffmpeg` using docker.
  '''
  ffmpeg_args: List[str] = list(ffmpeg_args)
  if docker_run_opts is None:
    docker_run_opts = []
  if ffmpeg_exe is None:
    ffmpeg_exe = FFMPEG_DOCKER_EXE_DEFAULT
  if image is None:
    image = FFMPEG_DOCKER_IMAGE_DEFAULT
  if not isdirpath(outputpath):
    raise ValueError(f'outputpath:{outputpath!r}: not a directory')
  DR = DockerRun(image=image, outputpath=outputpath)
  DR.popopts(docker_run_opts)
  if docker_run_opts:
    raise ValueError(f'unparsed docker_run args: {docker_run_opts!r}')
  # parse ffmpeg options in order to extract the input and output files
  ffmpeg_argv = [ffmpeg_exe]
  while ffmpeg_args:
    arg = ffmpeg_args.pop(0)
    with Pfx(arg):
      if arg == '':
        raise ValueError("invalid empty outfile")
      if arg == '-':
        # output to stdout
        ffmpeg_argv.append(arg)
      elif not arg.startswith('-'):
        # output filename
        # TODO: URLs?
        outputpath = arg
        outputpath = cutprefix(outputpath, 'file:')
        ffmpeg_argv.append(DR.add_output(outputpath))
      elif arg == '-i':
        # an input filename
        # TODO: URLs?
        inputpath = ffmpeg_args.pop(0)
        inputpath = cutprefix(inputpath, 'file:')
        if inputpath == '-':
          # input from stdin
          ffmpeg_argv.extend([arg, inputpath])
        else:
          ffmpeg_argv.extend(['-i', DR.add_input(inputpath)])
      else:
        arg_ = arg[1:]
        # check for singular options
        if arg_ in (
            # information options
            'version',
            'buildconf',
            'formats',
            'muxers',
            'demuxers',
            'devices',
            'codecs',
            'decoders',
            'encoders',
            'bsfs',
            'protocols',
            'filters',
            'pix_fmts',
            'layouts',
            'sample_fmts',
            'dispositions',
            'colors',
            'hwaccels',  # global options
            'report',
            'y',
            'n',
            'ignore_unknown',
            'stats',  # Per-file main options
            'apad',
            'reinit_filter',
            'discard',
            'disposition',  # Video options
            'vn',
            'dn',  # Audio options
            'an',  # Subtitle options
            'sn',
        ):
          ffmpeg_argv.append(arg)
        else:
          ffmpeg_argv.extend([arg, ffmpeg_args.pop(0)])
  return DR.run(*ffmpeg_argv, docker_exe=docker_exe, doit=doit, quiet=quiet)
