#!/usr/bin/python
#
# Assorted facilities for media information.
# - Cameron Simpson <cs@cskk.id.au> 09may2018
#

'''
Simple facilities for media information.
'''

import re
from types import SimpleNamespace as NS
from cs.pfx import Pfx
from cs.py.func import prop

DISTINFO = {
    'description': "Simple facilities for media information",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.pfx',
        'cs.py.func',
    ],
}

class EpisodeInfo(NS):
  ''' Trite class for episodic information, used to store, match
      or transcribe series/season, episode, etc values.
  '''

  RE_SERIES = re.compile(r's(\d+)', re.I)
  RE_EPISODE = re.compile(r'e(\d+)', re.I)
  RE_PART = re.compile(r'pt(\d+)', re.I)
  RE_SCENE = re.compile(r'sc(\d+)', re.I)

  def __init__(self, series=None, episode=None, part=None, scene=None):
    self.series = series
    self.episode = episode
    self.part = part
    self.scene = scene

  def __str__(self):
    return ''.join( (
        '' if self.series is None else 's%02d' % self.series,
        '' if self.episode is None else 'e%02d' % self.episode,
        '' if self.part is None else 'pt%02d' % self.part,
        '' if self.scene is None else 'sc%02d' % self.scene,
    ) )

  @classmethod
  def from_filename_part(cls, s, offset=0):
    ''' Factory to return an EpisodeInfo from a filename episode field.
        `s`: the string containing the episode information
        `offset`: the start of the episode information, default 0
        The episode information must extend to the end of the string
        because the factory returns just the information. See the
        `parse_filename_part` class method for the core parse.
    '''
    fields, offset = cls.parse_filename_part(s, offset=offset)
    if offset < len(s):
      raise ValueError("unparsed filename part: %r" % (s[offset:],))
    return cls(**fields)

  @classmethod
  def parse_filename_part(cls, s, offset=0):
    ''' Parse episode information from a string, returning the matched fields and the new offset.
        `s`: the string containing the episode information.
        `offset`: the starting offset of the information, default 0.
    '''
    with Pfx("parse_filename_part: %r", s):
      fields = {}
      for attr, r in (
          ('series', cls.RE_SERIES),
          ('episdoe', cls.RE_EPISODE),
          ('part', cls.RE_PART),
          ('scene', cls.RE_SCENE),
      ):
        m = r.match(s[offset:])
        if m:
          fields[attr] = int(m.group(1))
          offset += m.end()
      return fields, offset

  @prop
  def season(self):
    ''' .season property, synonym for .series
    '''
    return self.series

  @season.setter
  def season(self, n):
    ''' .season property, synonym for .series
    '''
    self.series = n
