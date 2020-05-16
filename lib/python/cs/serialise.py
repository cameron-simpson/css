#!/usr/bin/python -tt
#
# Common serialisation functions.
# - Cameron Simpson <cs@cskk.id.au>
#

''' Some serialising functions, now mostly a thin wrapper for the cs.binary functions.
'''

from cs.binary import BSUInt, BSData, BSString

DISTINFO = {
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': ['cs.binary'],
}

def get_bs(data, offset=0):
  ''' Read an extensible byte serialised unsigned int from `data` at `offset`.
      Return value and new offset.

      Continuation octets have their high bit set.
      The value is big-endian.

      If you just have a bytes instance, this is the go. If you're
      reading from a stream you're better off with `cs.binary.BSUint`.
  '''
  n = 0
  b = 0x80
  while b & 0x80:
    b = data[offset]
    offset += 1
    n = (n << 7) | (b & 0x7f)
  return n, offset

put_bs = BSUInt.transcribe_value

# old names
get_bsdata = BSData.value_from_bytes
put_bsdata = BSData.transcribe_value

get_bss = BSString.value_from_bytes
put_bss = BSString.transcribe_value

if __name__ == '__main__':
  import sys
  import cs.serialise_tests
  cs.serialise_tests.selftest(sys.argv)
