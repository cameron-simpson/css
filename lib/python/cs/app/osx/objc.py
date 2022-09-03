#!/usr/bin/env python3
#
# MacOSX Objective C facilities.
# Requires the PyObjC package from PyPI.
# Tweaks sys.path if necessary.
# Includes some type conversion.
# See also: https://pyobjc.readthedocs.io/en/latest/index.html
# - Cameron Simpson <cs@cskk.id.au> 29jun2011
#

from collections import defaultdict
from datetime import datetime
from os.path import isabs as isabspath
import sys

##objc_path = '/System/Library/Frameworks/Python.framework/Versions/Current/Extras/lib/python/PyObjC'
##if objc_path not in sys.path:
##  sys.path.append(objc_path)

import objc
from AddressBook import NSDate, ABMultiValueCoreDataWrapper, NSCFDictionary, NSDateComponents
from Foundation import NSBundle

##from cs.x import X
from cs.dateutils import tzinfoHHMM
from cs.deco import fmtdoc
from cs.logutils import warning
from cs.obj import SingletonMixin
from cs.pfx import Pfx, pfx, pfx_call

DEFAULT_BUNDLE_ID_PREFIX = 'com.apple.'

class _BundlesDict(defaultdict):

  def __missing__(self, key):
    file_as = [key]
    if isabspath(key):
      bundle_path = key
      bundle = pfx_call(
          NSBundle.bundleWithPath_,
          pfx_call(objc.pathForFramework, bundle_path)
      )
      if bundle is None:
        raise FileNotFoundError(bundle_path)
      ##bundle_id = bundle.infoDictionary()['CFBundleIdentifier']
      bundle_id = bundle.bundleIdentifier()
      file_as.append(bundle_id)
    else:
      bundle_id = key
      if '.' not in bundle_id:
        bundle_id = DEFAULT_BUNDLE_ID_PREFIX + bundle_id
        file_as.append(bundle_id)
      bundle = pfx_call(NSBundle.bundleWithIdentifier_, bundle_id)
      if bundle is None:
        raise KeyError(
            "%r: NSBundle.bundleWithIdentifier_(%r)" % (key, bundle_id)
        )
    assert isinstance(bundle, NSBundle)
    for k in file_as:
      if k not in self:
        self[k] = bundle
    return bundle

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
  if t is objc._pythonify.OC_PythonFloat:
    return float(o)
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

