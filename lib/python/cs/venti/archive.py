#!/usr/bin/python

''' Archive files for venti data.

    Archive files are records of data saved to a Store.
    Lines are written to the archive file of the form:

      unixtime dirent

    where unixtime is UNIX time (seconds since epoch) and dirent is the text
    encoding of a cs.venti.dir.Dirent.
'''

import os
import sys
import time
from datetime import datetime
from cs.venti import totext, fromtext
from cs.venti.dir import Dir, decodeDirent, storeDir
from cs.venti.file import storeFile
from cs.logutils import Pfx, error

def archive(arfile, path, verbose=False, fp=None,
          trust_size_mtime=False,
          keep_missing=False,
          ignore_existing=False):
  ''' Archive the named file path.
  '''
  if fp is None:
    fp = sys.stdout

  # look for existing archive for comparison
  oldtime, oldE = None, None
  try:
    arfp = open(arfile)
  except IOError:
    pass
  else:
    for unixtime, E in getDirents(arfp):
      if E.name == path and (oldtime is None or unixtime >= oldtime):
        oldtime, oldE = unixtime, E
    arfp.close()

  with Pfx("archive(%s)" % (path,)):
    if verbose:
      print >>fp, path
    if os.path.isdir(path):
      if oldE is not None and oldE.isdir:
        ok = oldE.updateFrom(path,
                     trust_size_mtime=trust_size_mtime,
                     keep_missing=keep_missing,
                     ignore_existing=ignore_existing)
        E = oldE
      else:
        E, ok = storeDir(path)
    else:
      E = storeFile(open(path, "rb"))
      ok = True
      try:
        st = os.stat(path)
      except OSError, e:
        warn("stat: %s", e)
        ok = False
      else:
        E.updateFromStat(st)

    E.name = path
    if arfile is None:
      writeDirent(sys.stdout, E)
    else:
      with open(arfile, "a") as arfp:
        writeDirent(arfp, E)
  return ok

def retrieve(arfile, paths=None):
  ''' Retrieve Dirents for the named file paths, or None if a
      path does not resolve.
      If `paths` if missing or None, retrieve the latest Dirents
      for all paths named in the archive file.
  '''
  with Pfx(arfile):
    found = {}
    with open(arfile) as arfp:
      for unixtime, E in getDirents(arfp):
        if paths is None or E.name in paths:
          found[E.name] = E
    if paths is None:
      paths = found.keys()
    return [ (path, found.get(path)) for path in paths ]

def toc_report(fp, path, E, verbose):
  print >>fp, path
  if E.isdir:
    entries = sorted(E.keys())
    for subpath in entries:
      toc_report(fp, os.path.join(path, subpath), E[subpath], verbose)

def toc(arfile, paths=None, verbose=False, fp=None):
  if fp is None:
    fp = sys.stdout
  for path, E in retrieve(arfile, paths):
    if E is None:
      error("no entry for %s", path)
    else:
      toc_report(fp, path, E, verbose)

def getDirents(fp):
  ''' Generator to yield (unixtime, Dirent) from archive file.
  '''
  for line in fp:
    assert line.endswith('\n'), "%s: unexpected EOF" % (fp,)
    unixtime, dent = line.strip().split(None, 1)
    when = float(unixtime)
    E, etc = decodeDirent(fromtext(dent))
    assert len(etc) == 0 or etc[0].iswhite(), \
      "failed to decode dent %s: unparsed: %s" % (`dent`, `etc`)
    yield when, E

def writeDirent(fp, E, when=None):
  ''' Write a Dirent to an archive file.
  '''
  if when is None:
    when = time.time()
  fp.write(str(when))
  fp.write(' ')
  fp.write(str(E))
  fp.write('\n')
