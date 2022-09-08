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

##objc_path = '/System/Library/Frameworks/Python.framework/Versions/Current/Extras/lib/python/PyObjC'
##if objc_path not in sys.path:
##  sys.path.append(objc_path)

import objc
from AddressBook import NSDate, ABMultiValueCoreDataWrapper, NSCFDictionary, NSDateComponents
from Foundation import NSBundle

##from cs.x import X
from cs.dateutils import tzinfoHHMM
from cs.deco import fmtdoc, default_params
from cs.logutils import warning
from cs.obj import SingletonMixin
from cs.pfx import Pfx, pfx_call

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

class Bundle(SingletonMixin):
  ''' Wrapper class for an `NSBundle`.

      Instances have the following attributes:
      * `_bundle`: the underlying `NSBundle` instance
      * `_bundle_id`: the identifier of the underlying bundle, eg `'com.apple.HIServices'`
      * `_ns`: a dictionary containing all the functions from the bundle

      The functions from the bundle are available as attributes on the `Bundle`.
  '''

  _bundles = _BundlesDict()

  # additional functions for which I don't seem to have any doco
  # signatures gleaned from arbitrary sources like
  # https://gist.github.com/RhetTbull/86394ac9c2cc1096e510775dee14ae08
  _additional_functions = {
      'com.apple.CoreGraphics':
      dict(
          _CGSDefaultConnection=b"i",
          CGSCopyManagedDisplaySpaces=(
              b"^{__CFArray=}i",
              "",
              {
                  "retval": {
                      "already_retained": True
                  }
              },
          ),
          CGMainDisplayID=b"I",
          CGSGetDisplayForUUID=b"I^{__CFUUID=}",
      ),
      'com.apple.HIServices':
      dict(
          DesktopPictureCopyDisplayForSpace=
          b"^{__CFDictionary=}Ii^{__CFString=}",
          DesktopPictureSetDisplayForSpace=
          b"vI^{__CFDictionary=}ii^{__CFString=}",
      ),
  }

  @classmethod
  def _singleton_key(cls, bundle_spec):
    bundle = cls._bundles[bundle_spec]
    return bundle.bundleIdentifier()

  def __init__(self, bundle_spec):
    if '_bundle' in self.__dict__:
      return
    cls = self.__class__
    self._bundle = cls._bundles[bundle_spec]
    self._bundle_id = self._bundle.bundleIdentifier()
    self._ns = {}
    objc.loadBundle('', self._ns, bundle_identifier=self._bundle_id)
    with Pfx("%s._additional_functions", self):
      additional_functions_mapping = cls._additional_functions.get(
          self._bundle_id, {}
      )
      additional_functions = []
      for funcname, sig in additional_functions_mapping.items():
        with Pfx("%s=%r", funcname, sig):
          if isinstance(sig, tuple):
            additional_functions.append((funcname, *sig))
          elif isinstance(sig, bytes):
            additional_functions.append((funcname, sig))
          else:
            warning("skipped, not tuple or bytes")
      if additional_functions:
        pfx_call(
            objc.loadBundleFunctions, self._bundle, self._ns,
            additional_functions
        )

  def __str__(self):
    return f'{__name__}.{self.__class__.__name__}:{self._bundle_id}'

  def __dir__(self):
    return sorted(
        set(
            k for k in list(self.__dict__.keys()) + list(self._ns.keys())
            if k and not k.startswith('_')
        )
    )

  def __getattr__(self, attr):
    try:
      return self._ns[attr]
    except KeyError:
      raise AttributeError(
          f'{self.__class__.__name__}:{self._bundle_id}.{attr}'
      )

@fmtdoc
class AutoBundles:
  ''' An object whose attributes autoload `{{prefix}}{{attrname}}`.
      The default `prefix` is DEFAULT_BUNDLE_ID_PREFIX (`'{DEFAULT_BUNDLE_ID_PREFIX}'`).
  '''

  def __init__(self, prefix=None):
    if prefix is None:
      prefix = DEFAULT_BUNDLE_ID_PREFIX
    self._prefix = prefix

  def __getattr__(self, attr: str):
    if attr and attr[0].isalpha():
      return self[attr]
    raise AttributeError(f'{self.__class__.__name__}.{attr}')

  def __getitem__(self, bundle_id):
    if not bundle_id or not bundle_id[0].isalpha():
      raise KeyError(bundle_id)
    if '.' not in bundle_id:
      bundle_id = self._prefix + bundle_id
    return Bundle(bundle_id)

apple = AutoBundles()

def convertObjCtype(o):
  if o is None:
    return o
  # more specialised types first
  if isinstance(o, NSDate):
    return convertNSDate(o)
  if isinstance(o, NSDateComponents):
    return convertNSDateComponents(o)
  if isinstance(o, ABMultiValueCoreDataWrapper):
    return [convertObjCtype(o.valueAtIndex_(i)) for i in range(o.count())]
  # specific generic types later
  t = type(o)
  if t is int:
    return o
  if t is objc.pyobjc_unicode:
    return str(o)
  if t is objc._pythonify.OC_PythonInt:
    return int(o)
  if t is objc._pythonify.OC_PythonFloat:
    return float(o)
  if hasattr(o, 'keys'):
    ##warning("pretending a dict - unrecognised <%s %s>", t, repr(o).replace("\n", ""))
    return dict([(k, o[k]) for k in o.keys()])
  raise TypeError("convertObjCtype: o = %s %r", t, o)

def convertNSDate(d):
  d_date, d_time, d_zone = d.description().split()
  year, month, day = [int(d_d) for d_d in d_date.split('-')]
  hour, minute, second = [int(d_t) for d_t in d_time.split(':')]
  tz = tzinfoHHMM(d_zone)
  return datetime(year, month, day, hour, minute, second, 0, tz)

def convertNSDateComponents(d):
  desc = d.description()
  year = convertObjCtype(d.year())
  month = convertObjCtype(d.month())
  day = convertObjCtype(d.day())
  return datetime(year, month, day)

def cg(func):
  return default_params(func, cg_conn=apple.CoreGraphics._CGSDefaultConnection)

if __name__ == '__main__':
  hi_services = Bundle('HIServices')
