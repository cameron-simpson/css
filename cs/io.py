import string
from StringIO import StringIO

""" read a line which may be continued with leading whitespace """
def readcontline(fp):
  line=fp.readline()
  if len(line) > 0:
    contlines=StringIO()

    while 1:
      oldpos=fp.tell()
      nline=fp.readline()
      if not nline: break
      if nline[0] != ' ' and nline[0] != '\t':
	fp.seek(oldpos)
	break
      contlines.write(nline)

    line=line+contlines.getvalue()
    contlines.close()

  return line

""" a file object whose iterator returns contlines """
class ContLineFile(file):
  def next(self):
    nline=readcontline(self)
    if not nline: raise StopIteration
    return nline

""" base class for wrapping a file
    expects to be extended by IFileWrapper or OFileWrapper
"""
class BaseFileWrapper:
  def __init__(self,fp):
    self.fp=fp
  def close(self):
    self.fp.close()
  def fflush(self):
    self.fp.fflush()
  def fileno(self):
    return self.fp.fileno()
  def isatty(self):
    return self.fp.isatty()
  def seek(offset,whence=0):
    self.fp.seek(whence)
  def tell(self):
    return self.fp.tell()

""" base class for wrapping a file open for input """
class IFileWrapper(BaseFileWrapper):
  def __init__(self,fp):
    BaseFileWrapper.__init(self,fp)
  def __iter__(self):
    return self.fp.__iter__()
  def next(self):
    return self.fp.next()
  def read(self,*args):
    return self.fp.read(*args)
  def readline(self,*args):
    return self.fp.readline(*args)
  def readlines(self,*args):
    return self.fp.readlines(*args)
  def xreadlines(self):
    return self.fp.xreadlines()

""" base class for wrapping a file open for output """
class OFileWrapper(BaseFileWrapper):
  def __init__(self,fp):
    BaseFileWrapper.__init__(self,fp)
  def truncate(self,*args):
    self.fp.truncate(*args)
  def write(self,str):
    self.fp.write(str)
  def writelines(self,seq):
    self.fp.writelines(seq)

""" file object with a "prevailing indent" """
class IndentedFile(OFileWrapper):
  def __init__(self,fp,i=0):
    OFileWrapper.__init__(self,fp)
    self.indent=i
    self.oldindent=[]
  def getindent(self):
    return self.indent
  def pushindent(self,i):
    self.oldindent.append(self.indent)
    self.indent=i
  def popindent(self):
    self.indent=self.oldindent.pop()
  def adjindent(self,inc):
    if self.indent is None: nindent=None
    else:                   nindent=self.indent+inc
    self.pushindent(nindent)
  def write(self,s):
    if self.indent is None or self.indent == 0:
      # fast if no indenting
      OFileWrapper.write(self,s)
    else:
      off=0
      nl=s.find('\n')
      while nl >= 0:
	OFileWrapper.write(self,s[off:nl+1])
	OFileWrapper.write(self,' '*self.indent)
	off=nl+1
	nl=s.find('\n',off)
      OFileWrapper.write(self,s[off:])
