#!/usr/bin/python
#
''' I find the supplied python traceback facilities quite awkward.
    These functions provide convenient facilities.
'''

from collections import namedtuple
import sys
from traceback import extract_stack

DISTINFO = {
    'description': "Convenience functions for the python execution stack.",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [],
}

_Frame = namedtuple('Frame', 'filename lineno funcname linetext')

class Frame(_Frame):
  ''' Namedtuple for stack frame contents.
  '''
  def __str__(self):
    return "%s:%d: %s" % (self.filename, self.lineno, self.linetext)

def frames():
  ''' Return the current stack as a list of Frame objects.
  '''
  return [ Frame(*f) for f in extract_stack()[:-1] ]

def caller(frame_index=-3):
  ''' Return the `Frame` of the caller's caller.

      Useful `frame_index` values:
      * `-1`: caller, this function
      * `-2`: invoker, who wants to know the caller
      * `-3`: the calling function of the invoker
  '''
  return Frame(*frames()[frame_index])

def stack_dump(fp=None, indent=0, Fs=None):
  ''' Recite current or supplied stack to `fp`, default sys.stderr.
  '''
  if fp is None:
    fp = sys.stderr
  if Fs is None:
    Fs = frames()
  for F in Fs:
    if indent > 0:
      fp.write(' ' * indent)
    fp.write(str(F))
    fp.write('\n')

if __name__ == '__main__':
  import cs.py.stack_tests
  cs.py.stack_tests.selftest(sys.argv)
