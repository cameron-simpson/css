#!/usr/bin/python -tt

from __future__ import print_function
from collections import defaultdict, deque
from functools import partial
from threading import Lock, Thread
from cs.py3 import StringTypes
import os
import sys
from time import sleep
from cs.sharedfile import lockfile, SharedAppendLines
from cs.lex import isUC_, parseUC_sAttr
from cs.obj import O
from cs.seq import the
from cs.tail import tail

class SeqMapUC_Attrs(object):
  ''' A wrapper for a mapping from keys (matching ^[A-Z][A-Z_0-9]*$)
      to tuples. Attributes matching such a key return the first element
      of the sequence (and requires the sequence to have exactly on element).
      An attribute FOOs or FOOes (ending in a literal 's' or 'es', a plural)
      returns the sequence (FOO must be a key of the mapping).
  '''
  def __init__(self,M,keepEmpty=False):
    self.__M=M
    self.keepEmpty=keepEmpty

  def __str__(self):
    kv=[]
    for k, value in self.__M.items():
      if isUC_(k):
        if len(value) != 1:
          k=k+'s'
        else:
          value=value[0]
      kv.append((k,value))
    return '{%s}' % (", ".join([ "%s: %r" % (k, value) for k, value in kv ]))

  def __hasattr__(self,attr):
    k, plural = parseUC_sAttr(attr)
    if k is None:
      return k in self.__dict__
    return k in self.__M

  def __getattr__(self,attr):
    k, plural = parseUC_sAttr(attr)
    if k is None:
      return self.__dict__[k]
    if plural:
      if k not in self.__M:
        return ()
      return self.__M[k]
    return the(self.__M[k])

  def __setattr__(self,attr,value):
    k, plural = parseUC_sAttr(attr)
    if k is None:
      self.__dict__[attr]=value
      return
    if plural:
      if type(value) in StringTypes:
        raise ValueError("invalid string %r assigned to plural attribute %r" % (value, attr))
      T=tuple(value)
      if len(T) == 0 and not self.keepEmpty:
        if k in self.__M:
          del self.__M[k]
      else:
        self.__M[k]=T
    else:
      self.__M[k]=(value,)

  def __delattr__(self,attr):
    k, plural = parseUC_sAttr(attr)
    if k is None:
      del self.__dict__[k]
    else:
      del self.__M[k]

class UC_Sequence(list):
  ''' A tuple-of-nodes on which .ATTRs indirection can be done,
      yielding another tuple-of-nodes or tuple-of-values.
  '''
  def __init__(self, Ns):
    ''' Initialise from an iterable sequence.
    '''
    list.__init__(self, Ns)

  def __getattr__(self, attr):
    k, plural = parseUC_sAttr(attr)
    if k is None or not plural:
      return list.__getattr__(self, attr)
    values = tuple(self.__attrvals(attr))
    if len(values) > 0 and not isNode(values[0]):
      return values
    return _Nodes(values)

  def __attrvals(self, attr):
    for N in self.__nodes:
      for v in getattr(N, attr):
        yield v

class AttributableList(list):
  ''' An AttributableList maps unimplemented attributes onto the list members
      and returns you a new AttributableList with the results, ready for a
      further dereference.

      Example:

        >>> class O(object):
        >>>   def __init__(self, i):
        >>>     self.i = i
        >>> Os = [ O(1), O(2), O(3) ]
        >>> AL = AttributableList( Os )
        >>> print(AL.i)
        [1, 2, 3]
        >>> print(type(AL.i))
        <class 'cs.mappings.AttributableList'>
  '''

  def __init__(self,  initlist=None, strict=False):
    ''' Initialise the list.
        The optional parameter `initlist` initialises the list
        as for a normal list.
        The optional parameter `strict`, if true, causes list elements
        lacking the attribute to raise an AttributeError. If false,
        list elements without the attribute are omitted from the results.
    '''
    if initlist:
      list.__init__(self, initlist)
    else:
      list.__init__(self)
    self.strict = strict

  def __getattr__(self, attr):
    if self.strict:
      result = [ getattr(item, attr) for item in self ]
    else:
      result = []
      for item in self:
        try:
          r = getattr(item, attr)
        except AttributeError:
          pass
        else:
          result.append(r)
    return AttributableList(result)

class MethodicalList(AttributableList):
  ''' A MethodicalList subclasses a list and maps unimplemented attributes
      into a callable which calls the corresponding method on each list members
      and returns you a new MethodicalList with the results, ready for a
      further dereference.

      Example:
        >>> class O(object):
        ...   def x(self):
        ...     return id(self)
        ...
        >>> Os=[ O(), O(), O() ]
        >>> ML = MethodicalList( Os )
        >>> print(ML.x())
        [4300801872, 4300801936, 4300802000]
        >>> print(type(ML.x()))
        <class 'cs.mappings.MethodicalList'>
  '''

  def __init__(self,  initlist=None, strict=False):
    ''' Initialise the list.
        The optional parameter `initlist` initialises the list
        as for a normal list.
        The optional parameter `strict`, if true, causes list elements
        lacking the attribute to raise an AttributeError. If false,
        list elements without the attribute are omitted from the results.
    '''
    AttributableList.__init__(self, initlist=initlist, strict=strict)

  def __getattr__(self, attr):
    return partial(self.__call_attr, attr)

  def __call_attr(self, attr):
    if self.strict:
      submethods = [ getattr(item, attr) for item in self ]
    else:
      submethods = []
      for item in self:
        try:
          submethod = getattr(item, attr)
        except AttributeError:
          pass
        else:
          submethods.append(submethod)
    return MethodicalList( method() for method in submethods )

class FallbackDict(defaultdict):
  ''' A dictlike object that inherits from another dictlike object;
      this is a convenience subclass of defaultdict.
  '''

  def __init__(self, otherdict):
    '''
    '''
    defaultdict.__init__(self)
    self.__otherdict = otherdict

  def __missing__(self, key):
    if key not in self:
      return self.__otherdict[key]
    raise KeyError(key)

class MappingChain(object):
  ''' A mapping interface to a sequence of mappings.
      It does not support __setitem__ at present; that is expected
      to be managed via the backing mappings.
  '''

  def __init__(self, mappings=None, get_mappings=None):
    ''' Initialise the MappingChain.
        If `mappings` is not None, use it as the sequence of mappings.
	If `get_mappings` is not None, it is used as a callable to
	return the sequence of mappings.
        Exactly one of `mappings` or `get_mappings` must be specified as not
        None.
    '''
    if mappings is not None:
      if get_mappings is None:
        mappings = list(mappings)
        self.get_mappings = lambda: mappings
      else:
        raise ValueError(
                "cannot supply both mappings (%r) and get_mappings (%r)",
                mappings, get_mappings)
    else:
      if get_mappings is not None:
        self.get_mappings = get_mappings
      else:
        raise ValueError("one of mappings or get_mappings must be specified")

  def __getitem__(self, key):
    ''' Return the first value for `key` found in the mappings.
        Raise KeyError if the key in not found in any mapping.
    '''
    for mapping in self.get_mappings():
      try:
        value = mapping[key]
      except KeyError:
        continue
      return value
    raise KeyError(key)

  def get(self, key, default=None):
    try:
      return self[key]
    except KeyError:
      return default

  def __contains__(self, key):
    try:
      value = self[key]
    except KeyError:
      return False
    return True

  def keys(self):
    ''' Return the union of the keys in the mappings.
    '''
    ks = set()
    for mapping in self.get_mappings():
      ks.update(mapping.keys())
    return ks

class SeenSet(object):
  ''' A set-like collection with optional backing store file.
  '''

  def __init__(self, name, backing_path=None):
    self.name = name
    self.backing_path = backing_path
    self.set = set()
    if backing_path is not None:
      # create file if missing, also tests access permission
      with open(backing_path, "a"):
        pass
      self._backing_file = SharedAppendLines(backing_path,
                                             importer=self._add_foreign_line)
      self._backing_file.ready()

  def _add_foreign_line(self, line):
    # EOF markers, discard
    if line is None:
      return
    if not line.endswith('\n'):
      warning("%s: adding unterminated line: %s", self, line)
    s = line.rstrip()
    self.add(s, foreign=True)

  def add(self, s, foreign=False):
    # avoid needlessly extending the backing file
    if s in self.set:
      return
    self.set.add(s)
    if not foreign and self.backing_path:
      self._backing_file.put(s)

  def __contains__(self, item):
    return item in self.set

def named_column_tuples(rows):
  ''' Process an iterable of data rows, with the first row being column names.
      `rows`: an iterable of rows, each an iterable of data values.
      Yields the generated namedtuple class for the first row in
      `rows` and then instances of the class for each subsequent
      row.
      Rows may be flat iterables in the same order as the column
      names or mappings keyed on the column names.
  '''
  column_names = None
  first = True
  for row in rows:
    if first:
      column_names = row
      column_attributes = [
          re.sub('_+$', '', re.sub(r'[^\w]+', '_', name)).lower()
          for name in column_names
      ]
      # skip empty columns
      tuple_attributes = [ name for name in column_attributes if name ]
      if len(tuple_attributes) == len(column_attributes):
        tuple_attributes = column_attributes
      class NamedRow(namedtuple('NamedRow', tuple_attributes)):
        index_of = dict( (s, i) for i, s in enumerate(column_names) )
        index_of.update( (s, i) for i, s in enumerate(column_attributes) )
        def keys(self):
          return column_attributes
        def names(self):
          return column_names
        def __getitem__(self, key):
          if isinstance(key, int):
            i = key
          elif isinstance(key, str):
            i = self.index_of[key]
            if i is None:
              raise KeyError("unknown name: " + repr(key))
          else:
            raise TypeError("expected int or str, got %s" % (type(key),))
          return super().__getitem__(i)
      first = False
      yield NamedRow
    else:
      # flatten a mapping into a list ordered by column_names
      if hasattr(row, keys):
        row = [ row[k] for k in column_names ]
      if tuple_attributes is not column_attributes:
        # drop itemsfrom columns with empty names
        row = [ item for item, key in zip(row, column_attributes) if key ]
      yield NamedRow(*row)
