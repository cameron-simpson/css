#!/usr/bin/python
#
# Various utility int subtypes.
#       - Cameron Simpson <cs@zip.com.au>
#

import sys
from cs.logutils import D

def BitMask(*bitnames):
  ''' Return a factory function for bitmasks, ints with friendly str/repr.
      Accept individual bit names, most to least significant.
  '''
  n = 1
  values = {}
  names = {}
  for name in bitnames:
    values[name] = n
    names[n] = name
    n <<= 1

  class B(int):
    ''' An int with human friendly str() and repr() for a bitmask or flag set.
    '''

    _bitnames = tuple(bitnames)
    _values = values
    _names = names

    def __str__(self):
      f = self
      n = 1
      names = []
      for name in self._bitnames:
        if f & n:
          names.append(name)
          f &= ~n
        n <<= 1
      if f > 0:
        names.append("%d" % (n,))
      return '|'.join(names) if names else '0'

    def __repr__(self):
      return '<%s 0x%02x %s>' % (self.__class__.__name__, self, self)

    def __getattr__(self, attr):
      ''' Flag mode: access bitmask by name as .name.
      '''
      if attr in self._bitnames:
        return bool(self & self._values[attr])
      raise AttributeError("%r not in %r" % (attr, self._bitnames))

  return B

def Enum(*names):
  n = 0
  revnames = {}
  for name in names:
    revnames[name] = n
    n += 1
  def f(n):
    en = _Enum(n)
    en.names = names
    en.revnames = revnames
    return en
  return f

class _Enum(int):
  ''' An int with human friendly str() and repr() for an enum counting from 0.
  '''

  def __str__(self):
    try:
      return self.names[self]
    except IndexError:
      return int.__str__(self)

if __name__ == '__main__':
  import cs.inttypes_tests
  cs.inttypes_tests.selftest(sys.argv)
