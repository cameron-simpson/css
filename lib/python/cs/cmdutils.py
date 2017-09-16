#!/usr/bin/python
#
# Convenience functions for working with the Cmd module.
#   - Cameron Simpson <cs@cskk.id.au> 03sep2015
#

from __future__ import print_function, absolute_import
import os
import sys
import io
import subprocess
from cs.pfx import Pfx

def docmd(dofunc):
  ''' Decorator for Cmd subclass methods.
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
