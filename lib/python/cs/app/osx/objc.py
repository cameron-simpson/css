#!/usr/bin/python
#
# MacOSX Objective C facilities.
# Tweaks sys.path if necessary.
# Includes some type conversion.
#       - Cameron Simpson <cs@zip.com.au> 29jun2011
#

import sys
objc_path = '/System/Library/Frameworks/Python.framework/Versions/Current/Extras/lib/python/PyObjC'
if objc_path not in sys.path:
  sys.path.append(objc_path)
from datetime import datetime, tzinfo
from AddressBook import objc, NSDate, ABMultiValueCoreDataWrapper, NSCFDictionary

def convertObjCtype(o):
  if o is None:
    return o
  t = type(o)
  if t == objc.pyobjc_unicode:
    return unicode(o)
  if issubclass(o.__class__, NSDate):
    return convertNSDate(o)
  if t == ABMultiValueCoreDataWrapper:
    return [ convertObjCtype(o.valueAtIndex_(i)) for i in range(o.count()) ]
  if t == NSCFDictionary:
    return dict( [ (k, o[k]) for k in o.keys() ] )
  if t == objc._pythonify.OC_PythonInt:
    return int(o)
  raise ValueError, "can't convert <%s %s>" % (t, o)

def convertNSDate(d):
  d_date, d_time, d_zone = d.description().split()
  year, month, day = [ int(d_d) for d_d in d_date.split('-') ]
  hour, minute, second = [ int(d_t) for d_t in d_time.split(':') ]
  tz = _offsetTZInfo(d_zone)
  return datetime(year, month, day, hour, minute, second, 0, tz)

class _offsetTZInfo(tzinfo):
  ''' tzinfo class based on +HHMM / -HHMM strings.
  '''

  def __init__(self, shhmm):
    sign, hour, minute = shhmm[0], int(shhmm[1:3]), int(shhmm[3:5])
    if sign == '+':
      sign = 1
    elif sign == '-':
      sign = -1
    else:
      raise ValueError, "invalid sign '%s', should be '+' or '-'" % (sign,)
    self.sign = sign
    self.hour = hour
    self.minute = minute

  def utcoffset(self, dt):
    return self.hour*60 + self.minute
