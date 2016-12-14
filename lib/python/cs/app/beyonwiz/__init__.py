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
from cs.app.ffmpeg import convert as ffconvert
from cs.logutils import info

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
    return json.JSONEncoder.default(self, o)

class RecordingMeta(NS):
  ''' Base class for recording metadata.
  '''

  def as_dict(self):
    return dict(self.__dict__)

  def as_json(self):
    return MetaJSONEncoder().encode(self.as_dict())

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

  def __init__(self):
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

  def convertpath(self, format='mp4'):
    left, middle, right = self.path_parts()
    # fixed length of the path
    fixed_len = len(self.metadata.start_dt_iso) \
              + len(left) \
              + len(right) \
              + len(format) \
              + 7
    middle = middle[:255-fixed_len]
    return '--'.join( (self.start_dt_iso, left, middle, right ) ) \
               .replace('/', '|') \
               .replace(' ', '-') \
               .replace('----', '--') \
           + '.' + format

  def convert(self, outpath, format=None):
    ''' Transcode video to `outpath` in FFMPEG `format`.
    '''
    if os.path.exists(outpath):
      raise ValueError("outpath exists")
    if format is None:
      _, ext = os.path.splitext(outpath)
      if not ext:
        raise ValueError("can't infer format from outpath, no extension")
      format = ext[1:]
    # prevent output path looking like option or URL
    if not os.path.isabs(outpath):
      outpath = os.path.join('.', outpath)
    ffmeta = self.ffmpeg_metadata(format)
    P, ffargv = ffconvert(None, 'mpegts', outpath, format, ffmeta)
    info("running %r", ffargv)
    self.copyto(P.stdin)
    P.stdin.close()
    xit = P.wait()
    if xit != 0:
      warning("ffmpeg failed, exit status %d", xit)
    return xit
