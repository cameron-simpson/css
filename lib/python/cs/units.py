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

from collections import namedtuple
from cs.deco import OBSOLETE
from cs.lex import get_chars, get_decimal, skipwhite

__version__ = '20220311-post'

DISTINFO = {
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': ['cs.deco', 'cs.lex'],
}

class UnitStep(namedtuple('UnitStep', 'factor unit max_width')):
  ''' A `namedtuple` expressing a scale step for a unit scale.
      The last tuple in a scale should have `factor=0`.

      Attributes:
      * `factor`: the scale factor for the unit in terms of the following unit.
        For example, `60` for seconds because there are `60` in the
        next unit, minutes.
      * `unit`: the textual abbreviation for the unit.
        For example, `'s'` for seconds.
      * `max_width`: the maximum width for a modulus of this unit.
        For example, `2` for seconds because the maximum value is less than `100`.
  '''

  def __new__(cls, factor, unit, max_width=0):
    return super().__new__(cls, factor, unit, max_width)

UNSCALED_SCALE = (UnitStep(0, ''),)

TIME_SCALE = (
    UnitStep(60, 's', 2),
    UnitStep(60, 'm', 2),
    UnitStep(24, 'h', 2),
    UnitStep(7, 'd', 1),
    UnitStep(0, 'w', 1),
)

# BINARY BYTES CALE
BINARY_BYTES_SCALE = (
    UnitStep(1024, 'B', 4),
    UnitStep(1024, 'KiB', 4),
    UnitStep(1024, 'MiB', 4),
    UnitStep(1024, 'GiB', 4),
    UnitStep(1024, 'TiB', 4),
    UnitStep(0, 'PiB', 4),
)

DECIMAL_BYTES_SCALE = (
    UnitStep(1000, 'B', 3),
    UnitStep(1000, 'KB', 3),
    UnitStep(1000, 'MB', 3),
    UnitStep(1000, 'GB', 3),
    UnitStep(1000, 'TB', 3),
    UnitStep(0, 'PB', 3),
)

DECIMAL_SCALE = (
    UnitStep(1000, '', 3),
    UnitStep(1000, 'K', 3),
    UnitStep(1000, 'M', 3),
    UnitStep(1000, 'G', 3),
    UnitStep(1000, 'T', 3),
    UnitStep(0, 'P', 3),
)

def decompose(n, scale):
  ''' Decompose a nonnegative integer `n` into counts by unit from `scale`.
      Returns a list of `(modulus,UnitStep)` in order from smallest unit upward.

      Parameters:
      * `n`: a nonnegative integer.
      * `scale`: a sequence of `UnitStep` or `(factor,unit[,max_width])`
        where factor is the size factor to the following scale item
        and `unit` is the designator of the unit.
  '''
  components = []
  for scale_step in scale:
    step = UnitStep(*scale_step)
    if step.factor == 0:
      components.append((n, step))
      n = 0
      break
    modulus = n % step.factor
    components.append((modulus, step))
    n //= step.factor
    if n == 0:
      break
  if n > 0:
    raise ValueError("invalid scale, final factor must be 0: %r" % (scale,))
  return components

@OBSOLETE(suggestion="decompose")  # pylint: disable=no-value-for-parameter
def human(n, scale):
  ''' Obsolete shim for `decompose()`.
  '''
  return decompose(n, scale)

def geek_bytes(n):
  ''' Decompose a nonnegative integer `n` into counts by unit
      from `BINARY_BYTES_SCALE`.
  '''
  return decompose(n, BINARY_BYTES_SCALE)

def decompose_bytes(n):
  ''' Decompose a nonnegative integer `n` into counts by unit
      from `DECIMAL_BYTES_SCALE`.
  '''
  return decompose(n, DECIMAL_BYTES_SCALE)

@OBSOLETE(suggestion="decompose_bytes")  # pylint: disable=no-value-for-parameter
def human_bytes(n):
  ''' Obsolete shim for `decompose_bytes()`.
  '''
  return decompose_bytes(n)

def decompose_time(n, scale=None):
  ''' Decompose a nonnegative integer `n` into counts by unit
      from `TIME_SCALE`.
  '''
  if scale is None:
    scale = TIME_SCALE
  return decompose(n, scale)

@OBSOLETE(suggestion="decompose_time")  # pylint: disable=no-value-for-parameter
def human_time(n, scale=None):
  ''' Obsolete shim for `decompose_time()`.
  '''
  return decompose_time(n, scale=scale)

def combine(components, scale):
  ''' Combine a sequence of value components as from `decompose()` into an integer.
  '''
  factors = {}
  current_factor = 1
  for scale_step in scale:
    step = UnitStep(*scale_step)
    factors[step.unit] = current_factor
    if step.factor == 0:
      break
    current_factor *= step.factor
  total = 0
  for count, step in components:
    total += count * factors[step.unit]
  return total

# pylint: disable=too-many-arguments
def transcribe(
    n, scale, max_parts=None, skip_zero=False, sep='', no_pad=False
):
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
  components = decompose(n, scale)
  text = []
  for count, step in reversed(components):
    if skip_zero and count == 0:
      continue
    count_i = int(count)
    count_s = str(count_i) if count_i == count else "%.1f" % count
    if not no_pad and step.max_width > len(count_s):
      count_s = " " * (step.max_width - len(count_s)) + count_s
    count_s += step.unit
    text.append(count_s)
    if max_parts is not None and len(text) == max_parts:
      break
  return sep.join(text)

def transcribe_bytes_geek(n, max_parts=1, **kw):
  ''' Transcribe a nonnegative integer `n` against `BINARY_BYTES_SCALE`.
  '''
  return transcribe(n, BINARY_BYTES_SCALE, max_parts=max_parts, **kw)

def transcribe_bytes_decompose(n, max_parts=1, **kw):
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
      for unit in scale:
        if unit.unit.lower() == vunit:
          break
        if not unit.factor:
          raise ValueError("unrecognised unit: %r" % (vunit0,))
        value *= unit.factor
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
