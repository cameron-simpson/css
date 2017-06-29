#!/usr/bin/python
#
# Convenience facilities for using FFmpeg (ffmpeg.org).
#   - Cameron Simpson <cs@zip.com.au> 30oct2016
#

from collections import namedtuple
import os.path
from subprocess import Popen, PIPE
from cs.logutils import Pfx
from cs.obj import O

class MetaData(O):
  ''' Object containing fields which may be supplied to ffmpeg's -metadata option.
  '''

  FIELDNAMES = { 'mp4':
                  [ 'album',
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

  def __init__(self,
                format,
                **kw):
    ''' Initialise .format, .fields and .values.
    '''
    try:
      fields = MetaData.FIELDNAMES[format]
    except KeyError:
      raise ValueError("unsupported target format %r" % (format,))
    self.format = format
    self.fields = fields
    self.values = {}
    for k, v in kw.items():
      if k == 'format':
        raise ValueError("forbidden keyword name %r" % (k,))
      if k not in fields:
        raise ValueError("format %r does not support field %r" % (format, field))
      self.values[k] = v

  def options(self):
    ''' Compute the FFmpeg -metadata option strings and return as a list.
    '''
    opts = []
    for field in self.fields:
      value = self.values.get(field)
      if value is not None:
        opts.extend( ('-metadata', '='.join( (field, value) ) ) )
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

def convert(src, srcfmt, dst, dstfmt, meta=None, overwrite=False,
            start_s=None, end_s=None):
    ''' Convert video `src` to `dst`, return a subprocess.Popen object and the ffmpeg argv.
        `src`: input source.
          If `src` is None, pass '-' to ffmpeg(1) as the input path and
          attach a pipe to its standard input.
          If `src` is a string it is considered to be a filename and
            passed to ffmpeg's -i option.
          Otherwise `src` is considered to be an open file and is attached
            to ffmpeg's standard input.
        `srcfmt`: FFmpeg format string. It is required if `src` is None or
          `src` is an open file.
        `dst`: output destination.
          If `dst` is None, pass '-' as the output path and attach a
            pipe to its standard output.
          If `dst` is a string it is considered to be a filename and
            passed as ffmpeg's output argument.
          Otherwise `dst` is considered to be an open file and is
            attached to ffmpeg's standard output.
        `dstfmt`: FFmpeg output format string. It is required if `dst` is
          None or `dst` is an open file.
        `meta`: a MetaData object used to populate ffmpeg's -metadata
          options. If meta is not None, meta.format must match
          `dstfmt` if that not None. If `dstfmt` is None, it is set
          from `meta.format`.
        `start_s`: start offset in seconds. Used for cropping.
        `end_s`: end offset in seconds. Used for cropping.
    '''
    return multiconvert([ConversionSource(src, srcfmt, start_s, end_s)],
                        dst, dstfmt, meta=meta, overwrite=overwrite)

def multiconvert(sources, dst, dstfmt, meta=None, overwrite=False):
    ''' Convert video `src` to `dst`, return a subprocess.Popen object and the ffmpeg argv.
        `src`: input source.
          If `src` is None, pass '-' to ffmpeg(1) as the input path and
          attach a pipe to its standard input.
          If `src` is a string it is considered to be a filename and
            passed to ffmpeg's -i option.
          Otherwise `src` is considered to be an open file and is attached
            to ffmpeg's standard input.
        `srcfmt`: FFmpeg format string. It is required if `src` is None or
          `src` is an open file.
        `dst`: output destination.
          If `dst` is None, pass '-' as the output path and attach a
            pipe to its standard output.
          If `dst` is a string it is considered to be a filename and
            passed as ffmpeg's output argument.
          Otherwise `dst` is considered to be an open file and is
            attached to ffmpeg's standard output.
        `dstfmt`: FFmpeg output format string. It is required if `dst` is
          None or `dst` is an open file.
        `meta`: a MetaData object used to populate ffmpeg's -metadata
          options. If meta is not None, meta.format must match
          `dstfmt` if that not None. If `dstfmt` is None, it is set
          from `meta.format`.
        `start_s`: start offset in seconds. Used for cropping.
        `end_s`: end offset in seconds. Used for cropping.
    '''
    with Pfx("multiconvert"):
      argv = [ 'ffmpeg',
               '-y' if overwrite else '-n',
               '-strict', '-2',     # enables experimental codes
               ]
      # assemble input arguments
      stdin = None
      stdout = None
      for src, srcfmt, start_s, end_s in sources:
        with Pfx(src):
          if src is None:
            # src from stdin
            if srcfmt is None:
              raise ValueError("srcfmt may not be None if src is None")
            if stdin is not None:
              raise ValueError("stdin already allocated")
            srcpath = '-'
            stdin = PIPE
          elif isinstance(src, str):
            # src from named file
            srcpath = src
            if srcpath.startswith('-'):
              srcpath = os.path.join(os.path.curdir, srcpath)
          else:
            # src should be a file
            if srcfmt is None:
              raise ValueError("srcfmt may not be None if src is a file")
            if stdin is not None:
              raise ValueError("stdin already allocated")
            srcpath = '-'
            stdin = src
          if meta is not None:
            if dstfmt is None:
              dstfmt = meta.format
            elif dstfmt != meta.format:
              raise ValueError("dstfmt %r does not match meta.format %r"
                               % (dstfmt, meta.format))
          if dst is None:
            # dst to stdout
            if dstfmt is None:
              raise ValueError("dstfmt may not be None if dst is None")
            if stdout is not None:
              raise ValueError("stdout already allocated")
            dstpath = '-'
            stdout = PIPE
          elif isinstance(dst, str):
            # dst from a named file
            dstpath = dst
            if dstpath.startswith('-'):
              dstpath = os.path.join(os.path.curdir, dstpath)
          else:
            # dst should be a file
            if dstfmt is None:
              raise ValueError("dstfmt may not be None if dst is a file")
            if stdout is not None:
              raise ValueError("stdout already allocated")
            dstpath = '-'
            stdout = dst
          if isinstance(dst, str):
            dstpath = dst
          if srcfmt is not None:
            argv.extend( ('-f', srcfmt) )
          duration = None
          if start_s is not None and end_s is not None:
            duration = end_s - start_s
          if start_s is not None:
            argv.extend( ('-ss', '%g' % (start_s,)) )
          if duration is not None:
            argv.extend( ('-t', '%g' % (duration,)) )
          argv.extend( ('-i', srcpath) )
      # assemble output arguments
      argv.extend( ('-f', dstfmt) )
      if meta is not None:
        argv.extend(meta.options())
      argv.append(dstpath)
      return Popen(argv, stdin=stdin, stdout=stdout), argv
