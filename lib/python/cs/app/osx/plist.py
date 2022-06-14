#!/usr/bin/python
#

''' Some simple MacOS plist facilities.
    Supports binary plist files, which the stdlib `plistlib` module does not.
'''

import base64
from datetime import datetime, timezone
import os
import plistlib
import shutil
import subprocess
import tempfile

from cs.logutils import warning
from cs.pfx import Pfx
import cs.sh
from cs.xml import etree

__version__ = '20220606-post'

DISTINFO = {
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.logutils',
        'cs.pfx',
        'cs.sh',
        'cs.xml',
    ],
}

def is_iphone():
  ''' Test if we're on an iPhone.
  '''
  return os.uname()[4].startswith('iPhone')

def import_as_etree(plist):
  ''' Load an Apple plist and return an etree.Element.

      Paramaters:
      * `plist`: the source plist: data if `bytes`, filename if `str`,
        otherwise a file object open for binary read.
  '''
  if isinstance(plist, bytes):
    # read bytes as a data stream
    # write to temp file, decode using plutil
    with tempfile.NamedTemporaryFile() as tfp:
      tfp.write(plist)
      tfp.flush()
      tfp.seek(0, 0)
      return import_as_etree(tfp.file)
  if isinstance(plist, str):
    # presume plist is a filename
    with open(plist, "rb") as pfp:
      return import_as_etree(pfp)
  # presume plist is a file
  with subprocess.Popen(['plutil', '-convert', 'xml1', '-o', '-', '-'],
                        stdin=plist, stdout=subprocess.PIPE) as P:
    E = etree.parse(P.stdout)
    retcode = P.wait()
  if retcode != 0:
    raise ValueError(
        "export_xml_as_plist(E=%s,...): plutil exited with returncode=%s" %
        (E, retcode)
    )
  return E

def ingest_plist(plist, recurse=False, resolve=False):
  ''' Ingest an Apple plist and return as a `PListDict`.
      Trivial wrapper for `import_as_etree` and `ingest_plist_etree`.

      Parameters:
      * `recurse`: unpack any `bytes` objects as plists
      * `resolve`: resolve unpacked `bytes` plists' `'$objects'` entries
  '''
  if resolve and not recurse:
    raise ValueError("resolve true but recurse false")
  with Pfx("ingest_plist(%r)", plist):
    pd = ingest_plist_etree(import_as_etree(plist))
    if recurse:
      for key, value in list(pd.items()):
        with Pfx("[%r]", key):
          if isinstance(value, bytes):
            subpd = ingest_plist(value)
            if resolve:
              subpd = resolve_object(subpd['$objects'], 1)
            pd[key] = subpd
    return pd

def export_xml_to_plist(E, fp=None, fmt='binary1'):
  ''' Export the content of an `etree.Element` to a plist file.

      Parameters:
      * `E`: the source `etree.Element`.
      * `fp`: the output file or filename (if a str).
      * `fmt`: the output format, default `"binary1"`.
        The format must be a valid value for the `-convert` option of plutil(1).
  '''
  if isinstance(fp, str):
    with open(fp, "wb") as ofp:
      return export_xml_to_plist(E, ofp, fmt=fmt)
  with subprocess.Popen(['plutil', '-convert', fmt, '-o', '-', '-'],
                        stdin=subprocess.PIPE, stdout=fp) as P:
    P.stdin.write(etree.tostring(E))
    P.stdin.close()
    retcode = P.wait()
  if retcode != 0:
    raise ValueError(
        "export_xml_as_plist(E=%s,...): plutil exited with returncode=%s" %
        (E, retcode)
    )
  return None

def ingest_plist_etree(plist_etree):
  ''' Recursively a plist's `ElementTree` into a native Python structure.
      This returns a `PListDict`, a mapping of the plists's top dict
      with attribute access to key values.
  '''
  root = plist_etree.getroot()
  if root.tag != 'plist':
    raise ValueError("%r root Element is not a plist" % (root,))
  return ingest_plist_dict(root[0])

# pylint: disable=too-many-return-statements
def ingest_plist_elem(e):
  ''' Ingest a plist `Element`, converting various types to native Python objects.
      Unhandled types remain as the original `Element`.
  '''
  if e.tag == 'dict':
    return ingest_plist_dict(e)
  if e.tag == 'array':
    return ingest_plist_array(e)
  if e.tag in ('string', 'key'):
    return e.text
  if e.tag == 'integer':
    return int(e.text)
  if e.tag == 'real':
    return float(e.text)
  if e.tag == 'false':
    return False
  if e.tag == 'true':
    return True
  if e.tag == 'data':
    return base64.b64decode(e.text)
  if e.tag == 'date':
    dt = datetime.strptime(e.text, '%Y-%m-%dT%H:%M:%SZ')
    dt = datetime.combine(dt.date(), dt.time(), timezone.utc)
    return dt
  return e

def ingest_plist_array(pa):
  ''' Ingest a plist <array>, returning a Python list.
  '''
  if pa.tag != 'array':
    raise ValueError("not an <array>: %r" % (pa,))
  a = []
  for i, e in enumerate(pa):
    e = pa[i]
    a.append(ingest_plist_elem(e))
  return a

def ingest_plist_dict(pd):
  ''' Ingest a plist <dict> Element, returning a PListDict.
  '''
  if pd.tag != 'dict':
    raise ValueError("not a <dict>: %r" % (pd,))
  d = PListDict()
  for i, e in enumerate(pd):
    if i % 2 == 0:
      if e.tag == 'key':
        key = e.text
      else:
        raise ValueError("unexpected key element %r" % (e,))
    else:
      value = ingest_plist_elem(e)
      d[key] = value
      key = None
  if key is not None:
    raise ValueError("no value for key %r" % (key,))
  return d

class PListDict(dict):
  ''' A mapping for a plist, subclassing `dict`, which also allows
      access to the elements by attribute if that does not conflict
      with a `dict` method.
  '''

  def __getattr__(self, attr):
    if attr[0].isalpha():
      try:
        return self[attr]
      except KeyError:
        # pylint: disable=raise-missing-from
        raise AttributeError(attr)
    raise AttributeError(attr)

  def __setattr__(self, attr, value):
    if attr in self:
      self[attr] = value
    else:
      super().__setattr__(attr, value)

# pylint: disable=too-many-statements.too-many-branches,too-many-nested-blocks,too-many-locals
def resolve_object(objs, i):
  ''' Resolve an object definition from structures like an iPhoto album
      queryData object list.
  '''
  o = objs[i]
  if isinstance(
      o, (str, int, bool, float, ObjectClassDefinition, ObjectClassInstance)):
    return o
  if isinstance(o, PListDict):
    if '$class' in o:
      # instance definition
      class_id = o.pop('$class')['CF$UID']
      class_def = resolve_object(objs, class_id)
      if 'NS.string' in o:
        # NS.string => flat string
        value = o.pop('NS.string')
      elif 'NS.objects' in o:
        objects = []
        for od in o.pop('NS.objects'):
          obj_id = od.pop('CF$UID')
          if od:
            raise ValueError("other fields in obj ref: %r" % (od,))
          objects.append(resolve_object(objs, obj_id))
        if 'NS.keys' in o:
          keys = []
          for kd in o.pop('NS.keys'):
            key_id = kd.pop('CF$UID')
            if kd:
              raise ValueError("other fields in key ref: %r" % (kd,))
            key = resolve_object(objs, key_id)
            keys.append(key)
          value = dict(zip(keys, objects))
        else:
          value = list(objects)
      elif 'NS.data' in o:
        value = o['NS.data']
      elif 'NSLocation' in o:
        count = o['NSRangeCount']
        length = o['NSLength']
        location = o['NSLocation']
        value = o
      elif 'NSRangeData' in o:
        count = o['NSRangeCount']
        data = o['NSRangeData']
        key_id = data.pop('CF$UID')
        if data:
          raise ValueError("other fields in NSRangeData: %r" % (data,))
        data = resolve_object(objs, key_id)
        value = {'NSRangeCount': count, 'NSRangeData': data}
      elif 'data' in o:
        data = o['data']
        key_id = data.pop('CF$UID')
        if data:
          raise ValueError("other fields in data: %r" % (data,))
        data = resolve_object(objs, key_id)
        o['data'] = data
        value = data
      else:
        warning("unhandled $class instance: %r", o)
      o = ObjectClassInstance(class_def, value)
    elif '$classname' in o:
      class_name = o.pop('$classname')
      class_mro = o.pop('$classes')
      o = ObjectClassDefinition(class_name, class_mro)
    else:
      warning("unknown dict content: %r" % (o,))
  else:
    warning("unsupported value %r" % (o,))
  objs[i] = o
  return o

# pylint: disable=too-few-public-methods
class ObjectClassDefinition(object):
  ''' A representation of a "class" object, used in `resolve_object()`
      for otherwise unrecognised objects which contain a `$classname` member.
  '''

  def __init__(self, name, mro):
    self.name = name
    self.mro = mro

  def __str__(self):
    return "%s%r" % (self.name, self.mro)

  __repr__ = __str__

class ObjectClassInstance(object):
  ''' A representation of a "class instance", used in `resolve_object()`
      for objects with a `$class` member.
  '''

  def __init__(self, class_def, value):
    self.class_def = class_def
    self.value = value

  @property
  def name(self):
    ''' The name from teh class definiton.
    '''
    return self.class_def.name

  def __str__(self):
    return "%s%r" % (self.name, self.value)

  __repr__ = __str__

  def __len__(self):
    return len(self.value)

  def __getitem__(self, key):
    return self.value[key]

  def __setitem__(self, key, value):
    self.value[key] = value

  def __contains__(self, key):
    return key in self.value

  def __getattr__(self, attr):
    try:
      return self.value[attr]
    except KeyError:
      # pylint: disable=raise-missing-from
      raise AttributeError("not in self.value: %r" % (attr,))
    except TypeError:
      # pylint: disable=raise-missing-from
      raise AttributeError("cannot index self.value: %r" % (attr,))
    raise AttributeError(attr)

  # pylint: disable=missing-function-docstring
  def keys(self):
    return self.value.keys()

  # pylint: disable=missing-function-docstring
  def items(self):
    return self.value.items()

  # pylint: disable=missing-function-docstring
  def values(self):
    return self.value.values()

####################################################################################
# Old routines written for use inside my jailbroken iPhone.
#

def readPlist(path, binary=False):
  ''' An old routine I made to use inside my jailbroken iPhone.
  '''
  if not binary:
    return plistlib.readPlist(path)
  tfd, tpath = tempfile.mkstemp()
  os.close(tfd)
  if is_iphone():
    shutil.copyfile(path, tpath)
    plargv = ('plutil', '-c', 'xml1', tpath)
  else:
    plargv = ('plutil', '-convert', 'xml1', '-o', tpath, path)
  os.system("set -x; exec " + " ".join(cs.sh.quote(plargv)))
  pl = plistlib.readPlist(tpath)
  os.unlink(tpath)
  return pl

def writePlist(rootObj, path, binary=False):
  ''' An old routine I made to use inside my jailbroken iPhone.
  '''
  if not binary:
    return plistlib.writePlist(rootObj, path)
  tfd, tpath = tempfile.mkstemp()
  os.close(tfd)
  plistlib.writePlist(rootObj, tpath)
  if is_iphone():
    shutil.copyfile(path, tpath)
    plargv = ('plutil', '-c', 'binary1', tpath)
  else:
    plargv = ('plutil', '-convert', 'binary1', '-o', path, tpath)
  os.system("set -x; exec " + " ".join(cs.sh.quote(plargv)))
  if is_iphone():
    shutil.copyfile(tpath, path)
  return None
