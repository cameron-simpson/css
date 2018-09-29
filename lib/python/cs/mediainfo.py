#!/usr/bin/python
#
# Assorted facilities for media information.
# - Cameron Simpson <cs@cskk.id.au> 09may2018
#

'''
Simple facilities for media information.
'''

from collections import namedtuple
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

_EpisodeDatumDefn = namedtuple('EpisodeDatumDefn', 'name prefix re')
class EpisodeDatumDefn(_EpisodeDatumDefn):
  ''' An EpisodeInfo marker definition.
  '''

  def __new__(cls, name, prefix):
    r = re.compile(name + r'(\d+)', re.I)
    return super(EpisodeDatumDefn, cls).__new__(cls, name, prefix, r)

  def parse(self, s, offset=0):
    ''' Parse an episode datum from a string, return the value and new offset.
        `s`: the string
        `offset`: parse offset, default 0
        Raises ValueError if the string doesn't match this EpisodeDatumDefn.
    '''
    m = self.re.match(s[offset:])
    if m:
      return int(m.group(1)), offset + m.end()
    raise ValueError(
        '%s: unparsed episode datum: %r'
        % (type(self).__name__, s[offset:])
    )

class EpisodeInfo(NS):
  ''' Trite class for episodic information, used to store, match
      or transcribe series/season, episode, etc values.
  '''

  MARKERS = [
      EpisodeDatumDefn('series', 's'),
      EpisodeDatumDefn('episode', 'e'),
      EpisodeDatumDefn('part', 'pt'),
      EpisodeDatumDefn('scene', 'sc'),
  ]

  def __init__(self, series=None, episode=None, part=None, scene=None):
    self.series = series
    self.episode = episode
    self.part = part
    self.scene = scene

  def __str__(self):
    marks = []
    for marker in self.MARKERS:
      value = self.get(marker.name)
      if value is not None:
        marks.append(marker.prefix + '%02d' % value)
    return ''.join(marks)

  def __getitem__(self, name):
    ''' We can look up values by name.
    '''
    try:
      value = getattr(self, name)
    except AttributeError:
      raise KeyError(name)
    return value

  def get(self, name, default=None):
    ''' Look up value by name with default.
    '''
    try:
      value = self[name]
    except KeyError:
      return default
    return value

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
      for defn in cls.MARKERS:
        try:
          value, offset = defn.parse(s, offset)
        except ValueError:
          pass
        else:
          fields[defn.name] = value
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
