#!/usr/bin/python
#
# Assorted facilities for media information.
# - Cameron Simpson <cs@cskk.id.au> 09may2018
#

'''
Simple facilities for media information.

The default filename parsing rules are based on my personal convention,
which is to name media files as:

  series_name--episode_info--title--source--etc....ext

where the components are:
* `series_name`:
  the programme series name downcased and with whitespace replaced by dashes;
  in the case of standalone items like movies this is usually the studio.
* `episode_info`: a structures field with episode information:
  `s`_n_ is a series/season,
  `e`_n_` is an episode number within the season,
  `x`_n_` is a "extra" - addition material supplied with the season,
  etc.
* `title`: the episode title downcased and with whitespace replaced by dashes
* `source`: the source of the media
* `ext`: filename extension such as `mp4`.

As you may imagine,
as a rule I dislike mixed case filenames
and filenames with embedded whitespace.
I also like a media filename to contain enough infomation
to identify the file contents in a compact and human readable form.
'''

from __future__ import print_function
from collections import namedtuple, defaultdict
from os.path import basename, splitext
import re
import sys
from types import SimpleNamespace as NS
from cs.pfx import Pfx
from cs.py.func import prop
from cs.tagset import Tag

DISTINFO = {
    'description':
    "Simple facilities for media information",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.pfx',
        'cs.py.func',
        'cs.tagset',
    ],
}

USAGE = r'''Usage: %s media-filenames...'''

def main(argv=None):
  ''' Main command line running some test code.
  '''
  if argv is None:
    argv = sys.argv
  argv = list(argv)
  cmd = argv.pop(0)
  usage = USAGE % (cmd,)
  if not argv:
    print("%s: missing media-filenames" % (cmd,), file=sys.stderr)
    print(usage)
    return 2
  for pathname in argv:
    with Pfx("%r", pathname):
      name, _ = splitext(basename(pathname))
      for part, fields in parse_name(name):
        print(" ", part, repr(fields))
      print(" ", name, pathname_info(pathname))
  return 0

def title_to_part(title):
  ''' Convert a title string into a filename part.
      This is lossy; the `part_to_title` function cannot completely reverse this.

      Example:

          >>> title_to_part('Episode Name')
          'episode-name'
  '''
  return re.sub(r'[\s-]+', '-', title).strip().lower()

def part_to_title(part):
  ''' Convert a filename part into a title string.

      Example:

          >>> part_to_title('episode-name')
          'Episode Name'
  '''
  return part.strip('- \t\r\n').replace('-', ' ').title()

_EpisodeDatumDefn = namedtuple('EpisodeDatumDefn', 'name prefix re')

class EpisodeDatumDefn(_EpisodeDatumDefn):
  ''' An `EpisodeInfo` marker definition.
  '''

  def __new__(cls, name, prefix):
    r = re.compile(prefix + r'(\d+)', re.I)
    return super(EpisodeDatumDefn, cls).__new__(cls, name, prefix, r)

  def parse(self, s, offset=0):
    ''' Parse an episode datum from a string, return the value and new offset.
        Raise `ValueError` if the string doesn't match this definition.

        Parameters:
        * `s`: the string
        * `offset`: parse offset, default 0
    '''
    m = self.re.match(s[offset:])
    if m:
      return int(m.group(1)), offset + m.end()
    raise ValueError(
        '%s: unparsed episode datum: %r' % (type(self).__name__, s[offset:])
    )

class EpisodeInfo(NS):
  ''' Trite class for episodic information, used to store, match
      or transcribe series/season, episode, etc values.
  '''

  MARKERS = [
      EpisodeDatumDefn('series', 's'),
      EpisodeDatumDefn('episode', 'e'),
      EpisodeDatumDefn('part', 'pt'),
      EpisodeDatumDefn('part', 'p'),
      EpisodeDatumDefn('scene', 'sc'),
      EpisodeDatumDefn('extra', 'x'),
      EpisodeDatumDefn('extra', 'ex'),
  ]

  def __init__(self, series=None, episode=None, part=None, scene=None):
    self.series = series
    self.episode = episode
    self.part = part
    self.scene = scene

  def as_dict(self):
    ''' Return the episode info as a `dict`.
    '''
    d = {}
    for attr in 'series', 'episode', 'part', 'scene':
      value = getattr(self, attr)
      if value is not None:
        d[attr] = value
    return d

  def as_tags(self, prefix=None):
    ''' Generator yielding the episode info as `Tag`s.
    '''
    for field, value in self.as_dict().items():
      yield Tag(field, value, prefix=prefix)

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
    ''' Factory to return an `EpisodeInfo` from a filename episode field.

        Parameters:
        * `s`: the string containing the episode information
        * `offset`: the start of the episode information, default 0

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
    ''' Parse episode information from a string,
        returning the matched fields and the new offset.

        Parameters:
        `s`: the string containing the episode information.
        `offset`: the starting offset of the information, default 0.
    '''
    with Pfx("parse_filename_part: %r", s):
      start_offset = offset
      fields = {}
      while offset < len(s):
        offset0 = offset
        for defn in cls.MARKERS:
          if defn.name in fields:
            # support for different definitions with a common prefix
            # earlier definitions are matched first
            continue
          try:
            value, offset = defn.parse(s, offset)
          except ValueError:
            pass
          else:
            fields[defn.name] = value
            break
        # parse fails, stop trying for more information
        if offset == offset0:
          break
      if offset == start_offset:
        # no component info, try other things
        if len(s) == 4 and s.isdigit() and s.startswith(('19', '20')):
          fields['year'] = int(s)
        elif len(s) == 6 and s[1:-1].isdigit() and s.startswith(
            ('19', '20'), 1):
          fields['year'] = int(s[1:-1])
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

def parse_name(name, sep='--'):
  ''' Parse the descriptive part of a filename
      (the portion remaining after stripping the file extension)
      and yield `(part,fields)` for each part as delineated by `sep`.
  '''
  unstructured_sections = ('series_name', 'episode_name', 'source_name')
  unstructured_index = 0
  with Pfx("parse_name(%r)", name):
    for part0 in name.split(sep):
      with Pfx(part0):
        part = part0.strip('- \t\r\n')
        if not part:
          # ignore empty parts
          continue
        # look for series/episode/scene/part markers
        fields, offset = EpisodeInfo.parse_filename_part(part)
        if offset == 0:
          # nothing parsed, consider the unstructured sections
          if unstructured_index < len(unstructured_sections):
            fields = {unstructured_sections[unstructured_index]: part}
            unstructured_index += 1
          else:
            fields = None
        elif offset < len(part):
          print(
              "warning: parse_name %r: part %r: unparsed: %r" %
              (name, part0, part[offset:])
          )
        yield part0, fields

def pathname_info(pathname):
  ''' Parse information from the basename of a file pathname.
      Return a mapping of field => values in the order parsed.
  '''
  info = defaultdict(list)
  name, _ = splitext(basename(pathname))
  for _, fields in parse_name(name):
    if fields:
      for field, value in fields.items():
        if field in info:
          print(
              "discard %r=%r: already have %r=%r" %
              (field, value, field, info[field])
          )
        else:
          info[field].append(value)
  return info

if __name__ == '__main__':
  sys.exit(main(sys.argv))
