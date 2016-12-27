#!/usr/bin/python

''' Classes to support access to Beyonwiz TVWiz and Enigma2 on disc data
    structures and to access Beyonwiz devices via the net. Also support for
    newer Beyonwiz devices running Enigma and their recording format.
'''

DISTINFO = {
    'description': "Beyonwiz PVR and TVWiz recording utilities",
    'keywords': ["python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
        ],
    'requires': ['cs.app.ffmpeg', 'cs.logutils', 'cs.obj', 'cs.threads', 'cs.urlutils'],
    'entry_points': {
      'console_scripts': [
          'beyonwiz = cs.app.beyonwiz:main',
          ],
    },
}

import datetime
import json
import os.path
from threading import Lock
from types import SimpleNamespace as NS
from cs.app.ffmpeg import convert as ffconvert, MetaData as FFmpegMetaData
from cs.logutils import info, warning, error, Pfx, X

# UNUSED
def trailing_nul(bs):
  # strip trailing NULs
  bs = bs.rstrip(b'\x00')
  # locate preceeding NUL padded area
  start = bs.rfind(b'\x00')
  if start < 0:
    start = 0
  else:
    start += 1
  return start, bs[start:]

class MetaJSONEncoder(json.JSONEncoder):
  def default(self, o):
    if isinstance(o, set):
      return sorted(o)
    if isinstance(o, datetime.datetime):
      return o.isoformat(' ')
    return json.JSONEncoder.default(self, o)

class RecordingMetaData(NS):
  ''' Base class for recording metadata.
  '''

  def _asdict(self):
    d = dict(self.__dict__)
    d["start_dt_iso"] = self.start_dt_iso
    return d

  def _asjson(self, indent=None):
    return MetaJSONEncoder(indent=indent).encode(self._asdict())

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

class _Recording(object):
  ''' Base class for video recordings.
  '''

  def __init__(self, path):
    self.path = path
    self._lock = Lock()

  def copyto(self, output):
    ''' Transcribe the uncropped content to a file named by output.
        Requires the .data() generator method to yield video data chunks.
    '''
    if type(output) is str:
      outpath = output
      with open(outpath, "wb") as output:
        self.copyto(output)
    else:
      for buf in self.data():
        output.write(buf)

  def path_parts(self):
    ''' The 3 components contributing to the .convertpath() method.
        The middle component may be trimmed to fit into a legal filename.
    '''
    M = self.metadata
    title = '--'.join([M.title, str(M.episode)]) if M.episode else M.title
    return ( title,
             '-'.join(sorted(M.tags)),
             M.channel
           )

  def convertpath(self, outext):
    ''' Generate the output filename
    '''
    left, middle, right = self.path_parts()
    # fixed length of the path
    fixed_len = len(left) \
              + len(right) \
              + len(self.metadata.start_dt_iso) \
              + len(outext) \
              + 7
    filename = '--'.join( (left,
                           middle,
                           right,
                           self.start_dt_iso,
                           self.metadata.description ) ) \
               .lower() \
               .replace('/', '|') \
               .replace(' ', '-') \
               .replace('----', '--')
    filename = filename[:255 - (len(outext) + 1)]
    return filename + '.' + outext

  # TODO: move into cs.fileutils?
  @staticmethod
  def choose_free_path(path, max_n=32):
    ''' Find an available unused pathname based on `path`.
        Raises ValueError in none is available.
    '''
    pfx, ext = os.path.splitext(path)
    for i in range(max_n):
      path2 = "%s--%d%s" % (pfx, i+1, ext)
      if not os.path.exists(path2):
        return path2
    raise ValueError("no available --0..--%d variations: %r", max_n-1, path)

  def convert(self, dstpath, outfmt='mp4', max_n=None):
    ''' Transcode video to `dstpath` in FFMPEG `outfmt`.
    '''
    ok = True
    srcpath = self.path
    if dstpath is None:
      dstpath = self.convertpath(outext=outfmt)
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
      if ok:
        if os.path.exists(dstpath):
          raise ValueError("dstpath exists")
        if outfmt is None:
          _, ext = os.path.splitext(dstpath)
          if not ext:
            raise ValueError("can't infer output format from dstpath, no extension")
          outfmt = ext[1:]
        # prevent output path looking like option or URL
        if not os.path.isabs(dstpath):
          dstpath = os.path.join('.', dstpath)
        ffmeta = self.ffmpeg_metadata(outfmt)
        P, ffargv = ffconvert(None, 'mpegts', dstpath, outfmt, ffmeta)
        info("running %r", ffargv)
        self.copyto(P.stdin)
        P.stdin.close()
        xit = P.wait()
        if xit == 0:
          ok = True
        else:
          warning("ffmpeg failed, exit status %d", xit)
          ok = False
        return ok

  def ffmpeg_metadata(self, outfmt='mp4'):
    M = self.metadata
    comment = 'Transcoded from %r using ffmpeg. Recording date %s.' \
              % (self.path, M.start_dt_iso)
    if M.tags:
      comment += ' tags={%s}' % (','.join(sorted(M.tags)),)
    return FFmpegMetaData(outfmt,
                          title=( '%s: %s' % (M.title, M.episode)
                                  if M.episode
                                  else M.title
                                ),
                          show=M.title,
                          episode_id=M.episode,
                          synopsis=M.description,
                          network=M.channel,
                          comment=comment,
                         )
