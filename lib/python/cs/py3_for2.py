#!/usr/bin/python
#
# Python 2 specific implementations.
# Provided to separate non-portable syntax across python 2 and 3.
#   - Cameron Simpson <cs@zip.com.au> 12nov2015
# 

def raise3(exc_type, exc_value, exc_traceback):
  raise exc_type, exc_value, exc_traceback

def exec_code(code, *a):
  if not a:
    exec code
  else:
    gs = a.pop(0)
    if not a:
      exec code in gs
    else:
      ls = a.pop(0)
      if not a:
        exec code in gs, ls
      else:
        raise ValueError("exec_code: extra arguments after locals: %r" % (a,))
