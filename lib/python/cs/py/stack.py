#!/usr/bin/python
#
# I find the supplied python traceback facilities quite awkward.
# These functions provide convenient facilities.
#       - Cameron Simpson <cs@zip.com.au> 14apr2014
#

DISTINFO = {
    'description': "Convenience functions for the python execution stack.",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
        ],
}

import sys
from collections import namedtuple
from traceback import extract_stack

_Frame = namedtuple('Frame', 'filename lineno functionname linetext')

class Frame(_Frame):
  def __str__(self):
    return "%s:%d: %s" % (self.filename, self.lineno, self.linetext)

def frames():
  ''' Return the current stack as a list of Frame objects.
  '''
  return [ Frame(*f) for f in extract_stack()[:-1] ]

def caller():
  ''' Return the frame of the caller's caller.
  '''
  # -1: caller, this function
  # -2: invoker, who wants to know the caller
  # -3: the calling function of the invoker
  return frames()[-3]

def stack_dump(fp=None, indent=0):
  ''' Recite current stack to `fp`, default sys.stderr.
  '''
  if fp is None:
    fp = sys.stderr
  for F in frames():
    if indent > 0:
      fp.write(' ' * indent)
    fp.write(str(F))
    fp.write('\n')

if __name__ == '__main__':
  import cs.py.stack_tests
  cs.py.stack_tests.selftest(sys.argv)
