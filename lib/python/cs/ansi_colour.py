#!/usr/bin/python
#
# ANSI terminal sequences.
#       - Cameron Simpson <cs@cskk.id.au> 16nov2010
#

'''
Convenience functions for ANSI terminal colour sequences

Mapping and function for adding ANSI terminal colour escape sequences
to strings for colour highlighting of output.
'''

import re

DISTINFO = {
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Development Status :: 6 - Mature",
        "Environment :: Console",
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
        "Topic :: Terminals",
    ],
    'install_requires': [],
}

COLOURS = {
    'normal': '\033[0m',
    'reverse': '\033[7m',
    'underline': '\033[4m',
    'bold': '\033[1m',
    'black': '\033[30m',
    'red': '\033[31m',
    'green': '\033[32m',
    'yellow': '\033[33m',
    'blue': '\033[34m',
    'magenta': '\033[35m',
    'cyan': '\033[36m',
    'white': '\033[37m',
}

NORMAL_COLOUR = 'normal'
assert NORMAL_COLOUR in COLOURS

DEFAULT_HIGHLIGHT = 'cyan'
assert DEFAULT_HIGHLIGHT in COLOURS

def colourise(s, colour=None, uncolour='normal'):
  ''' Return a string enclosed in colour-on and colour-off ANSI sequences.
      `colour` names the desired ANSI colour.
      `uncolour` may be used to specify the colour-off colour;
      the default is 'normal'.
  '''
  if colour is None:
    colour = DEFAULT_HIGHLIGHT
  return COLOURS[colour] + s + COLOURS[uncolour]

def make_pattern(pattern, default_colour=None):
  ''' Convert a pattern specification into a (colour, regexp) tuple.
      Each pattern may be:
        a string of the form "[colour]:regexp"
        a string containing no colon, taken to be a regexp
        a tuple of the form (colour, regexp)
        a regexp object
  '''
  pattern0 = pattern
  if default_colour is None:
    default_colour = DEFAULT_HIGHLIGHT
  if isinstance(pattern, str):
    try:
      colour, regexp = pattern.split(':', 1)
    except ValueError:
      colour = default_colour
      regexp = pattern
  else:
    try:
      colour, regexp = pattern
    except TypeError:
      colour = None
      regexp = pattern
  if isinstance(regexp, str):
    regexp = re.compile(regexp, re.I)
  if not colour:
    colour = default_colour
  return colour, regexp

def make_patterns(patterns, default_colour=None):
  ''' Convert an iterable of pattern specifications into a list if (colour, regexp) tuples.
      Each pattern may be:
        a string of the form "[colour]:regexp"
        a string containing no colon, taken to be a regexp
        a tuple of the form (colour, regexp)
        a regexp object
  '''
  return [
      make_pattern(pattern, default_colour=default_colour)
      for pattern in patterns
  ]

def colourise(s, patterns, default_colour=None):
  ''' Colourise a string according to regular expressions.
      `s`: the string
      `patterns`: a sequence of patterns
      `default_colour`: if a string pattern has no colon, or starts
        with a colon, use this colour; default DEFAULT_HIGHLIGHT
      Each pattern may be:
        a string of the form "[colour]:regexp"
        a string containing no colon, taken to be a regexp
        a tuple of the form (colour, regexp)
        a regexp object
      Returns the string with ANSI colour escapes embedded.
  '''
  patterns = make_patterns(patterns, default_colour=default_colour)
  chars = [ [c, NORMAL_COLOUR] for c in s ]
  for colour, regexp in patterns:
    for m in regexp.finditer(s):
      for pos in range(m.start(), m.end()):
        chars[pos][1] = colour
  subs = []
  prev_colour = NORMAL_COLOUR
  for c, colour in chars:
    if colour != prev_colour:
      subs.append(COLOURS[colour])
      prev_colour = colour
    subs.append(c)
  if prev_colour != NORMAL_COLOUR:
    subs.append(COLOURS[NORMAL_COLOUR])
  return ''.join(subs)
