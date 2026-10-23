#!/usr/bin/python
#

''' Some simple MacOS plist (property list) facilities.
    Supports binary plist files, which the stdlib `plistlib` module
    does not, via the `plutil` MacOS command.
'''

import base64
from datetime import datetime, timezone
from io import StringIO
import json
import os
from os import PathLike
import plistlib
import shutil
import subprocess
import tempfile
from typing import overload, Literal, Self

from cs.buffer import CornuCopyBuffer
from cs.deco import OBSOLETE
from cs.gimmicks import warning
from cs.pfx import Pfx
import cs.sh

try:
  from lxml import etree
except ImportError:
  import xml.etree.ElementTree as etree

__version__ = '20231129-post'

DISTINFO = {
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.buffer',
        'cs.gimmicks',
        'cs.pfx',
        'cs.sh',
    ],
    'requires_python':
    # 3.6 for for os.PathLike
    # 3.8 for typing.Literal
    # 3.11 for typing.Self
    '>=3.11',
}

class NSObject:
  ''' The root of the pre-known NS* classes.
  '''

class NSArray(list, NSObject):

  @classmethod
  def from_elem(cls, elem):
    ''' Ingest a plist <array>, returning a Python list.
    '''
    if elem.tag != 'array':
      raise ValueError(f'not an <array>: {elem=}')
    return cls(map(elem_as_python, elem))

class NSDictionary(dict, NSObject):
  ''' A mapping for a plist, subclassing `dict`, which also allows
      access to the elements by attribute if that does not conflict
      with a `dict` method.
  '''

  @classmethod
  def from_etree(cls, xml) -> Self:
    ''' Recursively a plist's `ElementTree` into a native Python structure.
        This returns a `NSDictionary`, a mapping of the plists's top dict
        with attribute access to key values.
    '''
    root = xml.getroot()
    if root.tag != 'plist':
      raise ValueError(f'root Element is not a plist: {root=}')
    return cls.from_elem(root[0])

  @classmethod
  def from_elem(cls, dict_elem) -> Self:
    ''' Ingest a plist <dict> Element, returning a `NSDictionary`.
    '''
    if dict_elem.tag != 'dict':
      raise ValueError("not a <dict>: %r" % (dict_elem,))
    self = cls()
    key = None
    for i, elem in enumerate(dict_elem):
      if i % 2 == 0:
        if elem.tag == 'key':
          key = elem.text
        else:
          raise ValueError(f'not a <key>: {elem=}')
      else:
        value = elem_as_python(elem)
        self[key] = value
        key = None
    if key is not None:
      raise ValueError(f'no value for {key=}')
    try:
      objects = self['$objects']
    except KeyError:
      pass
    else:
      printt(f'{cls.__name__} PRE RESOLVE', self)
      for i, object in enumerate(objects):
        resolve_object(objects, i)
      printt(f'{cls.__name__} POST RESOLVE', self)
    return self

  # make existing keys available as attribute
  def __getattr__(self, attr):
    if attr[0].isalpha():
      try:
        return self[attr]
      except KeyError as e:
        # pylint: disable=raise-missing-from
        raise AttributeError(attr) from e
    raise AttributeError(attr)

  def __setattr__(self, attr, value):
    if attr in self:
      self[attr] = value
    else:
      super().__setattr__(attr, value)

# a mapping of the predefined names to their classes
NS_BASE_CLASSES = {
    cls.__name__: cls
    for cls in (NSArray, NSDictionary, NSObject)
}

@overload
def read_plist(plist, format: Literal['dict']) -> NSDictionary:
  ...

@overload
def read_plist(plist, format: Literal['etree']) -> etree.ElementTree:
  ...

@overload
def read_plist(plist, format: Literal['json', 'xml1']) -> str:
  ...

def read_plist(plist, format: str):
  ''' Load an Apple plist and return it as a `str` in the specified
      `format`.

      Parameters:
      * `plist`: the source plist: data if `bytes`, filename if
        `str` or `os.PathLike`, otherwise a file decriptor or a
        file object open for binary read
      * `format`: the desired output format, `"dict"`, `"etree"`, `"json"` or `"xml"`

      Each `format` has its own expected return value:
      * `"dict"`: a `NSDictionary` decoded from the plist
      * `"etree"`: an `ElementTree` XML object
      * `"json"`: a `str` in JSON format
      * `"xml1"`: a `str` in the plist `xml1` XML format
  '''
  if format == 'dict':
    xml = read_plist(plist, 'etree')
    return NSDictionary.from_etree(xml)
  if format == 'etree':
    xml_s: str = read_plist(plist, 'xml')
    if xml_s.startswith('<?xml '):
      close_pos = xml_s.find('?>')
      assert close_pos >= 6
      xml_s = xml_s[close_pos + 2:]
    return etree.parse(StringIO(xml_s))
  if format == 'json':
    pl_convert = 'json'
  elif format in ('xml', 'xml1'):
    pl_convert = 'xml1'
  else:
    raise ValueError(f'unsupported {format=}')
  if isinstance(plist, bytes):
    pfd = CornuCopyBuffer([plist]).as_fd()
    return read_plist(pfd, format=format)
  if isinstance(plist, (str, PathLike)):
    # presume plist is a filename
    with open(plist, "rb") as pfp:
      return read_plist(pfp, format=format)
  # presume plist is a file or file descriptor
  pl_argv = ['plutil', '-convert', pl_convert, '-o', '-', '-']
  with subprocess.Popen(pl_argv, stdin=plist, stdout=subprocess.PIPE) as P:
    plist_bs = P.stdout.read()
  retcode = P.wait()
  if retcode != 0:
    raise ValueError(f'nonzero exit code {retcode} from {pl_argv=}')
  return plist_bs.decode('utf-8')

@OBSOLETE('read_plist(..,"etree")')
def import_as_etree(plist):
  ''' Load an Apple plist and return an `etree.Element`.
      Parameters:
      * `plist`: the source plist: data if `bytes`, filename if
        `str` or `os.PathLike`, otherwise a file descriptor or a
        file object open for binary read.
  '''
  return read_plist(plist, 'etree')

def ingest_plist(plist, recurse=False, resolve=False):
  ''' Ingest an Apple plist and return as a `NSDictionary`.
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

@OBSOLETE('NSDictionary.from_etree')
def ingest_plist_etree(plist_etree) -> NSDictionary:
  ''' Recursively a plist's `ElementTree` into a native Python structure.
      This returns a `NSDictionary`, a mapping of the plists's top dict
      with attribute access to key values.
  '''
  return NSDictionary.from_etree(plist_etree)

@OBSOLETE('NSDictionary.from_elem')
def ingest_plist_dict(e):
  return NSDictionary.from_elem(e)

# pylint: disable=too-many-return-statements
def elem_as_python(elem, objects: list | None = None):
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

@OBSOLETE('elem_as_python')
def ingest_plist_elem(elem, objects: list | None = None):
  return elem_as_python(elem, objects)

# pylint: disable=too-many-statements.too-many-branches,too-many-nested-blocks,too-many-locals
def resolve_object(objs, i):
  ''' Resolve an object definition from structures like an iPhoto album
      queryData object list.
  '''
  o = objs[i]
  if isinstance(
      o, (str, int, bool, float, ObjectClassDefinition, ObjectClassInstance)):
    return o
  if isinstance(o, NSDictionary):
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
class ObjectClassDefinition:
  ''' A representation of a "class" object, used in `resolve_object()`
      for otherwise unrecognised objects which contain a `$classname` member.
  '''

  def __init__(self, name, mro):
    self.name = name
    self.mro = mro

  def __str__(self):
    return f'{self.__class__.__name__}:{self.name}({",".join(self.mro)})'
    ##return "%s%r" % (self.name, self.mro)

  __repr__ = __str__

class ObjectClassInstance:
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
    return f'{self.__class__.__name__}:{self.name}:{self.value!r}'
    ##return "%s%r" % (self.name, self.value)

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

def is_iphone():
  ''' Test if we're on an iPhone.
  '''
  return os.uname()[4].startswith('iPhone')

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

if __name__ == '__main__':
  pass
