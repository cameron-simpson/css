#!/usr/bin/python
#
# AppleScript related stuff. - Cameron Simpson <cs@zip.com.au> 29oct2016
#

def quotestr(s):
  return '"' + s.replace('\\', '\\\\').replace('"', '\\"') + '"'
