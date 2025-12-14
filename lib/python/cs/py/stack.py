#!/usr/bin/python
#

''' I find the supplied python traceback facilities quite awkward.
    These functions provide convenient facilities.
'''

from __future__ import print_function
from collections import namedtuple
import sys
from traceback import extract_stack

__version__ = '20250724-post'

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

    @property
    def linetext(self):
      ''' The line of source code.
      '''
      return self.line

  class StackSummary(list):
    ''' Fallback `StackSummary` class for older Pythons.
    '''

    @classmethod
    def extract(
        cls, frame_gen, limit=None, lookup_lines=None, capture_locals=False
    ):
      ''' Constract a `StackSummary` from an iterable of stack frames.
      '''
      # not yet implemented
      assert limit is None
      assert capture_locals is None
      assert lookup_lines is None
      return cls(FrameSummary(raw_frame) for raw_frame, lineno in frame_gen)

    @classmethod
    def from_list(cls, frame_list):
      ''' Constract a `StackSummary` from a list of stack frames.
      '''
      return cls(FrameSummary(raw_frame) for raw_frame in frame_list)

    def format(self):
      ''' Return a list of the stack frames formatted as strings.
      '''
      return [self.format_frame_summary(frame) for frame in self]

    @staticmethod
    def format_frame_summary(frame):
      ''' The default frame format function.
          This implementation produces a single line of text.
      '''
      return (
          "%s:%d: %s: %s\n" %
          (frame.filename, frame.lineno, frame.name, frame.line)
      )

def frames(source=None, limit=None):
  ''' Return the current stack as a `StackSummary` instance, a list
      of `FrameSummary` instances. Crop the returned list at `limit`
      if it is not `None`.

      If `source` is omitted or `None`, obtain the source stack
      from `traceback.extract_stack()`.
      Otherwise if `source` has a `.tb_frame` attribute (like a
      `traceback` object) or `source` has a `.__traceback__` attribute
      (like an exception), call `traceback.extract_stack` with that
      to obtain the source stack.
  '''
  if source is None:
    # default to the current stack
    stack = extract_stack()
  else:
    # see if we can locate a stack frame
    tb_frame = None
    # does it look like a traceback object?
    try:
      tb_frame = source.tb_frame
    except AttributeError:
      # does it looks like an exception?
      try:
        tb_frame = source.__traceback__.tb_frame
      except AttributeError:
        pass
    if tb_frame is None:
      raise TypeError(
          "stack_dump: unhandled stack type, source=%s:%s" %
          (type(source).__name__, repr(source))
      )
    # extract the stack from the frame
    stack = extract_stack(f=tb_frame)
  if limit is not None:
    stack = stack[:limit]
  return StackSummary.from_list(stack)

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
    summary=None,
    skip=None,
    select=None,
    format_frame=None,
):
  ''' Recite current or supplied stack to `f`, default `sys.stderr`.

      Parameters:
      * `f`: the output file object, default `sys.stderr`
      * `indent`: how many spaces to indent the stack lines, default `0`
      * `summary`: the stack `Frame`s to write,
        default obtained from the current stack
      * `skip`: the number of `Frame`s to trim from the end of `summary`;
        if `summary` is `None` this defaults to `2` to trim the `Frame`s
        for the `stack_dump` function and its call to `frames()`,
        otherwise the default is `0` to use the supplied `Frame`s as is
      * `select`: if not `None`, select particular frames;
        if `select` is a `str` it must be present in the frame filename;
        otherwise `select(frame)` must be true
  '''
  if f is None:
    f = sys.stderr
  if summary is None:
    summary = frames()
    if skip is None:
      skip = 2
  elif not isinstance(summary, StackSummary):
    summary = StackSummary(list(summary))
    if skip is None:
      skip = 0
  assert isinstance(summary, StackSummary)
  if skip > 0:
    summary[-skip:] = []
  if format_frame is None:
    try:
      format_frame = summary.format_frame_summary
    except AttributeError:
      format_frame = lambda frame: "%s:%d: %s: %s\n" % (
          frame.filename, frame.lineno, frame.name, frame.line
      )
  for F in summary:
    if select is not None:
      if isinstance(select, str):
        if select not in F.filename:
          continue
      elif not select(F):
        continue
    formatted = format_frame(F)
    indent_s = ' ' * indent
    print(
        indent_s,
        formatted.rstrip('\n').replace('\n', indent_s + '\n'),
        file=f,
        sep='',
    )

if __name__ == '__main__':
  import cs.py.stack_tests
  cs.py.stack_tests.selftest(sys.argv)
