#!/usr/bin/python
#
# MacOSX related facilities.
#       - Cameron Simpson <cs@zip.com.au> 09may2006
#

import unicodedata

PLIST_IPHONE_SPRINGBOARD = \
        '/private/var/mobile/Library/Preferences/com.apple.springboard.plist'

#############################################################################
# Functions to handle general filenames on the basis that they are a sequence
# of (by default) Latin-1 bytes, and recode them as a Normal Form D UTF-8
# sequence of bytes.
# Such names will still be valid on a UNIX filesystem, and be accepted by
# a MacOSX HFS filesystem, which rejects attempts to make filenames with
# invalid NFD UTF8 byte sequence names.
#

def nfd(name,srcencoding='iso8859-1'):
  ''' Convert a name from another encoding (default ISO8859-1, Latin-1)
      into MacOSX HFS friendly UTF-8 Normal Form D.
  '''
  uf=unicode(name,srcencoding)          # get Unicode version
  nfduf=unicodedata.normalize('NFD',uf) # transform to Normal Form D
  utf8f=nfduf.encode('utf8')            # transcribe to UTF-8
  return utf8f

def is_iphone():
  import os
  return os.uname()[4].startswith('iPhone')

def readPlist(path, binary=False):
  import plistlib
  if not binary:
    return plistlib.readPlist(path)
  import tempfile
  import cs.sh
  import os
  tfd, tpath = tempfile.mkstemp()
  os.close(tfd)
  if is_iphone():
    import shutil
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
  import plistlib
  if not binary:
    return plistlib.writePlist(rootObj, path)
  import tempfile
  import cs.sh
  import os
  tfd, tpath = tempfile.mkstemp()
  os.close(tfd)
  plistlib.writePlist(rootObj, tpath)
  if is_iphone():
    import shutil
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

def iphone_desktop_icons():
  # list of dicts, one per desktop
  # each dict contains an 'iconMatrix' entry which is a list of rows
  # there are five rows, the last of which is empty
  # each row has four entries
  # each entry is a 0 for a blank slot
  # or a dict with an entry 'displayIdentifier' containing an app id string
  return readPlist(PLIST_IPHONE_SPRINGBOARD, binary=True) \
         ["iconState"]["iconLists"]

if __name__ == "__main__":
  pass
