#!/usr/bin/python
#
# Units for nonnegative integers. - Cameron Simpson <cs@cskk.id.au>
#

'''
Functions for decomposing nonnegative integers according to various unit scales
and also parsing support for values written in scales.

Presupplied scales:
* `BINARY_BYTES_SCALE`: Binary units of (B)ytes, KiB, MiB, GiB etc.
* `DECIMAL_BYTES_SCALE`: Decimal units of (B)ytes, KB, MB, GB etc.
* `DECIMAL_SCALE`: Unit suffixes K, M, G etc.
* `TIME_SCALE`: Units of (s)econds, (m)inutes, (h)ours, (d)ays and (w)eeks.
* `UNSCALED_SCALE`: no units
'''

from cs.lex import get_chars, get_decimal, skipwhite

__version__ = '20201025-post'

DISTINFO = {
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': ['cs.lex'],
}

UNSCALED_SCALE = ((0, ''),)

TIME_SCALE = (
    (60, 's'),
    (60, 'm'),
    (24, 'h'),
    (7, 'd'),
    (0, 'w'),
)

# BINARY BYTES CALE
BINARY_BYTES_SCALE = (
    (1024, 'B'),
    (1024, 'KiB'),
    (1024, 'MiB'),
    (1024, 'GiB'),
    (1024, 'TiB'),
    (0, 'PiB'),
)

DECIMAL_BYTES_SCALE = (
    (1000, 'B'),
    (1000, 'KB'),
    (1000, 'MB'),
    (1000, 'GB'),
    (1000, 'TB'),
    (0, 'PB'),
)

DECIMAL_SCALE = (
    (1000, ''),
    (1000, 'K'),
    (1000, 'M'),
    (1000, 'G'),
    (1000, 'T'),
    (0, 'P'),
)

def human(n, scale):
  ''' Decompose a nonnegative integer `n` into counts by unit from `scale`.

      Parameters:
      * `n`: a nonnegative integer.
      * `scale`: a sequence of `(factor,unit)` where factor is the
        size factor to the following scale item
        and `unit` is the designator of the unit.
  '''
  components = []
  for factor, unit in scale:
    if factor == 0:
      components.append((n, unit))
      n = 0
      break
    remainder = n % factor
    components.append((remainder, unit))
    n //= factor
    if n == 0:
      break
  if n > 0:
    raise ValueError("invalid scale, final factor must be 0: %r" % (scale,))
  return components

def geek_bytes(n):
  ''' Decompose a nonnegative integer `n` into counts by unit
      from `BINARY_BYTES_SCALE`.
  '''
  return human(n, BINARY_BYTES_SCALE)

def human_bytes(n):
  ''' Decompose a nonnegative integer `n` into counts by unit
      from `DECIMAL_BYTES_SCALE`.
  '''
  return human(n, DECIMAL_BYTES_SCALE)

def human_time(n, scale=None):
  ''' Decompose a nonnegative integer `n` into counts by unit
      from `TIME_SCALE`.
  '''
  return human(n, TIME_SCALE)

def combine(components, scale):
  ''' Combine a sequence of value components as from `human()` into an integer.
  '''
  factors = {}
  current_factor = 1
  for factor, unit in scale:
    factors[unit] = current_factor
    if factor == 0:
      break
    current_factor *= factor
  total = 0
  for count, unit in components:
    total += count * factors[unit]
  return total

def transcribe(n, scale, max_parts=None, skip_zero=False, sep=''):
  ''' Transcribe a nonnegative integer `n` against `scale`.

      Parameters:
      * `n`: a nonnegative integer.
      * `scale`: a sequence of (factor, unit) where factor is the
        size factor to the follow scale and `unit` is the designator
        of the unit.
      * `max_parts`: the maximum number of components to transcribe.
      * `skip_zero`: omit components of value 0.
      * `sep`: separator between words, default: `''`.
  '''
  components = human(n, scale)
  text = []
  for count, unit in reversed(components):
    if skip_zero and count == 0:
      continue
    count_i = int(count)
    text.append( (str(count_i) if count_i == count else "%.1f" % count) + unit )
    if max_parts is not None and len(text) == max_parts:
      break
  return sep.join(text)

def transcribe_bytes_geek(n, max_parts=1, **kw):
  ''' Transcribe a nonnegative integer `n` against `BINARY_BYTES_SCALE`.
  '''
  return transcribe(n, BINARY_BYTES_SCALE, max_parts=max_parts, **kw)

def transcribe_bytes_human(n, max_parts=1, **kw):
  ''' Transcribe a nonnegative integer `n` against `DECIMAL_BYTES_SCALE`.
  '''
  return transcribe(n, DECIMAL_BYTES_SCALE, max_parts=max_parts, **kw)

def transcribe_time(n, max_parts=3, **kw):
  ''' Transcribe a nonnegative integer `n` against `TIME_SCALE`.
  '''
  return transcribe(n, TIME_SCALE, max_parts=max_parts, **kw)

def parse(s, scale, offset=0):
  ''' Parse an integer followed by an optional scale and return computed value.
      Returns the parsed value and the new offset.

      Parameters:
      * `s`: the string to parse.
      * `scale`: a scale array of (factor, unit_name).
      * `offset`: starting position for parse.
  '''
  offset = skipwhite(s, offset)
  if not s:
    raise ValueError("missing count")
  value_s, offset2 = get_decimal(s, offset)
  if not value_s:
    raise ValueError("expected decimal value")
  value = int(value_s)
  offset = skipwhite(s, offset2)
  if offset < len(s):
    vunit, offset = get_chars(s, offset, str.isalpha)
    if vunit:
      vunit0 = vunit
      vunit = vunit.lower()
      for factor, unit in scale:
        if unit.lower() == vunit:
          break
        if not factor:
          raise ValueError("unrecognised unit: %r" % (vunit0,))
        value *= factor
  return value, offset

def multiparse(s, scales, offset=0):
  ''' Parse an integer followed by an optional scale and return computed value.
      Returns the parsed value and the new offset.

      Parameters:
      * `s`: the string to parse.
      * `scales`: an iterable of scale arrays of (factor, unit_name).
      * `offset`: starting position for parse.
  '''
  for scale in scales:
    try:
      return parse(s, scale, offset)
    except ValueError as e:
      exc = e
  raise exc

if __name__ == '__main__':
  print(transcribe(2050, BINARY_BYTES_SCALE))
  print(transcribe(2050, DECIMAL_BYTES_SCALE))
  print(transcribe(2050, TIME_SCALE))
  print(parse('1 KB', DECIMAL_BYTES_SCALE), 1000)
  print(parse('1 KiB', BINARY_BYTES_SCALE), 1024)
  print(parse('1 K', DECIMAL_SCALE), 1000)
  ##print(parse('1.1 K', DECIMAL_SCALE), 1000)
