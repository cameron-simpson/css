#!/usr/bin/python
#

''' Some simple MacOS plist (property list) facilities.
    Supports binary plist files, which the stdlib `plistlib` module
    does not, via the `plutil` MacOS command.
'''

import base64
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from functools import cache
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
from cs.deco import OBSOLETE, pprint_as_dict
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

@pprint_as_dict
class NSObject(Mapping):
  ''' The root of the pre-known NS* classes.
  '''

  def __str__(self):
    return 'NSObject(' + ','.join(
        f'.{attr}={value}' for attr, value in sorted(self.__dict__.items())
    ) + ')'

  def __repr__(self):
    return str(self)

  def __len__(self):
    return len(self.__dict__)

  def keys(self):
    return self.__dict__.keys()

  def __iter__(self):
    return iter(self.__dict__)

  def __getitem__(self, attr):
    return self.__dict__[attr]

class NSArray(list, NSObject):

  @classmethod
  def from_elem(cls, elem, objects=None):
    ''' Ingest a plist <array>, returning a Python list.
    '''
    if elem.tag != 'array':
      raise ValueError(f'not an <array>: {elem=}')
    return cls(map(lambda elem: elem_as_python(elem, objects=objects), elem))

class NSDictionary(dict, NSObject):
  ''' A mapping for a plist, subclassing `dict`, which also allows
      access to the elements by attribute if that does not conflict
      with a `dict` method.
  '''

  @classmethod
  def from_etree(cls, xml, objects=None) -> Self:
    ''' Recursively a plist's `ElementTree` into a native Python structure.
        This returns a `NSDictionary`, a mapping of the plists's top dict
        with attribute access to key values.
    '''
    root = xml.getroot()
    if root.tag != 'plist':
      raise ValueError(f'root Element is not a plist: {root=}')
    return cls.from_elem(root[0], objects=objects)

  @classmethod
  def from_elem(cls, dict_elem, objects=None) -> Self:
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
        value = elem_as_python(elem, objects=objects)
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
      scope = NSScope(objects=objects)
      for key, item in list(self.items()):
        self[key] = scope.resolve(item)
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

class NSScope(Sequence):
  ''' A scope for resolving object references in plists.
     It subclasses a list of reference objects and has a `.classes:dict`
     mapping of class names to class definitions.
  '''

  def __init__(self, base_classes=None, objects=None):
    super().__init__()
    self.classes = dict(base_classes or NS_BASE_CLASSES)
    self._objects = tuple(objects or ())

  def __len__(self):
    return len(self._objects)

  @cache
  def __getitem__(self, index):
    return self.resolve(self._objects[index])

  @ensure(lambda result: not isinstance(result, NSScope))
  def resolve(self, obj):
    ''' Resolve `obj` against this `Scope`.
    '''
    if isinstance(
        obj,
        (str, int, bool, float, ObjectClassDefinition, ObjectClassInstance)):
      return obj
    if isinstance(obj, list):
      return [self.resolve(item) for item in obj]
    if isinstance(obj, Mapping):
      objd = dict(obj)
      if (objid := objd.pop('CF$UID', None)) is not None:
        resolved = self[objid]
      elif (ns_string := objd.pop('NS.string', None)) is not None:
        resolved = ns_string
      elif (ns_data := objd.pop('NS.data', None)) is not None:
        resolved = ns_data
      elif (ns_locations := objd.pop('NSLocation', None)) is not None:
        ns_count = objd.pop('NSRangeCount')
        ns_length = objd.pop('NSLength')
        resolved = obj
      elif (ns_range_data := objd.pop('NSRangeData', None)) is not None:
        ns_range_count = obj['NSRangeCount']
        assert 'CF$UID' in ns_range_data
        key_id = self.resolve(ns_range_data)
        data = self[key_id]
        resolved = {'NSRangeCount': ns_range_count, 'NSRangeData': data}
      elif (data := objd.pop('data', None)) is not None:
        data = dict(data)
        assert 'CF$UID' in data
        key_id = self.resolve(data)
        resolved = self[key_id]
      elif (classname := objd.pop('$classname', None)) is not None:
        # classname
        class_mro = objd.pop('$classes')
        if objd:
          warning(f'unhandled items in obj: {objd!r}')

        mro = []
        for mro_name in class_mro:
          try:
            mro_cls = self.classes[mro_name]
          except KeyError as e:
            warning(
                f'no class named {mro_name!r} in self.classes:{sorted(self.classes.keys())}'
            )
          else:
            mro.append(mro_cls)
        if not mro:
          mro = dict,

        @pprint_as_dict
        class cls(*mro):

          def __repr__(self):
            return f'{self.__class__.__name__}:{self}'

        cls.__name__ = classname
        if classname in self.classes:
          warning(f'replacing Scope.classes[{classname=}]')
        self.classes[classname] = cls
        resolved = cls
      elif (cls_ := objd.pop('$class', None)) is not None:
        # #class instance
        cls = self.resolve(cls_)
        if (attributes := objd.pop('attributes', None)) is not None:
          # attributes
          attrs = self.resolve(attributes)
          resolved = cls()
          for key, item in attrs.items():
            setattr(resolved, key, item)
        else:
          # we expect NS.objects and maybe NS.keys
          objs = objd.pop('NS.objects')
          objs = self.resolve(objs)
          if (keys := objd.pop('NS.keys', None)) is not None:
            # objects and keys - a dict
            if len(objs) != len(keys):
              warning(
                  f'len(NS.objects)={len(objs)} != len(NS.keys)={len(keys)} for {obj=}'
              )
            keys = self.resolve(keys)
            resolved = cls(zip(keys, objs))
          else:
            # just objects = a list
            resolved = cls(objs)
      else:
        # just resolve every value in the Mapping
        resolved = {}
        for key in sorted(objd.keys()):
          item = objd.pop(key)
          resolved[key] = self.resolve(item)
      if objd:
        warning(f'uncomsumed keys {",".join(sorted(objd.keys()))} from {obj=}')
      return resolved
    warning(f'resolved value {obj.__class__.__name__}:{obj=}')
    return obj

@overload
def read_plist(plist, format: Literal['dict']) -> NSDictionary:
  ...

@overload
def read_plist(plist, format: Literal['etree']) -> etree.ElementTree:
  ...

@overload
def read_plist(plist, format: Literal['json', 'xml1']) -> str:
  ...

def read_plist(plist, format: str = 'dict'):
  ''' Load an Apple plist and return it in the specified `format`.

      Parameters:
      * `plist`: the source plist: data if `bytes`, filename if
        `str` or `os.PathLike`, otherwise a file decriptor or a
        file object open for binary read
      * `format`: the desired output format, `"dict"`, `"etree"`,
        `"json"` or `"xml"`; the default is `"dict"`

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

# pylint: disable=too-many-return-statements
def elem_as_python(elem, objects: list | None = None):
  ''' Ingest a plist `Element`, converting various types to native Python objects.
      Unhandled types remain as the original `Element`.
  '''
  if objects is None:
    objects = []
  if elem.tag in ('string', 'key'):
    return elem.text
  if elem.tag == 'integer':
    return int(elem.text)
  if elem.tag == 'real':
    return float(elem.text)
  if elem.tag == 'false':
    return False
  if elem.tag == 'true':
    return True
  if elem.tag == 'data':
    return base64.b64decode(elem.text)
  if elem.tag == 'date':
    dt = datetime.strptime(elem.text, '%Y-%m-%dT%H:%M:%SZ')
    dt = datetime.combine(dt.date(), dt.time(), timezone.utc)
    return dt
  if elem.tag == 'dict':
    pl_d = NSDictionary.from_elem(elem, objects=objects)
    return pl_d
  if elem.tag == 'array':
    return NSArray.from_elem(elem, objects=objects)
  warning(f'unhandled plist element {elem=}')
  return elem


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
