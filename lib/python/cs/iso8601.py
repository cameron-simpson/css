#!/usr/bin/python
#
# ISO8601 related facilities.
#   - Cameron Simpson <cs@cskk.id.au> 09jun2017
#

from datetime import datetime, timezone

def strformat(sep=None):
  if sep is None:
    sep = 'T'
  return sep.join( ('%Y-%m-%d', '%H:%M:%S') )

# strptime format for a Z style ISO8601 date string
ISO8601_FORMAT_Z = strformat() + 'Z'

def parseZ(s):
  ''' Parse an ISO8601 YYYY-MM-DDTHH:MM:SSZ time string, return a datetime.
  '''
  dt = datetime.strptime(s, ISO8601_FORMAT_Z)
  dt = datetime.combine(dt.date(), dt.time(), timezone.utc)
  return dt

def formatZ(dt, sep=None, tzinfo=None):
  if tzinfo is None:
    tzinfo = dt.tzinfo
  fmt = strformat(sep=sep) + 'Z'
  if tzinfo is None:
    # pretend it is UTC
    pass
  elif tzinfo.utcoffset(dt) != 0:
    # convert to UTC
    dt = dt.astimezone(timezone.utc)
  return dt.strftime(fmt)
