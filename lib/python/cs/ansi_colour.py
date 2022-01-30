#!/usr/bin/python
#
# ANSI terminal sequences.
#       - Cameron Simpson <cs@cskk.id.au> 16nov2010
#

'''
Convenience functions for ANSI terminal colour sequences [color].

Mapping and function for adding ANSI terminal colour escape sequences
to strings for colour highlighting of output.
'''

import os
from os.path import basename, exists as existspath, join as joinpath
import re
import sys

from cs.gimmicks import warning

__version__ = '20200729-post'

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
    'install_requires': ['cs.gimmicks'],
}

# the known colour names and their colour codes
COLOUR_CODES = {
    'default': 0,
    'normal': 0,
    'bold': 1,
    'bright': 1,
    'underline': 4,
    'blink': 5,
    'flash': 5,
    'reverse': 7,
    'black': 30,
    'red': 31,
    'green': 32,
    'yellow': 33,
    'blue': 34,
    'magenta': 35,
    'purple': 35,
    'cyan': 36,
    'white': 37,
    'blackbg': 40,
    'redbg': 41,
    'greenbg': 42,
    'yellowbg': 43,
    'bluebg': 44,
    'magentabg': 45,
    'purplebg': 45,
    'cyanbg': 46,
    'whitebg': 47,
}

def colour_escape(code):
  ''' Return the ANSI escape sequence to activate the colour `code`.
      `code` may be an `int` or a `str` which indexes `COLOUR_CODES`.
  '''
  if isinstance(code, str):
    try:
      code = int(code)
    except ValueError:
      code = COLOUR_CODES[code]
  return '\033[' + str(code) + 'm'

# the known colour names and their escape sequences
COLOURS = {name: colour_escape(name) for name in COLOUR_CODES}

# default uncoloured text mode
NORMAL_COLOUR = 'normal'
assert NORMAL_COLOUR in COLOURS

# default highlight colour
DEFAULT_HIGHLIGHT = 'cyan'
assert DEFAULT_HIGHLIGHT in COLOURS

def env_no_color(environ=None):
  ''' Test the `$NO_COLOR` environment variable per the specification at
      https://no-color.org/
  '''
  if environ is None:
    environ = os.environ
  return 'NO_COLOR' in environ

def colourise(s, colour=None, uncolour=None):
  ''' Return a string enclosed in colour-on and colour-off ANSI sequences.

      * `colour`: names the desired ANSI colour.
      * `uncolour`: may be used to specify the colour-off colour;
        the default is 'normal' (from `NORMAL_COLOUR`).
  '''
  if colour is None:
    colour = DEFAULT_HIGHLIGHT
  if uncolour is None:
    uncolour = NORMAL_COLOUR
  return COLOURS[colour] + s + COLOURS[uncolour]

def make_pattern(pattern, default_colour=None):
  ''' Convert a `pattern` specification into a `(colour,regexp)` tuple.

      Parameters:
      * `pattern`: the pattern to parse
      * `default_colour`: the highlight colour,
        default "cyan" (from `DEFAULT_HIGHLIGHT`).

      Each `pattern` may be:
      * a string of the form "[colour]:regexp"
      * a string containing no colon, taken to be a regexp
      * a tuple of the form `(colour,regexp)`
      * a regexp object
  '''
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
  ''' Convert an iterable of pattern specifications into a list of
      `(colour,regexp)` tuples.

      Parameters:
      * `patterns`: an iterable of patterns to parse
      * `default_colour`: the highlight colour,
        default "cyan" (from `DEFAULT_HIGHLIGHT`).

      Each pattern may be:
      * a string of the form "[colour]:regexp"
      * a string containing no colon, taken to be a regexp
      * a tuple of the form (colour, regexp)
      * a regexp object
  '''
  return [
      make_pattern(pattern, default_colour=default_colour)
      for pattern in patterns
  ]

def colourise_patterns(s, patterns, default_colour=None):
  ''' Colourise a string `s` according to `patterns`.

      Parameters:
      * `s`: the string.
      * `patterns`: a sequence of patterns.
      * `default_colour`: if a string pattern has no colon, or starts
        with a colon, use this colour;
        default "cyan" (from `DEFAULT_HIGHLIGHT`).

      Each pattern may be:
      * a string of the form "[colour]:regexp"
      * a string containing no colon, taken to be a regexp
      * a tuple of the form `(colour,regexp)`
      * a regexp object

      Returns the string with ANSI colour escapes embedded.
  '''
  patterns = make_patterns(patterns, default_colour=default_colour)
  chars = [[c, NORMAL_COLOUR] for c in s]
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

class TerminalColors:
  ''' A parser for `/etc/terminal-colors.d'` files.
  '''

  TERMINAL_COLORS_D = '/etc/terminal-colors.d'

  # pylint: disable=too-many-arguments
  def __init__(
      self,
      util_name=None,
      term_name=None,
      type_name=None,
      colors_dirpath=None,
      envvar=None,
  ):
    ''' Initialise the `TerminalColors` instance.

        Parameters:
        * `util_name`: optional utility name, default from `sys.argv[0]`
        * `term_name`: optional terminal name, default from the `$TERM` envvar
        * `type_name`: optional type name, default `'enable'`
        * `colors_dirpath`: optional specification files directory path,
          default from `TerminalColors.TERMINAL_COLORS_D`
        * `envvar`: environment variable to override matches;
          the default `util_name+'_COLORS'`,
          thus `$LS_COLORS` if `util_name=='ls'`.
          That may be the value `False` if no environment variable should be an override.
    '''
    if util_name is None:
      try:
        util_name = basename(sys.argv[0])
      except KeyError:
        util_name = ''
    if term_name is None:
      term_name = os.environ.get('TERM', 'dumb')
    if type_name is None:
      type_name = 'enable'
    if colors_dirpath is None:
      colors_dirpath = self.TERMINAL_COLORS_D
    if envvar is None:
      envvar = self.envvar = util_name.upper() + '_COLORS'
    self.util_name = util_name
    self.term_name = term_name
    self.type_name = type_name
    self.colors_dirpath = colors_dirpath
    self.envvar = envvar
    self._mapping = None
    # prefill the mapping if the environment variable is present
    if envvar is not False:
      envval = os.environ.get(envvar)
      if envval:
        m = {}
        for field in envval.strip().split(':'):
          if not field:
            continue
          try:
            name, sequence = field.strip().split('=', 1)
          except ValueError as e:
            warning("$%s: %r: %e", envvar, field, e)
          else:
            m[name] = self.convert_sequence(sequence)
        self._mapping = m

  @property
  def mapping(self):
    ''' The mapping of `name` to escape sequence.
    '''
    m = self._mapping
    if m is None:
      m = self._mapping = dict(self.scan())
    return m

  @staticmethod
  def convert_sequence(sequence):
    ''' Convert a colour specification to an escape sequence.
    '''
    escs = []
    for colour_code in sequence.split(';'):
      try:
        escs.append(colour_escape(colour_code))
      except KeyError as e:
        warning("%r: %r: %e", sequence, colour_code, e)
    return ''.join(escs)

  def find_specfile(self):
    ''' Locate the most specific specification file matching our criteria.
        Return `None` if no file matches.
    '''
    for utilpfx in self.util_name, '':
      for term in self.term_name, '':
        for type_ in self.type_name, :
          base = utilpfx
          if term:
            base += '@' + term
          if base or type_:
            base += '.'
          base += type_
          if not base:
            continue
          path = joinpath(self.colors_dirpath, base)
          if existspath(path):
            return path
    return None

  def scan(self, path=None):
    ''' Scan the colour specification in `path`
        and yield `(name,escape_sequence)` tuples.
    '''
    if path is None:
      path = self.find_specfile()
      if path is None:
        # no matching specfile
        return
    with open(path) as f:  # pylint: disable=unspecified-encoding
      for lineno, line in enumerate(f, 1):
        line = line.strip()
        if not line or line.startswith('#'):
          continue
        try:
          name, sequence = line.split()
        except ValueError as e:
          warning("%s, %d: %s", path, lineno, e)
          continue
        yield name, self.convert_sequence(sequence)
