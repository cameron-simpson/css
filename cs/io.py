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

""" file object with a "prevailing indent" """
class IndentedFile(file):
  def __init__(self,*args):
    file(*args)
    self.indent=0
    self.oldindent=[]
  def getindent(self):
    return self.indent
  def pushindent(self,i):
    append(self.oldindent,self.indent)
    self.indent=i
  def popindent(self):
    self.indent=pop(self.oldindent)
  def write(self,s):
    if self.indent == 0:
      # fast if no indenting
      file.write(self,s)
    else:
      off=0
      nl=find(s,'\n')
      while nl >= 0:
	file.write(self,s[off:nl+1])
	file.write(self,' '*self.indent)
	off=nl+1
	nl=find(s,'\n',off)
      file.write(self,s[off:])
      
