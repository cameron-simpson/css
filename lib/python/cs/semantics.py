#!/usr/bin/env python3

''' Some basic functions and exceptions for various semantics shared by modules.
'''

from inspect import iscoroutinefunction

from cs.deco import decorator

__version__ = '20250103'

DISTINFO = {
    'keywords': ["python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.deco',
    ],
}

class ClosedError(Exception):
  ''' Exception for operations which are invalid when something is closed.
  '''

@decorator
def not_closed(func):
  ''' A decorator to wrap methods of objects with a `.closed` property
      which should raise when `self.closed`.
      This raised `ClosedError` if the object is closed.

      Excample:

          @not_closed
          def doit(self):
              ... proceed know we were not closed ...
  '''

  if iscoroutinefunction(func):

    async def not_closed_wrapper(self, *a, **kw):
      ''' Wrapper function to check that this instance is not closed.
      '''
      if self.closed:
        raise ClosedError(
            "%s: %s: already closed" % (not_closed_wrapper.__name__, self)
        )
      return await func(self, *a, **kw)

  else:

    def not_closed_wrapper(self, *a, **kw):
      ''' Wrapper function to check that this instance is not closed.
      '''
      if self.closed:
        raise ClosedError(
            "%s: %s: already closed" % (not_closed_wrapper.__name__, self)
        )
      return func(self, *a, **kw)

  return not_closed_wrapper
