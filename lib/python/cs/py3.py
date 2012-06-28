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
else:
  unicode = str
  StringTypes = (str,)
  from io import BytesIO, StringIO
