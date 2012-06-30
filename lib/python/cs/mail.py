import os
import os.path
import time
import socket
import mailbox as pyMailbox
import email
from email.parser import HeaderParser, FeedParser
import email.Parser
import email.FeedParser
import string
import StringIO
import re
from contextlib import closing
from cs.logutils import OBSOLETE
from cs.misc import seq, saferename

def ismhdir(path):
  ''' Test if 'path' points at an MH directory.
  '''
  return os.path.isfile(os.path.join(path,'.mh_sequences'))

def ismaildir(path):
  ''' Test if 'path' points at a Maildir directory.
  '''
  for subdir in ('new','cur','tmp'):
    if not os.path.isdir(os.path.join(path,subdir)):
      return False
  return True

def ismbox(path):
  ''' Open path and check that its first line begins with "From ".
  '''
  fp=None
  try:
    fp=open(path)
    from_ = fp.read(5)
  except IOError:
    if fp is not None:
      fp.close()
    return False
  fp.close()
  return from_ == 'From '

def mailbox(path):
  if ismaildir(path):
    return pyMailbox.Maildir(path)
  if ismhdir(path):
    return pyMailbox.MH(path)
  return None

def messagesFromPath(path):
  if ismaildir(path):
    return messagesFromMaildir(path)
  elif ismbox(path):
    return messagesFromMBox(path)
  elif os.path.ispath(path):
    return messagesFromMailFile(path)
  else:
    raise ValueError("%s: not a recognised mail store" % (path,))

def _messageFromMailFile(mailfp):
  return HeaderParser().parse(mailfp)

def messagesFromMailFile(mailfp):
  yield _messageFromMailFile(mailfp)

def messagesFromMBox(mbox):
  ''' Generator that reads a UNIX mailbox and yields Message objects.
      TODO: do we still want readMBox()?
  '''
  needClose = False
  if type(mbox) in StringTypes:
    mbox = open(mbox)
    needClose = True

  P = None
  for line in mbox:
    assert line[-1] == '\n', "short line in %s" % (mbox,)
    if line.startswith('From '):
      if P is not None:
        yield P.close()
      P = FeedParser()
    elif P is None:
      raise ValueError("line in UNIX mailbox before first From_ line")
    else:
      P.feed(line)
  if P is not None:
    yield P.close()

  if needClose:
    mbox.close()

def messagesFromMaildir(maildir):
  ''' Generator that reads from a Maildir and yields Message header objects.
  '''
  for subdir in 'cur', 'new':
    subdirpath = os.path.join(maildir, subdir)
    for subpath in os.listdir(subdirpath):
      if subpath .startswith('.'):
        continue
      msgpath = os.path.join(subdirpath, subpath)
      if os.path.isfile(msgpath):
        with closing(open(msgpath)) as fp:
          yield _messageFromMailFile(fp)

def maildirify(path):
  ''' Make sure 'path' is a Maildir directory.
      If 'path' is missing it will be made, but not its antecedants.
  '''
  assert os.path.isdir(path) or os.mkdir(path)
  for subdir in ('new','cur','tmp'):
    dpath=os.path.join(path,subdir)
    if not os.path.isdir(dpath):
      os.mkdir(dpath)

def readMbox(path, gzipped=None):
  ''' Return a generator that reads a UNIX mailbox file and yields Messages.
      The optional argument 'gzipped' may be a boolean indicating whether
      the input is gzip compressed. If 'gzipped' is missing or None,
      readMbox() decides based on the path ending in '.gz'.
      TODO: accept a file-like object.
  '''
  if gzipped is None:
    gzipped=path.endswith('.gz')
  if gzipped:
    import gzip
    fp=gzip.open(path)
  else:
    fp=open(path)

  parser=None
  preline=fp.tell()
  for mboxline in fp:
    if mboxline.startswith('From '):
      if parser is not None:
        msg=parser.close()
        msg.set_unixfrom(from_)
        msgSize=preline-msgStart
        yield (msgStart, msgSize, msg)

      parser=email.FeedParser.FeedParser()
      from_=mboxline
      msgStart=preline
    elif parser is None:
      error(path+": skipping pre-message line:", mboxline)
    else:
      parser.feed(mboxline)

    preline=fp.tell()

  if parser is not None:
    msg=parser.close()
    msg.set_unixfrom(from_)
    msgSize=preline-msgStart
    yield (msgStart, msgSize, msg)

_delivered=0
def _nextDelivered():
  global _delivered
  _delivered+=1
  return _delivered

_MaildirInfo_RE = re.compile(r':(\d+,[^/]*)$')

class Maildir:
  def __init__(self,path):
    self.__path=path
    self.__parser=email.Parser.Parser()
    self.__hostname=None

  def mkbasename(self):
    now=time.time()
    secs=int(now)
    subsecs=now-secs

    left=str(secs)
    if self.__hostname is None:
      self.__hostname=socket.gethostname()
    right=self.__hostname.replace('/','\057').replace(':','\072')
    middle='#'+str(seq())+'M'+str(subsecs*1e6)+'P'+str(os.getpid())+'Q'+str(_nextDelivered())

    return string.join((left,middle,right),'.')

  def mkname(self,info=None):
    name=self.mkbasename()
    if info is None:
      return os.path.join('new',name)
    return os.path.join('cur',name+":"+info)

  def keys(self):
    return self.subpaths()

  def subpaths(self):
    for subdir in ('new','cur'):
      subpath=os.path.join(self.__path,subdir)
      for name in os.listdir(subpath):
        if len(name) > 0 and name[0] != '.':
          yield os.path.join(subdir,name)

  def fullpath(self,subpath):
    return os.path.join(self.__path,subpath)

  def paths(self):
    for subpath in self.subpaths():
      yield self.fullpath(subpath)

  def __iter__(self):
    for subpath in self.subpaths():
      yield self[subpath]

  def __getitem__(self,subpath):
    return self.__parser.parse(open(self.fullpath(subpath)))

  def newItem(self):
    return MaildirNewItem(self)

  def headers(self,subpath):
    fp=file(self.fullpath(subpath))
    headertext=''
    for line in fp:
      headertext+=line
      if len(line) == 0 or line == "\n":
        break

    fp=StringIO.StringIO(headertext)
    return self.__parser.parse(fp, headersonly=True)

  def importPath(self,path):
    info=None
    m=_MaildirInfo_RE.search(path)
    if m:
      info=m.group(1)

    newname=self.fullpath(self.mkname(info))
    info(path, '=>', newname)
    saferename(path,newname)

class MaildirNewItem:
  def __init__(self,maildir):
    self.__maildir=maildir
    self.__name=maildir.mkbasename()
    self.__tmpname=os.path.join('tmp',self.__name)
    self.__newname=os.path.join('new',self.__name)
    self.__fp=open(maildir.fullpath(self.__tmpname),"w")

  def write(self,s):
    self.__fp.write(s)

  def close(self):
    self.__fp.close()
    oldname=self.__maildir.fullpath(self.__tmpname)
    newname=self.__maildir.fullpath(self.__newname)
    saferename(oldname,newname)
    return newname

_maildirs={}
def openMaildir(path):
  if path not in _maildirs:
    info("open new Maildir", path)
    _maildirs[path]=Maildir(path)
  return _maildirs[path]

re_rfc2407 = re.compile( r'=\?([^?]+)\?([^?]+)\?([^?]*)\?=' )

@OBSOLETE
def unrfc2047(s):
  from cs.lex import unrfc2047 as real_unrfc2047
  return real_unrfc2047(s)
