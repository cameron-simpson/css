#!/usr/bin/env python
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

      Modern Pythons have a `datetime.isoformat` method, use that.
  '''
  if when is None:
    when = localtime()
  if dashed:
    format_s = '%Y-%m-%d'
  else:
    format_s = '%Y%m%d'
  return strftime(format_s, when)
