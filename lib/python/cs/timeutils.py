#!/usr/bin/python
#
# Convenience routines for timing.
#       - Cameron Simpson <cs@zip.com.au> 01feb2010
#

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
       If islocal
  '''
  tm = tmFromISO(isoformat)
  if islocaltime:
    return localtime(tm)
  return gmtime(tm)

def ISOtime(gmtime):
  ''' Produce an ISO8601 timestamp string from a UNIX time.
  '''
  import datetime
  dt = datetime.datetime.fromtimestamp(int(gmtime))
  if dt.microsecond != 0:
    from cs.logutils import warning
    warning("ISOtime: fromtimestamp(%d).microsecond = %s", gmtime, dt.microsecond)
  return dt.isoformat()
