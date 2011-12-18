#!/usr/bin/python
#
# Assorted algorithms.
#       - Cameron Simpson <cs@zip.com.au> 26sep2010
#

from types import StringTypes

def collate(seq, attr, select=None):
  ''' Collate members of a sequence by some attribute.
      If `select` is supplied and not None, collate only members
      whose attrtibute value is in `select`.
      If `select` is a string or numeric type it is tested for equality.
  '''
  if select is not None:
    t = type(select)
    if t in StringTypes or t in (int, long, float):
      select = (select,)

  collation = {}
  for S in seq:
    key = getattr(S, attr)
    if select is not None and key not in select:
      continue
    collation.setdefault(key, []).append(S)

  return collation

if __name__ == '__main__':
  import sys
  import cs.alg_tests
  cs.alg_tests.selftest(sys.argv)
