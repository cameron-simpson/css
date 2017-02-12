#!/usr/bin/python
#
# MacOSX plist facilities. Supports binary plist files, which the
# stdlib plistlib module does not.
#       - Cameron Simpson <cs@zip.com.au>
#

import os
import plistlib
import shutil
import subprocess
import tempfile
import cs.sh
from cs.xml import etree
from .iphone import is_iphone

def import_as_etree(plist):
  ''' Load an Apple plist and return an etree.Element.
      `plist`: the source plist: data if bytes, filename if str,
          otherwise a file object open for binary read.
  '''
  if isinstance(plist, bytes):
    # read bytes as a data stream
    # write to temp file, decode using plutil
    with tempfile.NamedTemporaryFile() as tfp:
      tfp.write(plist)
      tfp.flush()
      tfp.seek(0, 0)
      return import_as_etree(tfp.file)
  if isinstance(plist, str):
    # presume plist is a filename
    with open(plist, "rb") as pfp:
      return import_as_etree(pfp)
  # presume plist is a file
  P = subprocess.Popen(['plutil', '-convert', 'xml1', '-o', '-', '-'],
                       stdin=plist,
                       stdout=subprocess.PIPE)
  E = etree.parse(P.stdout)
  retcode = P.wait()
  if retcode != 0:
    raise ValueError("export_xml_as_plist(E=%s,...): plutil exited with returncode=%s" % (E, retcode))
  return E

def export_xml_to_plist(E, fp=None, fmt='binary1'):
  ''' Export the content of an etree.Element to a plist file.
      `E`: the source etree.Element.
      `fp`: the output file or filename (if a str).
      `fmt`: the output format, default "binary1". The format must
              be a valid value for the "-convert" option of plutil(1).
  '''
  if isinstance(fp, str):
    with open(fp, "wb") as ofp:
      return export_xml_as_plist(E, ofp, fmt=fmt)
  P = subprocess.Popen(['plutil', '-convert', fmt, '-o', '-', '-'],
                       stdin=subprocess.PIPE,
                       stdout=fp)
  P.stdin.write(etree.tostring(E))
  P.stdin.close()
  retcode = P.wait()
  if retcode != 0:
    raise ValueError("export_xml_as_plist(E=%s,...): plutil exited with returncode=%s" % (E, retcode))

####################################################################################
# Old routines written for use inside my jailbroken iPhone.
#

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
