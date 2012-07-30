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
from cs.lex import unctrl
from cs.venti import totext, fromtext
from .dir import Dir, decodeDirent, storeDir
from .file import storeFilename
from cs.logutils import Pfx, error

def archive(arfile, path, verbosefp=None,
          trust_size_mtime=False,
          keep_missing=False,
          ignore_existing=False):
  ''' Archive the named file path.
  '''
  # look for existing archive for comparison
  oldtime, oldE = None, None
  if arfile == '-':
    arfp = sys.stdin
    if arfp.isatty():
      arfp = None
  else:
    try:
      arfp = open(arfile)
    except IOError:
      arfp = None
  if arfp is not None:
    for unixtime, E in getDirents(arfp):
      if E.name == path and (oldtime is None or unixtime >= oldtime):
        oldtime, oldE = unixtime, E
    if arfile != '-':
      arfp.close()

  with Pfx("archive(%s)", path):
    if os.path.isdir(path):
      if oldE is not None and oldE.isdir:
        ok = oldE.updateFrom(path,
                     trust_size_mtime=trust_size_mtime,
                     keep_missing=keep_missing,
                     ignore_existing=ignore_existing,
                     verbosefp=verbosefp)
        E = oldE
      else:
        E, ok = storeDir(path, trust_size_mtime=trust_size_mtime, verbosefp=verbosefp)
    else:
      E = storeFilename(path, path,verbosefp=verbosefp)
      ok = True

    E.name = path
    if arfile is None:
      writeDirent(sys.stdout, E)
    else:
      if arfile == '-':
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
    if arfile == '-':
      arfp = sys.stdin
      assert not arfp.isatty(), "stdin may not be a tty"
    else:
      arfp = open(arfile)
    for unixtime, E in getDirents(arfp):
      if paths is None or E.name in paths:
        found[E.name] = E
    if arfile != '-':
      arfp.close()
    if paths is None:
      paths = found.keys()
    return [ (path, found.get(path)) for path in paths ]

def toc_report(fp, path, E, verbose):
  if verbose:
    print >>fp, path
  else:
    print >>fp, E.meta, path
  if E.isdir:
    for subpath in sorted(E.keys()):
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
    isodate, unixtime, dent = line.split(None, 3)[:3]
    when = float(unixtime)
    E, etc = decodeDirent(fromtext(dent))
    assert len(etc) == 0 or etc[0].iswhite(), \
      "failed to decode dent %r: unparsed: %r" % (dent, etc)
    yield when, E

def writeDirent(fp, E, when=None):
  ''' Write a Dirent to an archive file.
  '''
  if when is None:
    when = time.time()
  fp.write(datetime.fromtimestamp(when).isoformat())
  fp.write(' ')
  fp.write(str(when))
  fp.write(' ')
  fp.write(str(E))
  fp.write(' ')
  fp.write(unctrl(E.name))
  fp.write('\n')
