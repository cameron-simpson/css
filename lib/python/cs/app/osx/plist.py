#!/usr/bin/python
#
# MacOSX plist facilities. Supports binary plist files, which the
# stdlib plistlib module does not.
#       - Cameron Simpson <cs@zip.com.au>
#

import os
import plistlib
import shutil
import tempfile
import cs.sh
from .iphone import is_iphone

def readPlist(path, binary=False):
  if not binary:
    return plistlib.readPlist(path)
  tfd, tpath = tempfile.mkstemp()
  os.close(tfd)
  if is_iphone():
    shutil.copyfile(path,tpath)
    plargv=('plutil',
            '-c',
            'xml1',
            tpath)
  else:
    plargv=('plutil',
            '-convert',
            'xml1',
            '-o',
            tpath,
            path)
  os.system("set -x; exec "+" ".join(cs.sh.quote(plargv)))
  pl = plistlib.readPlist(tpath)
  os.unlink(tpath)
  return pl

def writePlist(rootObj, path, binary=False):
  if not binary:
    return plistlib.writePlist(rootObj, path)
  tfd, tpath = tempfile.mkstemp()
  os.close(tfd)
  plistlib.writePlist(rootObj, tpath)
  if is_iphone():
    shutil.copyfile(path,tpath)
    plargv=('plutil',
            '-c',
            'binary1',
            tpath)
  else:
    plargv=('plutil',
            '-convert',
            'binary1',
            '-o',
            path,
            tpath)
  os.system("set -x; exec "+" ".join(cs.sh.quote(plargv)))
  if is_iphone():
    shutil.copyfile(tpath,path)
  os.unlink(tpath)
