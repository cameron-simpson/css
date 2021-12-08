#!/usr/bin/python
#

''' Convenience routines for timing.
'''

from __future__ import print_function
import datetime
import time

__version__ = '20211208'

DISTINFO = {
    'description':
    "convenience routines for times and timing",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [],
}

def time_func(func, *args, **kw):
  ''' Run the supplied function and arguments.
      Return a the elapsed time in seconds and the function's own return value.
  '''
  from cs.logutils import LogTime
  with LogTime(repr(func)) as L:
    ret = func(*args, **kw)
  return L.elapsed, ret

def tm_from_ISO(isodate):
  '''  Parse an ISO8601 date string and return a struct_time.
  '''
  return time.strptime(isodate, "%Y-%m-%dT%H:%M:%S")

def time_from_ISO(isodate, islocaltime=False):
  '''  Parse an ISO8601 date string and return seconds since the epoch.
       If islocaltime is true convert using localtime(tm) otherwise use
       gmtime(tm).
  '''
  tm = tm_from_ISO(isodate)
  if islocaltime:
    return time.mktime(tm)
  raise NotImplementedError("only localtime supported just now")

def ISOtime(gmtime):
  ''' Produce an ISO8601 timestamp string from a UNIX time.
  '''
  dt = datetime.datetime.fromtimestamp(int(gmtime))
  if dt.microsecond != 0:
    from cs.logutils import warning
    warning(
        "ISOtime: fromtimestamp(%d).microsecond = %s", gmtime, dt.microsecond
    )
  return dt.isoformat()

def sleep(delay):
  ''' time.sleep() sometimes sleeps significantly less that requested.
      This function calls time.sleep() until at least `delay` seconds have
      elapsed, trying to be precise.
  '''
  if delay < 0:
    raise ValueError(
        "cs.timeutils.sleep: delay should be >= 0, given %g" % (delay,)
    )
  t0 = time.time()
  end = t0 + delay
  while t0 < end:
    delay = end - t0
    time.sleep(delay)
    elapsed = time.time() - t0
    if elapsed < delay:
      from cs.logutils import debug
      debug("time.sleep(%ss) took only %ss", delay, elapsed)
    t0 = time.time()

if __name__ == '__main__':
  iso = '2012-08-24T11:12:13'
  print("iso = %r" % (iso,))
  tm_val = tm_from_ISO(iso)
  print("tm = %r" % (tm_val,))
  when = time_from_ISO(iso, islocaltime=True)
  print("time = %r" % (when,))
