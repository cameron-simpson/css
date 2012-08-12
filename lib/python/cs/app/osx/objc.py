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
from AddressBook import objc, NSDate, ABMultiValueCoreDataWrapper, NSCFDictionary, NSDateComponents
from cs.dateutils import tzinfoHHMM
from cs.logutils import warning, D

def convertObjCtype(o):
  if o is None:
    return o
  # more specialised types first
  if isinstance(o, NSDate):
    return convertNSDate(o)
  if isinstance(o, NSDateComponents):
    return convertNSDateComponents(o)
  if isinstance(o, ABMultiValueCoreDataWrapper):
    return [ convertObjCtype(o.valueAtIndex_(i)) for i in range(o.count()) ]
  # specific generic types later
  t = type(o)
  if t is int:
    return o
  if t is objc.pyobjc_unicode:
    return unicode(o)
  if t is objc._pythonify.OC_PythonInt:
    return int(o)
  if hasattr(o, 'keys'):
    ##warning("pretending a dict - unrecognised <%s %s>", t, repr(o).replace("\n", ""))
    return dict( [ (k, o[k]) for k in o.keys() ] )
  raise TypeError("convertObjCtype: o = %s %r", t, o)

def convertNSDate(d):
  d_date, d_time, d_zone = d.description().split()
  year, month, day = [ int(d_d) for d_d in d_date.split('-') ]
  hour, minute, second = [ int(d_t) for d_t in d_time.split(':') ]
  tz = tzinfoHHMM(d_zone)
  return datetime(year, month, day, hour, minute, second, 0, tz)

def convertNSDateComponents(d):
  desc = d.description()
  ##D("NSDateComponents str = %s", d)
  ##D("NSDateComponents desc = %r", desc)
  year = convertObjCtype(d.year())
  ##D("year = %s %r", type(year), year)
  month = convertObjCtype(d.month())
  ##D("month = %s %r", type(month), month)
  day = convertObjCtype(d.day())
  ##D("day = %s %r", type(day), day)
  return datetime(year, month, day)

