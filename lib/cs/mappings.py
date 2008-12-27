#!/usr/bin/python -tt

from cs.misc import the
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
    return str(self.__M)

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
      print >>sys.stderr, "attr=%s, k=%s, plural=%s" % (attr,k,plural)
      self.__dict__[attr]=value
      return
    if plural:
      T=tuple(value)
      if len(T) == 0 and not self.keepEmpty:
        if k in self.__M:
          del self.__M[k]
      else:
        self.__M[k]=tuple(value)
    else:
      print >>sys.stderr, "set %s=(%s,)" % (k,value)
      self.__M[k]=(value,)

  def __delattr__(self,attr):
    k, plural = parseUC_sAttr(attr)
    if k is None:
      del self.__dict__[k]
    else:
      del self.__M[k]
