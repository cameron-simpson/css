#!/usr/bin/python

''' Archive files for venti data.

    Archive files are records of data saved to a Store.
    Lines are appended to the archive file of the form:

      isodatetime unixtime dirent

    where unixtime is UNIX time (seconds since epoch) and dirent is the text
    encoding of a cs.venti.dir.Dirent.
'''

from __future__ import print_function
import os
import sys
import time
from datetime import datetime
import errno
from cs.lex import unctrl
from cs.logutils import D, Pfx, error
from . import totext, fromtext
from .blockify import blockFromFile
from .dir import decode_Dirent_text, Dir
from .paths import copy_in_dir, copy_in_file

def archive(arfile, path, modes):
  ''' Archive the named file path.
      Get the last dirref from the `arfile`, if any, otherwise make a new Dir.
      Store the `path` (updating the Dir).
      Save the new dirref to `arfile`.
  '''
  ok = True
  # look for existing archive for comparison
  oldtime, oldE = None, None
  if arfile == '-':
    arfp = sys.stdin
    # refuse to use terminal as input
    if arfp.isatty():
      arfp = None
  else:
    with Pfx(arfile):
      try:
        arfp = open(arfile)
      except OSError as e:
        if e.errno == errno.ENOENT:
          arfp = None
        else:
          raise
  if arfp:
    for unixtime, E in read_Dirents(arfp):
      if E.name == path and (oldtime is None or unixtime >= oldtime):
        oldtime, oldE = unixtime, E
    if arfile != '-':
      arfp.close()
    B = E.getBlock()
    try:
      refdata = B.data
    except KeyError as e:
      warning("%s: %r: can't load data from Store, ignoring",
              arfile, B.hashcode)
      oldtime, oldE = None, None

  with Pfx("archive(%s)", path):
    if os.path.isdir(path):
      if oldE is None or not oldE.isdir:
        E = Dir(os.path.basename(path))
      else:
        E = oldE
      copy_in_dir(path, E, modes)
    elif not os.path.isfile(path):
      error("not a directory or regular file")
      return False
    else:
      E = copy_in_file(path)

    E.name = path
    if arfile is None:
      write_Dirent(sys.stdout, E)
    else:
      if arfile == '-':
        write_Dirent(sys.stdout, E)
      else:
        with open(arfile, "a") as arfp:
          write_Dirent(arfp, E)
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
    for unixtime, E in read_Dirents(arfp):
      if paths is None or E.name in paths:
        found[E.name] = E
    if arfile != '-':
      arfp.close()
    if paths is None:
      paths = found.keys()
    return [ (path, found.get(path)) for path in paths ]

def toc_report(fp, path, E, verbose):
  if verbose:
    print(path, file=fp)
  else:
    print(E.meta, path, file=fp)
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

def read_Dirents(fp):
  ''' Generator to yield (unixtime, Dirent) from archive file.
  '''
  lineno = 0
  for line in fp:
    with Pfx("%s:%d", fp, lineno):
      lineno += 1
      if not line.endswith('\n'):
        raise ValueError("incomplete? no trailing newline")
      line = line.rstrip()
      # allow optional trailing text, which will be the E.name part normally
      isodate, unixtime, dent = line.split(None, 3)[:3]
      when = float(unixtime)
      E = decode_Dirent_text(dent)
    # note: yield _outside_ Pfx
    yield when, E

def write_Dirent(fp, E, when=None):
  ''' Write a Dirent to an archive file:
        isodatetime unixtime totext(dirent) dirent.name
  '''
  if when is None:
    when = time.time()
  fp.write(datetime.fromtimestamp(when).isoformat())
  fp.write(' ')
  fp.write(str(when))
  fp.write(' ')
  fp.write(str(E.textencode()))
  fp.write(' ')
  fp.write(unctrl(E.name))
  fp.write('\n')
