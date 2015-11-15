#!/bin/usr/binpython
#
# Python 3 specific implementations.
# Provided to separate non-portable syntax across python 2 and 3.
#   - Cameron Simpson <cs@zip.com.au> 12nov2015
#

from builtins import bytes

def raise3(exc_type, exc_value, exc_traceback):
  raise exc_type(exc_value).with_traceback(exc_traceback)

exec_code = exec

# file interface returning bytes from binary file
BytesFile = lambda fp: fp
