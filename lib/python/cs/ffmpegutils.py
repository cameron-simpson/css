#!/usr/bin/python
#
# Convenience facilities for using FFmpeg (ffmpeg.org).
#   - Cameron Simpson <cs@cskk.id.au> 30oct2016
#

r'''
Wrapper for the ffmpeg command for video conversion.
'''

from collections import namedtuple
import os.path
from subprocess import Popen, PIPE
import sys
from cs.pfx import Pfx
from cs.tagset import TagSet

DISTINFO = {
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': ['cs.pfx'],
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

  def __init__(self, format, **kw):
    super().__init__()
    try:
      allowed_fields = MetaData.FIELDNAMES[format]
    except KeyError:
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
  source: Any
  timespans: List[Tuple]

  def __init__(self, source, timespans=()):
    self.source = source
    self.timespans = list(timespans)

  def add_as_input(self, ff):
    ''' Add as an input to `ff`.
        Return `None` if `self.source` is a pathname,
        otherwise return the file descriptor of the data sourc.

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
    if not isinstance(source, cls):
      source = cls(source)
    return source

        else:
