#!/usr/bin/python
#

''' I find the supplied python traceback facilities quite awkward.
    These functions provide convenient facilities.
'''

from __future__ import print_function
from collections import namedtuple
import sys
from traceback import extract_stack

__version__ = '20240412-post'

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

try:
  from traceback import FrameSummary, StackSummary
except ImportError:
  # provide _incomplete_ simple implementations

  class FrameSummary(namedtuple('FrameSummary', 'filename lineno name line')):
    ''' A `namedtuple` for stack frame contents.
    '''

    def __str__(self):
      return "%s:%d: %s" % (self.filename, self.lineno, self.line)

    # compatibility with my prior Frame class
    @property
    def funcname(self):
      ''' The frame function name.
      '''
      return self.name

    @property
    def linetext(self):
      ''' The line of source code.
      '''
      return self.line

  class StackSummary(list):

    @classmethod
    def extract(
        cls, frame_gen, limit=None, lookup_lines=None, capture_locals=False
    ):
      # not yet implemented
      assert limit is None
      assert capture_locals is None
      assert lookup_lines is None
      return cls(FrameSummary(raw_frame) for raw_frame, lineno in frame_gen)

    @classmethod
    def from_list(cls, frame_list):
      return cls(FrameSummary(raw_frame) for raw_frame in frame_list)

    def format(self):
      return [self.format_frame_summary(frame) for frame in self]

def frames():
  ''' Return the current stack as a list of `Frame` objects.
  '''
  return StackSummary.from_list(extract_stack())

def caller(frame_index=-3):
  ''' Return the `Frame` of the caller's caller.
      Returns `None` if `frame_index` is out of range.

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

def stack_dump(
    f=None,
    indent=0,
    frames=None,
    skip=None,
    select=None,
    format_frame=None,
):
  ''' Recite current or supplied stack to `f`, default `sys.stderr`.

      Parameters:
      * `f`: the output file object, default `sys.stderr`
      * `indent`: how many spaces to indent the stack lines, default `0`
      * `frames`: the stack `Frame`s to write,
        default obtained from the current stack
      * `skip`: the number of `Frame`s to trim from the end of `frames`;
        if `frames` is `None` this defaults to `2` to trim the `Frame`s
        for the `stack_dump` function and its call to `frames()`,
        otherwise the default is `0` to use the supplied `Frame`s as is
      * `select`: if not `None`, select particular frames;
        if `select` is a `str` it must be present in the frame filename;
        otherwise `select(frame)` must be true
  '''
  if f is None:
    f = sys.stderr
  if frames is None:
    frames = frames()
    if skip is None:
      skip = 2
  elif skip is None:
    skip = 0
  if skip > 0:
    frames = frames[:-skip]
  summary = StackSummary.from_list(list(frames))
  if format_frame is None:
    try:
      format_frame = summary.format_frame_summary
    except AttributeError:
      format_frame = lambda frame: str(frame) + '\n'
  for F in frames:
    if select is not None:
      if isinstance(select, str):
        if select not in F.filename:
          continue
      else:
        if not select(F):
          continue
    formatted = format_frame(F)
    indent_s = ' ' * indent
    print(
        indent_s,
        formatted.rstrip('\n').replace('\n', indent_s + '\n'),
        file=f,
        sep='',
    )
    print(
        indent_s,
        "  ",
        F.line,
        file=f,
        sep='',
    )

if __name__ == '__main__':
  import cs.py.stack_tests
  cs.py.stack_tests.selftest(sys.argv)
