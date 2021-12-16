#!/usr/bin/env python
#

''' A few conveniences to do with dates and times.

    There are some other PyPI modules providing richer date handling
    than the stdlib `datetime` module.
    This module mostly contains conveniences used in my other code;
    you're welcome to it, but it does not pretend to be large or complete.
'''

from datetime import date, datetime, tzinfo, timedelta
from time import localtime, mktime, strftime

__version__ = '20210306-post'

DISTINFO = {
    'keywords': ["date", "time", "datetime", "python", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [],
}

class tzinfoHHMM(tzinfo):
  ''' tzinfo class based on +HHMM / -HHMM strings.
  '''

  def __init__(self, shhmm):
    sign, hour, minute = shhmm[0], int(shhmm[1:3]), int(shhmm[3:5])
    if sign == '+':
      sign = 1
    elif sign == '-':
      sign = -1
    else:
      raise ValueError(
          "%s: invalid sign '%s', should be '+' or '-'" % (
              shhmm,
              sign,
          )
      )
    self._tzname = shhmm
    self.sign = sign
    self.hour = hour
    self.minute = minute

  def utcoffset(self, dt):
    return self.hour * 60 + self.minute

  def dst(self, dt):
    return timedelta(0)

  def tzname(self, dt):
    return self._tzname

try:
  from datetime import timezone  # pylint: disable=ungrouped-imports
except ImportError:
  UTC = tzinfoHHMM('+0000')
else:
  UTC = timezone.utc

def isodate(when=None, dashed=True):
  ''' Return a date in ISO8601 YYYY-MM-DD format, or YYYYMMDD if not `dashed`.

      Modern Pythons have a `datetime.isoformat` method, use that.
  '''
  if when is None:
    when = localtime()
  if dashed:
    format_s = '%Y-%m-%d'
  else:
    format_s = '%Y%m%d'
  return strftime(format_s, when)

def datetime2unixtime(dt):
  ''' Convert a `datetime` to a UNIX timestamp.

      *Note*: unlike `datetime.timestamp`,
      if the `datetime` is naive
      it is presumed to be in UTC rather than the local timezone.
  '''
  if dt.tzinfo is None:
    dt = dt.replace(tzinfo=UTC)
  return dt.timestamp()

def unixtime2datetime(unixtime, tz=None):
  ''' Convert a a UNIX timestamp to a `datetime`.

      *Note*: unlike `datetime.fromtimestamp`,
      if `tz` is `None` the UTC timezone is used.
  '''
  if tz is None:
    tz = UTC
  return datetime.fromtimestamp(unixtime, tz=tz)

def localdate2unixtime(d):
  ''' Convert a localtime `date` into a UNIX timestamp.
  '''
  return mktime(date(d.year, d.month, d.day).timetuple())

class UNIXTimeMixin:
  ''' A mixin for classes with a `.unixtime` attribute,
      a `float` storing a UNIX timestamp.
  '''

  def as_datetime(self, tz=None):
    ''' Return `self.unixtime` as a `datetime` with the timezone `tz`.

        *Note*: unlike `datetime.fromtimestamp`,
        if `tz` is `None` the UTC timezone is used.
    '''
    return unixtime2datetime(self.unixtime, tz=tz)

  @property
  def datetime(self):
    ''' The `unixtime` as a UTC `datetime`.
    '''
    return self.as_datetime()

  @datetime.setter
  def datetime(self, dt):
    ''' Set the `unixtime` from a `datetime`.

        *Note*: unlike `datetime.timestamp`,
        if the `datetime` is naive
        it is presumed to be in UTC rather than the local timezone.
    '''
    self.unixtime = datetime2unixtime(dt)
