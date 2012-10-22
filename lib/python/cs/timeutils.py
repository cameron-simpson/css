#!/usr/bin/python
#
# Convenience routines for timing.
#       - Cameron Simpson <cs@zip.com.au> 01feb2010
#

import datetime
import time

class TimeoutError(StandardError):
  def __init__(self, message, timeout=None):
    if timeout is None:
      msg = "%s: timeout exceeded" % (message,)
    else:
      msg = "%s: timeout exceeded (%ss)" % (message, timeout,)
    StandardError.__init__(self, msg)

def timeFunc(func, *args, **kw):
  ''' Run the supplied function and arguments.
      Return a the elapsed time in seconds and the function's own return value.
  '''
  from cs.logutils import LogTime
  L = LogTime(repr(func))
  with L:
    ret = func(*args,**kw)
  return L.elapsed, ret

def tmFromISO(isodate):
  '''  Parse an ISO8601 date string and return a struct_time.
  '''
  return time.strptime(isodate, "%Y-%m-%dT%H:%M:%S")

def timeFromISO(isodate, islocaltime=False):
  '''  Parse an ISO8601 date string and return seconds since the epoch.
       If islocaltime is true convert using localtime(tm) otherwise use
       gmtime(tm).
  '''
  tm = tmFromISO(isodate)
  if islocaltime:
    return time.mktime(tm)
  raise NotImplementedError("only localtime supported just now")

def ISOtime(gmtime):
  ''' Produce an ISO8601 timestamp string from a UNIX time.
  '''
  dt = datetime.datetime.fromtimestamp(int(gmtime))
  if dt.microsecond != 0:
    from cs.logutils import warning
    warning("ISOtime: fromtimestamp(%d).microsecond = %s", gmtime, dt.microsecond)
  return dt.isoformat()

if __name__ == '__main__':
  iso = '2012-08-24T11:12:13'
  print "iso = %r" % (iso,)
  tm = tmFromISO(iso)
  print "tm = %r" % (tm,)
  when = timeFromISO(iso, islocaltime=True)
  print "time = %r" % (when,)
