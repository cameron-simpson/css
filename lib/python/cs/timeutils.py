#!/usr/bin/python
#
# Convenience routines for timing.
#       - Cameron Simpson <cs@zip.com.au> 01feb2010
#

def timeFunc(func, *args, **kw):
  """ Run the supplied function and arguments.
      Return a the elapsed time in seconds and the function's own return value.
  """
  from cs.logutils import LogTime
  L = LogTime(repr(func))
  with L:
    ret = func(*args,**kw)
  return L.elapsed, ret
