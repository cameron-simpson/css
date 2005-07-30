import sys
import os
import atexit
import string
import cgi
import cgitb; cgitb.enable()
import re
import types
import cs.hier

cookie_sepRe=re.compile(r'\s*;\s*')
cookie_valRe=re.compile(r'([a-z][a-z0-9_]*)=([^;,\s]*)',re.I)

hexSafeRe=re.compile(r'[-=.\w:@/?~#+&]+')

def hexify(str,fp,safeRe=hexSafeRe):
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

def puttext(fp,str,safeRe=textSafeRe):
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
  for a in args:
    puttok(fp,a)

def puttok(fp,tok):
  f=cs.hier.flavour(tok)
  if f is 'SCALAR':
    # flat text
    if type(tok) is types.StringType:
      puttext(fp,tok)
    else:
      puttext(fp,`tok`)
  elif f is 'ARRAY':
    # token
    if hasattr(tok,'tag'):
      # Tag class item
      tag=tok.tag
      attrs=tok.attrs
    else
      # raw array [ tag[,attrs][,tokens...] ]
      tag=tok[0]; tok[:1]=()
      if len(tok) > 0 and cs.hier.flavour(tok[0]) is 'HASH':
	attrs=tok[0]; tok[:1]=()

    fp.write('<')
    fp.write(tag)
    for k in attrs:
      fp.write(' ')
      fp.write(k)
      v=attrs[k]
      if v is not None:
	fp.write('="')
	hexify(v,fp)
	fp.write('"')
    fp.write('>')
    for subtok in tok:
      puttok(fp,subtok)
    fp.write('</')
    fp.write(tag)
    fp.write('>')
  else:
    # unexpected
    raise TypeError

def ht_form(action,method,*toks):
  return [FORM,{'ACTION': action, 'METHOD': method},toks]

class CGI:
  def __init__(self,input=sys.stdin,output=sys.stdout,env=os.environ):
    self.state='OPEN'
    self.input=input
    self.output=output
    self.env=env
    self.qs=None
    self.headers=[]	# HTTP headers
    self.head=[]	# HTML <HEAD> section
    self.body=[]	# HTML <BODY> section
    if 'QUERY_STRING' in env:
      self.qs=cgi.parse_qs(env['QUERY_STRING'])

    self.path_info=None
    if 'PATH_INFO' in env:
      self.path_info=[word for word in split(env['PATH_INFO'],'/') if len(word) > 0]

    self.cookies={}
    if 'HTTP_COOKIE' in self.env:
      for m in cookie_valRe.finditer(env['HTTP_COOKIE']):
	self.cookies[m.group(1)]=m.group(2)

    self.script_name=None
    if 'SCRIPT_NAME' in env:
      self.script_name=env['SCRIPT_NAME']

    atexit.register(CGI.__del__,self)

  def __del__(self):
    if self.output is not None:
      self.close()

  def close(self):
    self.flush()
    self.output=None

  def flush(self):
    if self.header is not None:
      ctype=self.content_type()
      self.ishtml=(ctype is 'text/html')
      self.header('Content-Type',ctype)
      for hdr in self.headers:
	self.output.write(hdr[0])
	self.output.write(': ')
	self.output.write(hdr[1])
	self.output.write('\n')
      self.output.write('\n')
      self.header=None

    if not self.ishtml:
      for b in self.body:
	self.output.write(b)
    else:
      puthtml(self.output,
	      ['!DOCTYPE','HTML','PUBLIC', '-//W3C//DTD HTML 4.01 Transitional//EN'],
	      ['HTML',
	       ['HEAD']+self.head,
	       ['BODY']+self.body
	      ])
    self.head=[]
    self.body=[]

  def header(self,field,value,append=0):
    if not append:
      lcfield=string.lower(field)
      self.header=[hdr for hdr in self.header if string.lower(hdr[0]) != lcfield]
    self.header.append((field,value))

  def content_type(self):
    ctype='text/html'
    for hdr in self.header:
      if string.lower(hdr[0]) == 'content-type':
	ctype=string.lower(hdr[1])
    return ctype

  def head(self,*tokens):
    self.head=self.head+tokens

  def out(self,*tokens)
    self.body=self.body+tokens

  def nl(self,*tokens):
    self.out(*tokens)
    self.out('\n')

  def prepend(self,*args)
    self.body=args+self.body

# convenience classes for tags with complex substructure
class Tag:
  def __init__(self,tag,attrs={},*tokens):
    self.tag=tag
    self.attrs=attrs
    self.tokens=*tokens

  def token(self):
    return (self.tag,self.attrs,self.tokens)

  def __getattr__(self,key):
    return self.attrs[key]

  def __setattr__(self,key,value):
    self.attrs[key]=value

  def __iter__(self):
    return self.tokens

  def append(self,*tokens)
    self.tokens=self.tokens+*tokens

class Table(Tag):
  def __init__(self,attrs={}):
    Tag.__init__(self,'TABLE',attrs)

  def TR(self,attrs={}):
    tr=Tag('TR',attrs)
    tokens.append(tr)
    return tr

class TR(Tag):
  def __init__(self,attrs={}):
    Tag.__init__(self,'TR',attrs)

  def TD(self,*args):
    td=Tag('TD',*args)
    self.tokens.appen(td)
    return td
