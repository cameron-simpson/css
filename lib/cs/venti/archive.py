#!/usr/bin/python

import os
from datetime import datetime
from cs.venti import totext, fromtext
from cs.venti.dir import decodeDirent

def archive2dir(archive):
  ''' Open an archive file, return the last dirent mentioned.
      TODO: accept timestamp or something to retrieve older snapshots.
  '''
  if not os.path.exists(archive):
    return None
  assert os.path.isfile(archive)
  fp=open(archive)
  refword=None
  for line in fp:
    assert line[-1] == '\n'
    words=line[:-1].strip().split(' ')
    if len(words) == 0:
      continue
    if words[0].startswith("#"):
      continue
    refword=words[0]
  fp.close()
  if refword is None:
    return None
  E = decodeDirent(fromtext(refword), justone=True)
  if len(words) > 2 and (E.name is None or len(E.name) == 0):
    E.name=words[2]
  return E

def archiveAppend(archive,E,when=None,path=None):
  ''' Store a dirent and optional datetime and path in an archive.
      If archive is a string, treat it as a pathname to open.
      Otherwise treat it as a file.
      The optional arguments when and path will come from the dirent
      if None or not supplied.
  '''
  if when is None:
    M=E.meta
    when=M.mtime
    if when is 0:
      when=datetime.now()
  if path is None:
    path=E.name

  if type(archive) is str:
    fp=open(archive,mode='a')
  else:
    fp=archive

  if path is None:
    fp.write("%s %s\n"
             % (totext(E.encode(noname=True)), datetime.now()))
  else:
    fp.write("%s %s %s\n"
           % (totext(E.encode(noname=True)), datetime.now(), path))
  if fp is not archive:
    fp.close()
