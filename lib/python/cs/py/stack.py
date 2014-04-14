#!/usr/bin/python
#
# I find the supplied python traceback facilities quite awkward.
# These functions provide convenient facilities.
#       - Cameron Simpson <cs@zip.com.au> 14apr2014
#

import sys
from collections import namedtuple
from traceback import extract_stack

Frame = namedtuple('StackFrame', 'funcname file lineno linetext')

def frames():
  ''' Return the current stack as a list of Frame objects.
  '''
  return [ Frame(*f) for f in extract_stack() ]

def caller():
  ''' Return the frame of the caller's caller.
  '''
  return frames()[2]

if __name__ == '__main__':
  import cs.py.stack_tests
  cs.py.stack_tests.selftest(sys.argv)
