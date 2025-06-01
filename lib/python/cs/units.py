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

from dataclasses import dataclass
from typing import Tuple

from cs.deco import OBSOLETE, Promotable, promote
from cs.lex import get_chars, get_decimal, r, skipwhite

__version__ = '20250601'

DISTINFO = {
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': ['cs.deco', 'cs.lex'],
}

@dataclass(slots=True)
class UnitStep:
  ''' A scale step for a unit scale.
      The last `UnitStep` in a scale should have `factor=0`.
  '''

  # the scale factor for the unit in terms of the following unit.
  # For example, `60` for seconds because there are `60` in the
  # next unit, minutes
  factor: int

  # the textual abbreviation for the unit
  unit: str

  # the maximum width for a modulus of this unit.
  # For example, `2` for seconds because the maximum value is less than `100`.
  max_width: int = 0

  # for unpacking assignment
  def __iter__(self):
    return iter((self.factor, self.unit, self.max_width))

class Decomposed(list):
  ''' A list of `(modulus,UnitStep)` 2-tuples
      representing a decomposed value.
  '''

  # pylint: disable=too-many-arguments
  def __str__(
      self, max_parts=None, *, skip_zero=False, sep='', no_pad=False
  ) -> str:
    ''' Transcribe a nonnegative integer `n` against `scale`.

        Parameters:
        * `max_parts`: the maximum number of components to transcribe.
        * `skip_zero`: omit components of value 0.
        * `sep`: separator between words, default: `''`.
    '''
    text = []
    for count, step in reversed(self):
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

  def sum(self):
    ''' Return the total value represented by this `Decomposed`.
    '''
    total = 0
    factor = 1
    for modulus, step in self:
      if factor == 0:
        raise ValueError(
            f'unexpected step with factor=0 before the end: {self=}'
        )
      total += modulus * factor
      factor *= step.factor
    return total

  def __float__(self) -> float:
    ''' The total value as a `float`.
    '''
    return float(self.sum())

  def __int__(self) -> int:
    ''' The total value as an `int`.
    '''
    return int(self.sum())

class UnitScale(Promotable):
  ''' A representation of a unit scale as a list of unit terms and scale factors.
  '''

  SCALES = {
      None: [
          UnitStep(0, ''),
      ],
      'decimal': [
          UnitStep(1000, '', 3),
          UnitStep(1000, 'K', 3),
          UnitStep(1000, 'M', 3),
          UnitStep(1000, 'G', 3),
          UnitStep(1000, 'T', 3),
          UnitStep(0, 'P', 3),
      ],
      'geek_bytes': [
          UnitStep(1024, 'B', 4),
          UnitStep(1024, 'KiB', 4),
          UnitStep(1024, 'MiB', 4),
          UnitStep(1024, 'GiB', 4),
          UnitStep(1024, 'TiB', 4),
          UnitStep(0, 'PiB', 4),
      ],
      'human_bytes': [
          UnitStep(1000, 'B', 3),
          UnitStep(1000, 'KB', 3),
          UnitStep(1000, 'MB', 3),
          UnitStep(1000, 'GB', 3),
          UnitStep(1000, 'TB', 3),
          UnitStep(0, 'PB', 3),
      ],
      'human_time': [
          UnitStep(60, 's', 2),
          UnitStep(60, 'm', 2),
          UnitStep(24, 'h', 2),
          UnitStep(7, 'd', 1),
          UnitStep(0, 'w', 1),
      ],
  }

  def __init__(self, scale):
    if isinstance(scale, str):
      try:
        scale = self.SCALES[scale]
      except KeyError:
        scale_names = ['None'] + sorted(
            repr(scale_name)
            for scale_name in self.SCALES
            if scale_name is not None
        )
        raise ValueError(
            f'unknown scale name {scale!r}, expected one of {",".join(scale_names)}'
        )
    elif not isinstance(scale, (list, tuple)):
      raise TypeError(f'unsupported scale type {r(scale)}')
    self.scale = scale

  @classmethod
  def from_str(cls, scale_name: str):
    ''' Return a `UnitScale` from its name.
    '''
    return cls(scale_name)

  def decompose(self, n) -> Decomposed:
    ''' Decompose a nonnegative integer `n` into counts by unit.
        Returns a `Decomposed` list of `(modulus,UnitStep)` in order from smallest unit upward.

        Parameters:
        * `n`: a nonnegative integer.
        * `scale`: a sequence of `UnitStep` or `(factor,unit[,max_width])`
          where factor is the size factor to the following scale item
          and `unit` is the designator of the unit.
    '''
    decomposed = Decomposed()
    decomposed.n = n
    decomposed.scale = self
    for step in self.scale:
      factor, *_ = step
      if factor == 0:
        decomposed.append((n, step))
        n = 0
        break
      n, modulus = divmod(n, factor)
      decomposed.append((modulus, step))
      if n == 0:
        break
    if n > 0:
      raise ValueError(
          f'invalid scale, final factor must be 0: {self.scale!r}'
      )
    return decomposed

  def transcribe(
      self, n, max_parts=None, *, skip_zero=False, sep='', no_pad=False
  ):
    ''' Transcribe `n` according to this scale.
        Parameters are passed to `Decomposed.__str__`.
    '''
    return self.decompose(n).__str__(
        max_parts, skip_zero=skip_zero, sep=sep, no_pad=no_pad
    )

  def get_term(self, s: str, offset=0) -> Tuple[int, int]:
    ''' Parse a decimal values possibly followed by a unit name from `s` at `offset`.
        Return a `(value,offset)` 2-tuple.

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
        for unit in self.scale:
          if unit.unit.lower() == vunit:
            break
          if not unit.factor:
            raise ValueError(f'unrecognised unit: {vunit0!r}')
          value *= unit.factor
    return value, offset

UNSCALED_SCALE = UnitScale.SCALES[None]
TIME_SCALE = UnitScale.SCALES['human_time']
BINARY_BYTES_SCALE = UnitScale.SCALES['geek_bytes']
DECIMAL_BYTES_SCALE = UnitScale.SCALES['human_bytes']
DECIMAL_SCALE = UnitScale.SCALES['decimal']

@promote
def decompose(n, scale: UnitScale) -> Decomposed:
  ''' Decompose a nonnegative integer `n` into counts by unit from `scale`.
      Returns a `Decomposed` list of `(modulus,UnitStep)` in order from smallest unit upward.

      Parameters:
      * `n`: a nonnegative integer.
      * `scale`: a `UnitScale` (or promotable)
  '''
  return scale.decompose(n)

def decimal(n):
  ''' Decompose a nonnegative integer `n` into human decimal counts (kilo etc).
  '''
  return UnitScale('decimal').decompose(n)

# AKA human(n)
human = decimal

def geek_bytes(n):
  ''' Decompose a nonnegative integer `n` into geek bytes sizes (kibibytes etc).
  '''
  return decompose(n, BINARY_BYTES_SCALE)

# AKA geek(n)
geek = geek_bytes

def human_bytes(n):
  ''' Decompose a nonnegative integer `n` into human bytes sizes (kilobytes etc).
  '''
  return UnitScale('human_bytes').decompose(n)

def human_time(n):
  ''' Decompose a nonnegative integer `n` into human time (hours etc).
  '''
  return UnitScale('human_time').decompose(n)

decompose_time = OBSOLETE('human_time(n)')(human_time)

# pylint: disable=too-many-arguments
@promote
def transcribe(
    n,
    scale: UnitScale,
    max_parts=None,
    *,
    skip_zero=False,
    sep='',
    no_pad=False
):
  ''' Transcribe a nonnegative integer `n` against `scale`.
      This is just a shim for `UnitScale.transcribe`,
      itself a shim for `Decomposed.__str__`.

      Parameters:
      * `n`: a nonnegative integer.
      * `scale`: a `UnitScale` (or promotable)
      * `max_parts`: the maximum number of components to transcribe.
      * `skip_zero`: omit components of value 0.
      * `sep`: separator between words, default: `''`.
  '''
  return scale.transcribe(
      n, max_parts, skip_zero=skip_zero, sep=sep, no_pad=no_pad
  )

@OBSOLETE("transcribe(n,'geek_bytes',1) or str(geek_bytes(n))")
def transcribe_bytes_geek(n, max_parts=1, **kw):
  ''' Transcribe a nonnegative integer `n` against `BINARY_BYTES_SCALE`.
  '''
  return transcribe(n, 'geek_bytes', max_parts=max_parts, **kw)

@OBSOLETE("transcribe(n,'decimal') or str(human(n))")
def transcribe_bytes_decompose(n, max_parts=1, **kw):
  ''' Transcribe a nonnegative integer `n` against `DECIMAL_BYTES_SCALE`.
  '''
  return transcribe(n, DECIMAL_BYTES_SCALE, max_parts=max_parts, **kw)

@OBSOLETE("transcribe(n,'human_time',3) or str(human_time(n))")
def transcribe_time(n, max_parts=3, **kw):
  ''' Transcribe a nonnegative integer `n` against `TIME_SCALE`.
  '''
  return transcribe(n, TIME_SCALE, max_parts=max_parts, **kw)

@promote
def parse(s: str, scale: UnitScale, offset=0):
  ''' Parse an integer followed by an optional scale and return computed value.
      Returns the parsed value and the new offset.
      This is a shim for `UnitScale.get_term(s,offset)`.

      Parameters:
      * `s`: the string to parse.
      * `scale`: a scale array of (factor, unit_name).
      * `offset`: starting position for parse.
  '''
  return scale.get_term(s, offset=offset)

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
  print(f'{transcribe(2050, BINARY_BYTES_SCALE)=}')
  print(f'{transcribe(2050, "geek_bytes")=}')
  print(f'{transcribe(2050, DECIMAL_BYTES_SCALE)=}')
  print(f'{transcribe(2050, "human_bytes")=}')
  print(f'{transcribe(2050, TIME_SCALE)=}')
  print(f'{transcribe(2050, "human_time")=}')
  print(f'{parse("1 KB", DECIMAL_BYTES_SCALE)=} vs 1000')
  print(f'{parse("1 KB", "human_bytes")=} vs 1000')
  print(f'{parse("1 KiB", BINARY_BYTES_SCALE)=} vs 1024')
  print(f'{parse("1 KiB", "geek_bytes")=} vs 1024')
  print(f'{parse("1 K", DECIMAL_SCALE)=} vs 1000')
  print(f'{parse("1 K", "decimal")=} vs 1000')
  ##print(f'{parse("1.1 K", DECIMAL_SCALE), 1000=}')
