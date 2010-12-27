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
from datetime import datetime
from cs.venti import totext, fromtext
from cs.venti.dir import Dir, decodeDirent, storeDir
from cs.venti.file import storeFile
from cs.logutils import Pfx

def archive(arfile, path, verbose):
    ''' Archive the named file path.
    '''
    with Pfx("archive(%s)" % (path,)):
      if os.path.isdir(path):
        E = storeDir(path)
      else:
        E = storeFile(open(path, "rb"))
      if arfile is None:
        writeDirent(sys.stdout, D)
      else:
        with open(arfile, "a") as fp:
          writeDirent(fp, D)

def retrieve(arfile, paths, verbose):
  ''' Retrieve Dirents for the named file paths.
  '''
  with Pfx(arfile):
    found = {}
    with open(arfile) as fp:
      for unixtime, D in getDirents(fp):
        if D.name in paths:
          found[D.name] = D
    return [ (path, found.get(path)) for path in paths ]

def getDirents(fp):
  ''' Generator to yield (unixtime, Dirent) from archive file.
  '''
  for line in fp:
    assert line.endswith('\n'), "%s: unexpected EOF" % (fp,)
    unixtime, dent = line.split(None, 1)
    when = float(unixtime)
    D, etc = decodeDirent(dent)
    assert len(etc) == 0 or etc[0].iswhite(), \
      "failed to decode dent %s: unparsed: %s" % (`dent`, `etc`)
    yield when, D

def writeDirent(fp, D, when=None):
  ''' Write a Dirent to an archive file.
  '''
  if when is None:
    when = time.time()
  fp.write(str(when))
  fp.write(' ')
  fp.write(str(D))
  fp.write('\n')
