#!/usr/bin/python -tt
#
# Convenience routines for web services.
#       - Cameron Simpson <cs@zip.com.au> 25mar2008
#

import sys
from ZSI import SoapWriter, ParsedSoap, TC
from StringIO import StringIO
from cs.misc import ifdebug, debug, objFlavour, T_MAP, T_SEQ

def lather(obj,tc=None):
  ''' Serial a python object into SOAP, return the SOAP.
      If tc is None, expect obj to have a .typecode attribute.
  '''
  if tc is None:
    tc=obj.typecode
  IO=StringIO()
  S=SoapWriter(IO)
  debug("S.serialze=%s" % S.serialize)
  S.serialize(obj,tc)
  xml=str(S)
  if ifdebug():
    debug("===========")
    print >>sys.stderr, xml
  return xml

def rinse(soap,tc):
  ''' Turn SOAP into a python object.
  '''
  return ParsedSoap(soap).Parse(tc)

def autoObjectify(O):
  ''' Recurse down an object, return a transformation of it with
      interior nodes as AutoObjects.
  '''
  if isinstance(O,AutoObject):
    return O
  T=objFlavour(O)
  if T is T_MAP:
    return AutoObject(O)
  if T is T_SEQ:
    return [ autoObjectify(e) for e in O ]
  return O

autoObjectOff=False

def withoutAutoObject(func,*args,**kw):
  global autoObjectOff
  oAOO=autoObjectOff
  autoObjectOff=True
  ret=func(*args,**kw)
  autoObjectOff=oAOO
  return ret

class AutoObject(object):
  ''' A class with auto-instantiating _foo fields for
      use as ZSI SOAP object instantiators.
  '''
  def __init__(self,D=None,Dkeys=None):
    if D is not None:
      if Dkeys is None:
        Dkeys=D.keys()
      for k in Dkeys:
        setattr(self,'_'+k,D[k])
  def __setattr__(self,attr,value):
    global autoObjectOff
    if not autoObjectOff \
    and attr.startswith('_') \
    and not attr.startswith('__'):
        value=autoObjectify(value)
    self.__dict__[attr]=value
  def __getattr__(self,attr):
    global autoObjectOff
    if not autoObjectOff \
    and attr.startswith('_') \
    and not attr.startswith('__'):
        O=AutoObject()
        self.__dict__[attr]=O
        return O
    raise AttributeError, "AutoObject has no attribute '%s'" % attr
  def __repr__(self):
    return "AO(%s)" % self
  def printAO(self,name):
    for s in str(self).split("\n"):
      print "%s.%s" % (name, s)
  def __str__(self):
    ks=self.__dict__.keys()
    ks.sort()
    strs=[]
    for k in ks:
      v=getattr(self,k)
      if isinstance(v,AutoObject):
        sep='.'
      else:
        sep='='
      s=str(getattr(self,k))
      strs.append("\n".join( "%s%s%s" % (k,sep,ss) for ss in s.split("\n") ))
    return "\n".join(strs)
