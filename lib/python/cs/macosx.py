#!/usr/bin/python
#
# MacOSX related facilities.
#       - Cameron Simpson <cs@zip.com.au> 09may2006
#

import unicodedata
import sys
if sys.hexversion < 0x02060000: from sets import Set as set

PLIST_IPHONE_SPRINGBOARD = \
        '/private/var/mobile/Library/Preferences/com.apple.springboard.plist'

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

class IPhoneIconList(list):
  ''' A model of the desktop icons.
      It is a linear list, and will produce the iPhone's nested structure
      on demand.

      The PList data structure is a list of dicts, one per desktop.
      Each dict contains an 'iconMatrix' entry which is a list of rows.
      There are five rows, the last of which is empty).
      Each row has four entries.
      Each entry is a 0 for a blank slot or a dict with an entry
      'displayIdentifier' containing an app id string.
  '''
  def __init__(self, sbprefs = None):
    list.__init__(self)
    self._byApp = {}
    if sbprefs is not None:
      iconlists = sbprefs._node_iconLists()
      for desktop in iconlists:
        assert desktop.keys() == ["iconMatrix"], \
                "unexpected desktop keys, expected just iconMatrix, got: %s" % (desktop,)
        iconMatrix = desktop["iconMatrix"]
        nrows = len(iconMatrix)
        ncols = max(*[len(row) for row in iconMatrix])
        assert ncols == 4, \
                "expected 4 columns, found %d columns: %s" % (ncols, iconMatrix)
        for row in iconMatrix:
          for slot in row:
            if slot == 0:
              icon = None
            else:
              assert slot.keys() == ["displayIdentifier"], \
                "expected just displayIdentifier, got: %s" % (slot,)
              appname = slot["displayIdentifier"]
              icon = {"displayIdentifier": appname}
            self.placeIcon(icon)
    print "icons = %s" % (self,)

  def placeIcon(self, icon, pos = None):
    ''' Place the supplied icon (which may be None).
        If pos is not supplied or None, append the icon to the list.
    '''
    if pos is None:
      pos = len(self)
    if len(self) <= pos:
      self.extend(None for i in range(pos-len(self)+1))
    assert self[pos] is None, "pos %d taken by %s" % (pos, self[pos])
    self[pos] = icon
    if icon is not None:
      self._byApp.setdefault(icon["displayIdentifier"], []).append([pos, icon])

  def placeApp(self, appname, startpos = 0, allowDupe = False):
    ''' Place the named application at the first free slot at startpos
        or beyond.
    '''
    if not allowDupe and self._byApp.get(appname, ()):
      print >>sys.stderr, \
        "placeApp(%s, %d, allowDupe = %s): app exists at: %s" \
        % (appname, startpos, allowDupe, self._byApp[appname])
    pos = startpos
    while pos < len(self) and self[pos] is not None:
      pos += 1
    self.placeIcon({"displayIdentifier": appname}, pos)

class IPhonePrefsSpringboard(object):
  def __init__(self, plist = None):
    if plist is None:
      plist = PLIST_IPHONE_SPRINGBOARD
    self.plist = plist
    self.prefs = readPlist(plist, binary=True)
    self.iconlist = IPhoneIconList(self)

  def _node_iconLists(self):
    return self.prefs["iconState"]["iconLists"]

if __name__ == "__main__":
  SB = IPhonePrefsSpringboard()
