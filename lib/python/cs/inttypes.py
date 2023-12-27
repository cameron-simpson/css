#!/usr/bin/python
#
# Various utility int subtypes.
#       - Cameron Simpson <cs@cskk.id.au>
#

DISTINFO = {
    'description': "various trite types associated with integers, such as bitmasks, flags and enums",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [],
}

def _bitvalues(bitnames):
  ''' Compute the association between bit names and values.
      Return name->value and value>name mapping.
  '''
  n = 1
  values = {}
  names = {}
  for name in bitnames:
    values[name] = n
    names[n] = name
    n <<= 1
  return names, values

def _bitnames(i, bitnames):
  ''' Return a sequence of bitnames composing the value `i`,
      given `bitnames` in least significant value first order.
      Returns the list of bitnames and any unnamed remainder.
  '''
  if i < 0:
    raise ValueError("expected an integer >= 0, received: %r" % (i,))
  names = []
  n = 1
  for name in bitnames:
    if i & n:
      names.append(name)
      i -= n
    n <<= 1
  return names, i

def BitMask(*bitnames):
  ''' Return a class for bitmasks, ints with friendly str/repr and
      attribute access for bit tests. Attributes may not be modified;
      use Flags for that.
      Accept individual bit names, most to least significant.
  '''
  names, values = _bitvalues(bitnames)

  class BitMaskClass(int):
    ''' An int with human friendly str() and repr() for a bitmask or flag set.
    '''

    _bitnames = tuple(bitnames)
    _values = values
    _names = names

    def __str__(self):
      names, i = _bitnames(self, bitnames)
      if i > 0:
        names.append(str(i))
      return '|'.join(names) if names else '0'

    def __repr__(self):
      return '<%s 0x%02x %s>' % (self.__class__.__name__, self, self)

    def __getattr__(self, attr):
      ''' Flag mode: access bitmask by name as .name.
      '''
      if attr in self._bitnames:
        return bool(self & self._values[attr])
      raise AttributeError("%r not in %r" % (attr, self._bitnames))

    def __setattr__(self, attr, value):
      if attr in self._bitnames:
        raise AttributeError("you may not set .%s" % (attr,))
      int.__setattr__(self, attr, value)

  return BitMaskClass

def Flags(*flagnames):
  ''' Much like BitMask, but with modifiable attributes.
  '''
  names, values = _bitvalues(flagnames)

  class FlagsClass(object):

    __slots__ = ('value',)

    _flagnames = tuple(flagnames)
    _values = values
    _name = names

    def __init__(self, value=0, **flagvalues):
      for flag, flagvalue in flagvalues.items():
        if flag not in self._flagnames:
          raise ValueError("invalid flag name: %r" % (flag,))
        if flagvalue:
          value |= self._values[flag]
      self.value = value

    def __str__(self):
      names, _ = _bitnames(self.value, self._flagnames)
      return ','.join(names) if names else "0"

    def __int__(self):
      return self.value

    def __getattr__(self, attr):
      try:
        bit = self._values[attr]
      except KeyError:
        raise AttributeError("no such attribute: %s.%s" % (self.__class__.__name__, attr))
      return bool(self.value & bit)

    def __setattr__(self, attr, value):
      if attr == 'value':
        object.__setattr__(self, attr, value)
      else:
        try:
          bit = self._values[attr]
        except KeyError:
          raise AttributeError("no such attribute: %s.%s" % (self.__class__.__name__, attr))
        if value:
          self.value |= bit
        else:
          self.value &= ~bit

  return FlagsClass

def Enum(*names):
  ''' And int enum type with friendly str and repr.
  '''

  class E(int):
    ''' An int with human friendly str() and repr() for an enum counting from 0.
    '''
    _names = tuple(names)

    def __str__(self):
      try:
        return self._names[self]
      except IndexError:
        return int.__str__(self)

  return E

if __name__ == '__main__':
  import sys
  import cs.inttypes_tests
  cs.inttypes_tests.selftest(sys.argv)
