#!/usr/bin/python
#
# Various utility int subtypes.
#       - Cameron Simpson <cs@zip.com.au>
#

def BitMask(*bitnames):
  def f(n):
    bm = _BitMask(n)
    bm.bitnames = bitnames
    return bm
  return f
  
class _BitMask(int):
    ''' An int with human friendly str() and repr() for a bitmask or flag set.
    '''

    def __str__(self):
      f = self
      n = 1
      names = []
      for name in self.bitnames:
        if f & n:
          names.append(name)
          f &= ~n
        n <<= 1
      if n:
        names.append("%d" % (n,))
      return '|'.join(names) if names else '0'

    def __repr__(self):
      return '<%s 0x%02x %s>' % (self.__class__.__name__, self, self)

    @property
    def vars(self):
      d = {}
      n = 1
      for name in self.bitnames:
        d[name] = n
        n <<= 1
      return d

if __name__ == '__main__':
  B = BitMask('a', 'b', 'c')
  n = B(9)
  print("%d => %s" % (n, n))
  print("%r" % (n.vars,))
