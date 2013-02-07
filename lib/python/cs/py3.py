#!/usr/bin/python -tt
#
# Python3 helpers to aid code sharing between python2 and python3.
#       - Cameron Simpson <cs@zip.com.au> 28jun2012
#

import sys

if  sys.hexversion < 0x03000000:
  globals()['unicode'] = unicode
  from types import StringTypes
  try:
    from cStringIO import StringIO as BytesIO
  except ImportError:
    from StringIO import StringIO as BytesIO
  StringIO = BytesIO    # horribly wrong, I know
  from Queue import Queue
else:
  unicode = str
  StringTypes = (str,)
  from io import BytesIO, StringIO
  from queue import Queue

def raise3(exc_type, exc_value, exc_traceback):
  if sys.hexversion >= 0x03000000:
    raise exc_type(exc_value).with_traceback(exc_traceback)
  else:
    # subterfuge to let this pass a python3 parser; ugly
    exec('raise exc_type, exc_value, exc_traceback')
