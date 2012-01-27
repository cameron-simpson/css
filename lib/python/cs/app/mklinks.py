#!/usr/bin/env python
#
# Recode of mklinks in Python, partly for the exercise and partly to
# improve the algorithm.
#       - Cameron Simpson <cs@zip.com.au> 21may2006
#

import sys
import os
import os.path
from stat import S_ISREG
import filecmp
if sys.hexversion >= 0x02050000:
  from hashlib import md5 as hashobj
else:
  from md5 import md5
  def hashobj(): return md5.new()
from cs.logutils import setup_logging, error, warn, info, debug

# amount of file to read and checksum before trying whole file compare
HASH_PREFIX_SIZE = 8192

def main(argv, stdin=None):
  argv = list(argv)
  if stdin is None:
    stdin = sys.stdin

  cmd = os.path.basename(argv.pop(0))
  setup_logging(cmd)

  if not argv:
    argv = ['-']

  for names in argv:
    if name == '-':
      if not hasattr(stdin, 'isatty') or not stdin.isatty():
        error("refusing to read filenames from a terminal")
        return 1
      lineno = 0
      for line in stdin:
        lineno += 1
        if not line.endswith('\n'):
          error("stdin:%d: unexpected EOF", lineno)
          return 1
        path = line[:-1]
        l

class FileInfo(object):

  def __init__(self, path):
    self.path = path
    self._inode = None
  
  def __getattr__(self, attr):
    if attr == 'pfx_csum':
      with open(self.path, "rb") as fp:
        csum = md5(fp.read(HASH_PREFIX_SIZE)).digest()
      self.pfx_csum = csum
      return csum
    if attr == '_lstat':
      S = os.lstat(self.path)
      self._lstat = S
      return S
    if attr == 'size':
      if not S_ISREG(self._lstat):
        size = None
      else:
        size = self._lstat.st_size
      self.size = size
      return size
    raise AttributeError("'%s' object has no attribute '%s'" % (self.__class__.__name__, attr))

  def __eq__(self, other):
    if 

sizeinfo={}
identinfo={}
pathinfo={}

noAction=False

def st_ident(st):
  return str(st[stat.ST_DEV])+":"+str(st[stat.ST_INO])

class FileInfo:
  def __init__(self,path,st):
    self.path=path
    self.paths=[]
    self.ident=st_ident(st)
    self.size=st[stat.ST_SIZE]
    self.mtime=st[stat.ST_MTIME]
    self.digestpfx=None
    self.addpath(path)
    debug("new FileInfo ident=%s, size=%s : %s", self.ident,self.size,path)

  def addpath(self,path):
    self.paths.append(path)
    pathinfo[path]=self

  def getdigestpfx(self):
    if self.digestpfx is None:
      self.digestpfx=digestpfx(self.path)
    return self.digestpfx

  def subsume(self,other):
    for path in other.paths:
      other.paths.remove(path)
      linkto(self.path,path)

    # discard mention of other
    if other in sizeinfo[self.size]: sizeinfo[self.size].remove(other)
    identinfo[other.ident]=self.path

# fetch digest hash of first 8192 bytes of the file
def digestpfx(path,size=8192):
  ##info("digest %s", path)
  fp=open(path)
  H=hashobj()
  H.update(fp.read(size))
  fp.close()
  return H.digest()

def linkto(srcpath,dstpath):
  info("%s => %s", srcpath, dstpath)
  dstdir=os.path.dirname(dstpath)
  global noAction
  if not noAction:
    tmpf=os.path.join(dstdir,tmpfilename(dstdir))
    ##assert not os.path.lexists(tmpf) # not in python 2.3 :-)
    try:
      os.lstat(tmpf)
    except OSError:
      pass
    else:
      assert False, "%s: already exists!" % tmpf
    os.link(srcpath,tmpf)
    os.rename(tmpf,dstpath)
  pathinfo[srcpath].addpath(dstpath)

def do(path):
  ''' Process each file in the directory tree.
      If a file is known by dev:ino,
        if another file is preferred,
          replace with other file
          add this path to other file list
        else
          add this path to this file list
      else
        if this is the first file of this size
          stash it, but don't open it for prefix digest
        else
          o
  '''
  ##info("DO %s", path)
  try:
    st=os.lstat(path)
  except os.OSerr, e:
    warn("%s: %s", path, e)
    return

  if not stat.S_ISREG(st[stat.ST_MODE]):
    return

  ident=st_ident(st)
  if ident in identinfo:
    # known file
    idinfo=identinfo[ident]
    if type(idinfo) is str:
      # known to be replaceable - replace
      ##debug("know to replace %s by %s", path, idinfo)
      linkto(idinfo,path)
      return

    # just note new path for this file
    debug("note new instance of",idinfo.path,":",path)
    idinfo.addpath(path)
    return

  # new file, note it
  idinfo=identinfo[ident]=FileInfo(path,st)

  size=st[stat.ST_SIZE]
  if size not in sizeinfo:
    # new size, nothing to compare to; save and return
    debug("new size",size,"on file",path)
    sizeinfo[size]=[idinfo]
    return

  # existing size - compare against files of same size
  digest=idinfo.getdigestpfx()
  for other in sizeinfo[size]:
    ##warn("cmp", path, "vs", other.path)
    if digest == other.getdigestpfx() and filecmp.cmp(path,other.path):
      # same contents
      # keep newest file
      if idinfo.mtime >= other.mtime:
        # we are newer (or as new)
        # push us to the front of the comparison list
        # discard the older file
        sizeinfo[size].insert(0,idinfo)
        idinfo.subsume(other)
        return

      # we are older - keep newer file
      ##debug("I am older, subsume %s", other.path)
      other.subsume(idinfo)
      return

  # no other file matches - add this file to the size list
  debug("no matches, size",size,"+",path)
  sizeinfo[size].append(idinfo)

def slotfile(size):
  slot=0
  while size > 0:
    size/=16
    slot+=1

  global slotfiles
  global slotpaths
  while slot >= len(slotfiles):
    slotfiles.append(None)
    slotpaths.append(None)

  ##print "slot =", slot, "len(slotfiles) =", len(slotfiles)
  if slotfiles[slot] is None:
    slotpaths[slot]=os.path.join(slotdir,str(slot))
    slotfiles[slot]=file(slotpaths[slot],'w')

  return slotfiles[slot]

def scatter(path):
  st=os.lstat(path)
  if not stat.S_ISREG(st[stat.ST_MODE]):
    return
  f=slotfile(st[stat.ST_SIZE])
  f.write(path)
  f.write('\n')

splitMode=False
slotdir=None
if sys.argv[1] == "--split":
  splitMode=True
  del sys.argv[1]
  slotfiles=[]
  slotpaths=[]
  slotdir=tmpdirn()

for path in sys.argv[1:]:
  if path == "-":
    for line in sys.stdin:
      line=chomp(line)
      if splitMode:
        scatter(line)
      else:
        do(line)
  else:
    for dirpath, dirnames, filenames in os.walk(path):
      info(dirpath)
      filenames.sort()
      for name in filenames:
        subpath=os.path.join(dirpath,name)
        if splitMode:
          scatter(subpath)
        else:
          do(subpath)
      dirnames.sort()

if splitMode:
  for slotfile in slotfiles:
    if slotfile:
      slotfile.close()

  slotpaths.reverse()   # do big files first
  for path in slotpaths:
    if path:
      info("mklinks - <%s", path)
      os.system("exec <"+path+"; rm "+path+"; exec "+sys.argv[0]+" -")

if slotdir:
  os.rmdir(slotdir)

if __name__ == '__main__':
  sys.exit(main(sys.argv))
