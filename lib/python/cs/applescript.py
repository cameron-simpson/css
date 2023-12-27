#!/usr/bin/python
#
# AppleScript related stuff. - Cameron Simpson <cs@cskk.id.au> 29oct2016
#

def quotestr(s):
  return '"' + s.replace('\\', '\\\\').replace('"', '\\"') + '"'
