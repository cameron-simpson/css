#!/usr/bin/python -tt
#
# Convenience routines for web services.
#       - Cameron Simpson <cs@zip.com.au> 25mar2008
#

import sys
from ZSI import SoapWriter, ParsedSoap, TC
import ZSI.wstools.Utility
from StringIO import StringIO
import urllib2
from cs.misc import isdebug, ifdebug, debug, objFlavour, T_MAP, T_SEQ, reportElapsedTime

def lather(obj,tc=None):
  ''' Serial a python object into SOAP, return the SOAP.
      If tc is None, expect obj to have a .typecode attribute.
  '''
  if tc is None:
    tc=obj.typecode
  IO=StringIO()
  S=SoapWriter(IO)
  S.serialize(obj,tc)
  return str(S)

def rinse(soap,tc):
  ''' Turn SOAP into a python object.
  '''
  return reportElapsedTime("parse SOAP into %s object" % (tc,),
                           ParsedSoap(soap).Parse,tc)

def xml2pyobj(xml,typecode):
  return ParsedSoap(xml).Parse(typecode)

def callSOAP(url,action,xml,retAction,retTypecode):
  ''' Call the specified web services URL with an action and SOAP XML string.
      Return the parsed response, which should have the action retAction
      and be of type retTypecode.
  '''
  rq=urllib2.Request(url,xml)
  rq.add_header('Accept-Encoding', 'identity')
  rq.add_header('Soapaction', '"%s"'%action)
  rq.add_header('Content-Type', 'text/xml; charset="utf-8"')
  U=reportElapsedTime('call %s with %d bytes of XML'%(url,len(xml)),
                      urllib2.urlopen,rq)
  I=U.info()
  assert I.type == 'text/xml', \
         "%s: did not get XML back from %s:%s" % (cmd,url,action)
  retxml=''.join(U.readlines())
  ret=reportElapsedTime('decode %d bytes of %s response'%(len(retxml),retAction),
                        xml2pyobj,retxml,retTypecode)
  return ret

class BigElementProxy(ZSI.wstools.Utility.ElementProxy):
  ''' An ElementProxy with its canonicalize method
      replaced with one that uses a chunkyString.
      The ZSI default uses StringIO, which scales very badly
      to large strings; I'm constructing SOAP packets over
      10MB in size:-(
  '''
  def __init__(self, *args, **kw):
    print >>sys.stderr, "BigElementProxy.__init__(%s,%s)" % (args,kw)
    ZSI.wstools.Utility.ElementProxy.__init__(self,*args,**kw)

  def canonicalize(self):
    from cs.chunkyString import ChunkyString
    cs=ChunkyString()
    cs.write(' ')       # HACK: work around bug in ZSI.wstools.c14n
    reportElapsedTime("BigElementProxy.canonicalize()",
                      ZSI.wstools.Utility.Canonicalize,
                      self.node,output=cs)
    cs=str(cs)
    ##if isdebug: print >>sys.stderr, "BigElementProxy.canonicalize: XML=[%s]" % cs
    return cs

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
