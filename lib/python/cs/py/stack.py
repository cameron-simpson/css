#!/usr/bin/python
#

''' I find the supplied python traceback facilities quite awkward.
    These functions provide convenient facilities.
'''

from collections import namedtuple
import sys
from traceback import extract_stack

DISTINFO = {
    'description':
    "Convenience functions for the python execution stack.",
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
  ''' A `namedtuple` for stack frame contents.
  '''

  def __str__(self):
    return "%s:%d: %s" % (self.filename, self.lineno, self.linetext)

def frames():
  ''' Return the current stack as a list of `Frame` objects.
  '''
  return [Frame(*f) for f in extract_stack()[:-1]]

def caller(frame_index=-3):
  ''' Return the `Frame` of the caller's caller.
      Return `None` if `frame_index` is out of range.

      Useful `frame_index` values:
      * `-1`: caller, this function
      * `-2`: invoker, who wants to know the caller
      * `-3`: the calling function of the invoker

      The default `from_index` value is `-3`.
  '''
  frs = frames()
  try:
    return frs[frame_index]
  except IndexError:
    return None

def stack_dump(fp=None, indent=0, Fs=None, skip=None):
  ''' Recite current or supplied stack to `fp`, default `sys.stderr`.

      Parameters:
      * `fp`: the output file object, default `sys.stderr`
      * `indent`: how many spaces to indent the stack lines, default `0`
      * `Fs`: the stack `Frame`s to write,
        default obtained from the current stack
      * `skip`: the number of `Frame`s to trim from the end of `Fs`;
        if `Fs` is `None` this defaults to `2` to trim the `Frame`s
        for the `stack_dump` function and its call to `frames()`,
        otherwise the default is `0` to use the supplied `Frame`s as is
  '''
  if fp is None:
    fp = sys.stderr
  if Fs is None:
    Fs = frames()
    if skip is None:
      skip = 2
  elif skip is None:
    skip = 0
  if skip > 0:
    Fs = Fs[:-skip]
  for F in Fs:
    if indent > 0:
      fp.write(' ' * indent)
    fp.write(str(F))
    fp.write('\n')

if __name__ == '__main__':
  import cs.py.stack_tests
  cs.py.stack_tests.selftest(sys.argv)
