import os
import string
from StringIO import StringIO

def readn(fp,n):
  ''' Read n bytes from a file, coping with short reads.
  '''
  s=''
  while n > len(s):
    s2=fp.read(n-len(s))
    if len(s2) == 0:
      cmderr("readn(%s,%d): unexpected EOF after %d bytes"
             % (fp,n,len(s)))
      return None
    s+=s2
  return s

def lastline(fp):
  last=None
  for line in fp:
    last=line
  return last

def loadfile(path):
  return file(path).read()

def contlines(fp):
  ''' Generator that reads continued lines from a file.
      Continued lines have leading whitespace on following lines.
      `fp` is an iterable that yields lines, such as an open file.
  '''
  lastline = None
  for line in fp:
    if len(line) == 0:
      break
    if line.startswith(' ') or line.startswith('\t'):
      lastline += line
      continue
    if lastline is not None:
      yield lastline
    lastline = line
  if lastline is not None:
    yield lastline

def readcontline(fp):
  """ read a line which may be continued with leading whitespace """
  line=fp.readline()
  if len(line) == 0:
    return line
  assert line[-1] == '\n', "%s: unterminated line"%fp

  lines=[line]
  while True:
    oldpos=fp.tell()
    line=fp.readline()
    if len(line) == 0:
      break
    assert line[-1] == '\n', "%s: unterminated line"%fp
    if line[0] != ' ' and line[0] != '\t':
      fp.seek(oldpos)
      break
    lines.append(line)

  return "".join(lines)

class ContLineFile(file):
  """ a file object whose iterator returns contlines """
  def next(self):
    line=readcontline(self)
    if len(line) == 0:
      raise StopIteration
    return line

def pread(f,size,pos,whence=0,norestore=False):
  ''' Read a chunk of data from an arbitrary position in a file.
      Restores the file pointer after the read unless norestore is True.
  '''
  if type(f) is string:
    f=file(f)
  if not norestore:
    here=f.tell()
  f.seek(pos,whence)
  data=f.read(size)
  if not norestore:
    f.seek(here)
  return data

class BaseFileWrapper:
  """ base class for wrapping a file
      expects to be extended by IFileWrapper or OFileWrapper
  """
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

class IFileWrapper(BaseFileWrapper):
  """ base class for wrapping a file open for input """
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

class OFileWrapper(BaseFileWrapper):
  """ base class for wrapping a file open for output """
  def __init__(self,fp):
    BaseFileWrapper.__init__(self,fp)
  def truncate(self,*args):
    self.fp.truncate(*args)
  def write(self,str):
    self.fp.write(str)
  def writelines(self,seq):
    self.fp.writelines(seq)

class IndentedFile(OFileWrapper):
  """ file object with a "prevailing indent" """
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

if __name__ == '__main__':
  import unittest
  from StringIO import StringIO
  class Tests(unittest.TestCase):
    def _testContlines(self, text, lines):
      self.assertEquals( list( contlines(StringIO(text)) ), lines )

    def test00contlines(self):
      self._testContlines("", [])
      self._testContlines("line 1\nline 2\n", ["line 1\n", "line 2\n"])
      self._testContlines("line 1\n  line 1b\n", ["line 1\n  line 1b\n"])
      self._testContlines("line 0\nline 1\n  line 1b\nline 2\n", ["line 0\n", "line 1\n  line 1b\n", "line 2\n"])
  unittest.main()
