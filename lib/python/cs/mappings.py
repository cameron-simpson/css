#!/usr/bin/python -tt

from cs.lex import isUC_, parseUC_sAttr
from cs.misc import the
from collections import defaultdict
from functools import partial
from types import StringTypes
import sys

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
        >>> print AL.i
        [1, 2, 3]
        >>> print type(AL.i)
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
        >>> print ML.x()
        [4300801872, 4300801936, 4300802000]
        >>> print type(ML.x())
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
