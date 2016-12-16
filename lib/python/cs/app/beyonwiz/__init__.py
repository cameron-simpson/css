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
from cs.logutils import info, warning, error

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

  def convertpath(self, outfmt='mp4', ):
    ''' Generate the output filename
    '''
    left, middle, right = self.path_parts()
    # fixed length of the path
    fixed_len = len(left) \
              + len(right) \
              + len(self.metadata.start_dt_iso) \
              + len(outfmt) \
              + 7
    middle = middle[:255-fixed_len]
    return '--'.join( (left, middle, right, self.start_dt_iso ) ) \
               .lower() \
               .replace('/', '|') \
               .replace(' ', '-') \
               .replace('----', '--') \
           + '.' + outfmt

  def convert(self, outpath, outfmt=None):
    ''' Transcode video to `outpath` in FFMPEG `outfmt`.
    '''
    if os.path.exists(outpath):
      raise ValueError("outpath exists")
    if outfmt is None:
      _, ext = os.path.splitext(outpath)
      if not ext:
        raise ValueError("can't infer output format from outpath, no extension")
      outfmt = ext[1:]
    # prevent output path looking like option or URL
    if not os.path.isabs(outpath):
      outpath = os.path.join('.', outpath)
    ffmeta = self.ffmpeg_metadata(outfmt)
    P, ffargv = ffconvert(None, 'mpegts', outpath, outfmt, ffmeta)
    info("running %r", ffargv)
    self.copyto(P.stdin)
    P.stdin.close()
    xit = P.wait()
    if xit != 0:
      warning("ffmpeg failed, exit status %d", xit)
    return xit
