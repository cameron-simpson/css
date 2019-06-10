#!/usr/bin/python
#

''' Convenience functions for working with the Cmd module
    and other command line related stuff.
    - Cameron Simpson <cs@cskk.id.au> 03sep2015
'''

from __future__ import print_function, absolute_import
from contextlib import contextmanager
from getopt import getopt, GetoptError
from logging import warning, exception
from cs.mappings import StackableValues
from cs.pfx import Pfx

def docmd(dofunc):
  ''' Decorator for Cmd subclass methods
      to supply some basic quality of service.

      This decorator:
      - wraps the function call in a `cs.pfx.Pfx` for context
      - intercepts `getopt.GetoptError`s, issues a `warning`
        and runs `self.do_help` with the method name,
        then returns `None`
      - intercepts other `Exception`s,
        issues an `exception` log message
        and returns `None`

      The intended use is to decorate `cmd.Cmd` `do_`* methods:

        @docmd
        def do_something(...):
          ... do something ...
  '''

  def wrapped(self, *a, **kw):
    funcname = dofunc.__name__
    if not funcname.startswith('do_'):
      raise ValueError("function does not start with 'do_': %s" % (funcname,))
    argv0 = funcname[3:]
    with Pfx(argv0):
      try:
        return dofunc(self, *a, **kw)
      except GetoptError as e:
        warning("%s", e)
        self.do_help(argv0)
        return None
      except Exception as e:
        exception("%s", e)
        return None

  wrapped.__doc__ = dofunc.__doc__
  return wrapped
