import sys
import os
import atexit
import string
import cgi
import cgitb; cgitb.enable()
import re
import types
import cs.hier
from cs.misc import extend, warn

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
    else:
      # raw array [ tag[,attrs][,tokens...] ]
      tag=tok[0]; tok=tok[1:]
      if len(tok) > 0 and cs.hier.flavour(tok[0]) is 'HASH':
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
  extend(form,tokens)
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
      self.path_info=[word for word in string.split(env['PATH_INFO'],'/') if len(word) > 0]

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
      lcfield=string.lower(field)
      self.headers=[hdr for hdr in self.headers if string.lower(hdr[0]) != lcfield]
    self.headers.append((field,value))

  def content_type(self):
    ctype='text/html'
    for hdr in self.headers:
      if string.lower(hdr[0]) == 'content-type':
	ctype=string.lower(hdr[1])
    return ctype

  def markup(self,part,*tokens):
    extend(self.tokens[part],tokens)

  def head(self,*tokens):
    self.markup('HEAD',*tokens)

  def out(self,*tokens):
    warn("CGI.out() TOKENS = ...")
    for t in tokens:
      warn(" ", `t`)
    self.markup('BODY',*tokens)

  def nl(self,*tokens):
    self.out(*tokens)
    self.out('\n')

  def prepend(self,*args):
    self.body=args+self.body

# convenience classes for tags with complex substructure
class Tag:
  def __init__(self,tag,attrs=None,*tokens):
    if attrs is None: attrs={}
    self.tag=tag
    self.attrs=attrs
    self.tokens=[]
    extend(self.tokens,tokens)

  def token(self):
    return (self.tag,self.attrs,self.tokens)

  def __getitem__(self,key):
    return self.attrs[key]

  def __setitem__(self,key,value):
    self.attrs[key]=value

  def __iter__(self):
    for t in self.tokens:
      yield t

  def extend(self,*tokens):
    extend(self.tokens,tokens)

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
