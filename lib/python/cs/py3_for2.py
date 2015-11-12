#!/usr/bin/python
#
# Python 2 specific implementations.
# Provided to separate non-portable syntax across python 2 and 3.
#   - Cameron Simpson <cs@zip.com.au> 12nov2015
# 

def raise3(exc_type, exc_value, exc_traceback):
  raise exc_type, exc_value, exc_traceback
