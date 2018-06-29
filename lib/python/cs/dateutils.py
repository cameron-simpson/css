#!/usr/bin/python
#

''' Convenience functions to do with date and time.
'''

from datetime import tzinfo, timedelta, date
from time import localtime, strftime, strptime

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
          "%s: invalid sign '%s', should be '+' or '-'" % (shhmm, sign,))
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

def isodate(when=None, dashed=True):
  ''' Return a date in ISO8601 YYYY-MM-DD format, or YYYYMMDD if not `dashed`.
  '''
  if when is None:
    when = localtime()
  if dashed:
    format_s = '%Y-%m-%d'
  else:
    format_s = '%Y%m%d'
  return strftime(format_s, when)

def a2date(s):
  ''' Create a date object from an ISO8601 YYYY-MM-DD date string.
  '''
  return date(*strptime(s, "%Y-%m-%d")[0:3])

def parse_date(datestr):
  ''' Parse a date specifcation and return a datetime.date, or None for empty strings.
  '''
  datestr = datestr.strip()
  if not datestr:
    return None
  try:
    parsed = strptime(datestr, '%Y-%m-%d')
  except ValueError:
    try:
      parsed = strptime(datestr, '%d %B %Y')
    except ValueError:
      parsed = strptime(datestr, '%d/%m/%Y')
  return date(parsed.tm_year, parsed.tm_mon, parsed.tm_mday)
