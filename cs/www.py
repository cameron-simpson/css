import atexit
import cgi
import cgitb; ##cgitb.enable()
import os
import os.path
import re
import string
import sys
import types
import urllib
from urlparse import urljoin
import cs.hier
from cs.hier import T_SEQ, T_MAP, T_SCALAR
from cs.misc import warn

cookie_sepRe=re.compile(r'\s*;\s*')
cookie_valRe=re.compile(r'([a-z][a-z0-9_]*)=([^;,\s]*)',re.I)

hexSafeRe=re.compile(r'[-=.\w:@/?~#+&]+')
dqAttrValSafeRe=re.compile(r'[-=. \w:@/?~#+&]+')

def hexify(str,fp,safeRe=None):
  """ Percent encode a string, transcribing to a file.
      safeRe is a regexp matching a non-empty seqeunce of characters that do not need encoding.
      FIXME: percent encoding for Unicode?
  """
  if safeRe is None: safeRe=hexSafeRe
  while len(str):
    m=safeRe.match(str)
    if m:
      safetext=m.group(0)
      fp.write(safetext)
      str=str[len(safetext):]
    else:
      fp.write("%%%02x"%ord(str[0]))
      str=str[1:]

textSafeRe=re.compile(r'[^<>&]+')

def puttext(fp,str,safeRe=None):
  """ Transcribe plain text in HTML safe form.
  """
  if safeRe is None: safeRe=textSafeRe
  while len(str):
    m=safeRe.match(str)
    if m:
      safetext=m.group(0)
      fp.write(safetext)
      str=str[len(safetext):]
    else:
      if str[0] == '<':
	fp.write('&lt;')
      elif str[0] == '>':
	fp.write('&gt;')
      elif str[0] == '&':
	fp.write('&amp;')
      else:
	fp.write('&#%d;'%ord(str[0]))

      str=str[1:]

def puthtml(fp,*args):
  """ Transcribe tokens as HTML. """
  for a in args:
    puttok(fp,a)

def puttok(fp,tok):
  """ transcribe a single token as HTML. """
  global dqAttrValSafeRe
  f=cs.hier.flavour(tok)
  if f is T_SCALAR:
    # flat text
    if type(tok) is types.StringType:
      puttext(fp,tok)
    else:
      puttext(fp,`tok`)
  elif f is T_SEQ:
    # token
    if hasattr(tok,'tag'):
      # Tag class item
      tag=tok.tag
      attrs=tok.attrs
    else:
      # raw array [ tag[,attrs][,tokens...] ]
      tag=tok[0]; tok=tok[1:]
      if len(tok) > 0 and cs.hier.flavour(tok[0]) is T_MAP:
	attrs=tok[0]; tok=tok[1:]
      else:
	attrs={}

    fp.write('<')
    fp.write(tag)
    for k in attrs:
      fp.write(' ')
      fp.write(k)
      v=attrs[k]
      if v is not None:
	fp.write('="')
	hexify(str(v),fp,dqAttrValSafeRe)
	fp.write('"')
    fp.write('>')
    for t in tok:
      puttok(fp,t)
    fp.write('</')
    fp.write(tag)
    fp.write('>')
  else:
    # unexpected
    raise TypeError

def ht_form(action,method,*tokens):
  """ Make a <FORM> token, ready for content. """
  form=['FORM',{'ACTION': action, 'METHOD': method}]
  form.extend(tokens)
  warn("NEW FORM =", cs.hier.h2a(form))
  return form

class CGI:
  def __init__(self,input=None,output=None,env=None):
    if input is None: input=sys.stdin
    if output is None: output=sys.stdout
    if env is None: env=os.environ
    self.state='OPEN'
    self.input=input
    self.output=output
    self.env=env
    self.qs=None
    self.headers=[]	# HTTP headers
    self.tokens={'HEAD': [], 'BODY': []}
    if 'QUERY_STRING' in env:
      self.qs=cgi.parse_qs(env['QUERY_STRING'])

    self.path_info=None
    if 'PATH_INFO' in env:
      self.path_info=[word for word in env['PATH_INFO'].split('/') if len(word) > 0]

    self.cookies={}
    if 'HTTP_COOKIE' in self.env:
      for m in cookie_valRe.finditer(env['HTTP_COOKIE']):
	self.cookies[m.group(1)]=m.group(2)

    self.uri=None
    if 'REQUEST_URI' in env:
      self.uri=env['REQUEST_URI']

    self.script_name=None
    if 'SCRIPT_NAME' in env:
      self.script_name=env['SCRIPT_NAME']

#    atexit.register(CGI.__del__,self)
#
#  def __del__(self):
#    if self.output is not None:
#      self.close()

  def __getitem__(self,key):
    return self.env[key]

  def close(self):
    self.flush()
    self.output=None

  def flush(self):
    ##print "Content-Type: text/plain"
    ##print
    if self.headers is not None:
      ctype=self.content_type()
      self.ishtml=(ctype == 'text/html')
      self.header('Content-Type',ctype)
      for hdr in self.headers:
	self.output.write(hdr[0])
	self.output.write(': ')
	self.output.write(hdr[1])
	self.output.write('\n')
      self.output.write('\n')
      self.headers=None

    if not self.ishtml:
      for b in self.tokens['BODY']:
	self.output.write(b)
    else:
      puthtml(self.output,
	      ['!DOCTYPE',{'HTML': None,'PUBLIC': None, '-//W3C//DTD HTML 4.01 Transitional//EN': None}],
	      ['HTML',
	       ['HEAD']+self.tokens['HEAD'],
	       ['BODY']+self.tokens['BODY'],
	      ])
    self.tokens={'HEAD': [], 'BODY': []}

  def header(self,field,value,append=0):
    if not append:
      lcfield=field.lower()
      self.headers=[hdr for hdr in self.headers if hdr[0].lower() != lcfield]
    self.headers.append((field,value))

  def content_type(self,newctype=None):
    if newctype is None:
      ctype='text/html'
      for hdr in self.headers:
        if hdr[0].lower() == 'content-type':
          ctype=hdr[1].lower()
      return ctype

    self.header('Content-Type', newctype)

  def markup(self,part,*tokens):
    self.tokens[part].extend(tokens)

  def head(self,*tokens):
    self.markup('HEAD',*tokens)

  def out(self,*tokens):
    self.markup('BODY',*tokens)

  def nl(self,*tokens):
    self.out(*tokens)
    self.out('\n')

  def prepend(self,*args):
    self.body=args+self.body

class JSRPCCGI(CGI):
  def __init__(self,input=None,output=None,env=None):
    CGI.__init__(self,input=input,output=output,env=env)
    self.__seq=self.path_info.pop(0);
    self.__result={}
    self.content_type('application/x-javascript')

  def flush(self):
    from cs.lex import dict2js
    self.tokens['BODY']="csRPC_doCallback("+self.__seq+","+dict2js(self.__result)+");"
    CGI.flush(self)

  def __getitem__(self,key):
    return self.__result[key]

  def __setitem__(self,key,value):
    self.__result[key]=value

  def keys(self):
    return self.__result.keys()

# convenience classes for tags with complex substructure
class Tag:
  def __init__(self,tag,attrs=None,*tokens):
    if attrs is None: attrs={}
    self.tag=tag
    self.attrs=attrs
    self.tokens=[]
    self.tokens.extend(tokens)

  def token(self):
    return (self.tag,self.attrs,self.tokens)

  def __getitem__(self,key):
    return self.attrs[key]

  def __setitem__(self,key,value):
    self.attrs[key]=value

  def __iter__(self):
    for t in self.tokens:
      yield t

  def extend(self,tokens):
    self.tokens.extend(tokens)

class Table(Tag):
  def __init__(self,attrs={}):
    Tag.__init__(self,'TABLE',attrs)

  def TR(self,attrs=None):
    if attrs is None: attrs={}
    tr=TableTR(attrs)
    self.tokens.append(tr)
    return tr

class TableTR(Tag):
  def __init__(self,attrs=None):
    if attrs is None: attrs={}
    Tag.__init__(self,'TR',attrs)

  def TD(self,*args):
    td=Tag('TD',*args)
    self.tokens.append(td)
    return td

from htmllib import HTMLParser
from formatter import NullFormatter

class htmlparse(HTMLParser):
  def __init__(self,attr=None,tags=None):
    if attr is None: attr='href'
    if tags is None: tags=('a',)
    self.__attr=attr
    self.__tags=tags
    self.__P=HTMLParser(NullFormatter())
    self.__saved=[]
    self.__tagHandlers={}
    warn("__tags =", tags)

  def __getattr__(self,a):
    if a[:6] != "start_":
      return getattr(self.__P,a)

    tag=a[6:]
    if tag in self.__tagHandlers:
      return self.__tagHandlers[tag]

    def handler(attrs):
      if tag in self.__tags:
        for (attr,value) in attrs:
          if attr == self.__attr:
            self.__saved.append(value)

      try:
        return getattr(self.__P,a)(attrs)
      except AttributeError:
        pass

    self.__tagHandlers[tag]=handler

    return handler

  def anchor_bgn(self,href,name,type):
    if href: self.__saved.append(href)

  def getSavedValues(self,doFlush=False):
    saved=self.__saved[:]
    if doFlush: self.__saved=[]
    return saved

class URL:
  def __init__(self,url):
    self.__url=url

  def info(self):
    return urllib.urlopen(self.__url).info()

  def images(self,absolute=None,attr=None,tags=None):
    if attr is None: attr='src'
    if tags is None: tags=('img',)
    return self.links(absolute=absolute,attr=attr, tags=tags)

  def links(self,absolute=False,attr=None,tags=None):
    ''' Yield each link in the document.
    '''
    U=urllib.urlopen(self.__url)
    fullurl=U.geturl()
    I=U.info()
    if I.type == 'text/html':
      ##P=openURLParser(U)
      P=htmlparse(attr=attr,tags=tags)
      for line in U:
        P.feed(line)
        for link in P.getSavedValues(True):
          if absolute: link=urljoin(fullurl,link)
          yield link
      P.close()
      for link in P.getSavedValues(True):
        if absolute: link=urljoin(fullurl,link)
        yield link

  def open(self):
    return urllib.urlopen(self.__url)

def saveURL(url,dir=None):
  U=URL(url).open()
  filename=os.path.basename(U.geturl())
  if len(filename) == 0: filename="index.html"
  print "filename =", filename
  F=file(filename,"w")
  data=U.read(8192)
  while len(data) > 0:
    F.write(data)
    data=U.read(8192)
  F.close()

def rget(url,path):
  if len(path) == 0:
    saveURL(url)
    return

  task=path.pop(0)
  ##if task == 'src':
