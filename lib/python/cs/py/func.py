#!/usr/bin/python
#
# Convenience routines for python functions.
#       - Cameron Simpson <cs@zip.com.au> 15apr2014
#

def funcname(func):
  ''' Return a name for the supplied function `func`.
      Several objects do not have a __name__ attribute, such as partials.
  '''
  try:
    return func.__name__
  except AttributeError:
    return str(func)
