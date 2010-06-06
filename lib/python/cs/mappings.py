#!/usr/bin/python -tt

from cs.misc import the
from types import StringTypes
import sys

def isUC_(s):
  ''' Check that a string matches ^[A-Z][A-Z_0-9]*$.
  '''
  if s.isalpha() and s.isupper():
    return True
  if len(s) < 1:
    return False
  if not s[0].isupper():
    return False
  for c in s[1:]:
    if c != '_' and not c.isupper() and not c.isdigit():
      return False
  return True

def parseUC_sAttr(attr):
  ''' Take an attribute name and return (key, isplural).
      FOO returns (FOO, False).
      FOOs or FOOes returns (FOO, True).
      Otherwise return (None, False).
  '''
  if len(attr) > 1:
    if attr[-1] == 's':
      if attr[-2] == 'e':
        k=attr[:-2]
        if isUC_(k):
          return k, True
      else:
        k=attr[:-1]
        if isUC_(k):
          return k, True
  if isUC_(attr):
    return attr, False
  return None, False

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
    return '{%s}' % (", ".join([ "%s: %s" % (k, repr(value)) for k, value in kv ]))

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
        raise ValueError("invalid string %s assigned to plural attribute \"%s\"" % (repr(value), attr))
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
