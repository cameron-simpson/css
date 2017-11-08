#!/usr/bin/python
#
# Units for nonnegative integers. - Cameron Simpson <cs@cskk.id.au>
#

'''
Functions for decomposing nonnegative integers according to various unit scales.

Presupplied scales:
  BINARY_BYTES_SCALE  Units of (B)ytes, KiB, MiB, GiB etc.
  DECIMAL_BYTES_SCALE Units of (B)ytes, KB, MB, GB etc.
  DECIMAL_SCALE       Unit prefixes K, M, G etc.
  TIME_SCALE          Units of (s)econds, (m)inutes, (h)ours, (d)ays and (w)eeks.
'''

TIME_SCALE = (
    ( 60, 's' ),
    ( 60, 'm' ),
    ( 24, 'h' ),
    ( 7, 'd' ),
    ( 0, 'w' ),
)

BINARY_BYTES_SCALE = (
    ( 1024, 'B' ),
    ( 1024, 'KiB' ),
    ( 1024, 'MiB' ),
    ( 1024, 'GiB' ),
    ( 1024, 'TiB' ),
    ( 0, 'PiB' ),
)

DECIMAL_BYTES_SCALE = (
    ( 1000, 'B' ),
    ( 1000, 'KB' ),
    ( 1000, 'MB' ),
    ( 1000, 'GB' ),
    ( 1000, 'TB' ),
    ( 0, 'PB' ),
)

DECIMAL_SCALE = (
    ( 1000, '' ),
    ( 1000, 'K' ),
    ( 1000, 'M' ),
    ( 1000, 'G' ),
    ( 1000, 'T' ),
    ( 0, 'P' ),
)

def human(n, scale):
  ''' Decompose a nonnegative integer `n` into counts by unit from `scale`.
      `n`: a nonnegative integer
      `scale`: a sequence of (factor, unit) where factor is the
        size factor to the follow scale and `unit` is the designator
        of the unit
  '''
  components = []
  for factor, unit in scale:
    if factor == 0:
      components.append( (n, unit) )
      n = 0
      break
    remainder = n % factor
    components.append( (remainder, unit) )
    n //= factor
    if n == 0:
      break
  if n > 0:
    raise ValueError("invalid scale, final factor must be 0: %r" % (scale,))
  return components

def geek_bytes(n):
  ''' Decompose a nonnegative integer `n` into counts by unit from BINARY_BYTES_SCALE.
  '''
  return human(n, BINARY_BYTES_SCALE)

def human_bytes(n):
  ''' Decompose a nonnegative integer `n` into counts by unit from DECIMAL_BYTES_SCALE.
  '''
  return human(n, DECIMAL_BYTES_SCALE)

def human_time(n, scale=None):
  ''' Decompose a nonnegative integer `n` into counts by unit from TIME_SCALE.
  '''
  return human(n, TIME_SCALE)

def combine(components, scale):
  ''' Combine a sequence of value components as from human() into an integer.
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

def transcribe(n, scale, max=None, skip_zero=False, sep=''):
  ''' Transcribe a nonnegative integer `n` against `scale`.
      `n`: a nonnegative integer
      `scale`: a sequence of (factor, unit) where factor is the
        size factor to the follow scale and `unit` is the designator
        of the unit
      `max`: the maximum number of components to transcribe
      `skip_zero`: omit components of value 0
  '''
  components = human(n, scale)
  text = []
  for count, unit in reversed(components):
    if skip_zero and count == 0:
      continue
    text.append( str(count) + unit )
    if max is not None and len(text) == max:
      break
  return sep.join(text)

if __name__ == '__main__':
  print(transcribe(2050, BINARY_BYTES_SCALE))
  print(transcribe(2050, DECIMAL_BYTES_SCALE))
  print(transcribe(2050, TIME_SCALE))
