#!/usr/bin/python

''' Convenience hash facilities.
    Portably imports the names `sha1` and `md5`.
    Provides hashfile(), to update a hash from a file's content.
'''

from __future__ import with_statement
try:
  from hashlib import sha1
except ImportError:
  from sha import sha as sha1
try:
  from hashlib import md5
except ImportError:
  from md5 import md5

_iosize = 16384

def hashfile(fp, H=None):
  ''' Return a hash of the content of the file `fp`.
      If `fp` is a str, open it as a file pathname.
      If `H` is missing or None, allocate a new SHA1 hash object.
      Returns the hash object updated from the file content.
  '''
  if type(fp) is str:
    with open(fp, "rb") as fp2:
      return hashfile(fp2, H)
  if H is None:
    H = sha1()
  while True:
    s = fp.read(_iosize)
    if len(s) == 0:
      break
    H.update(s)
  return H

def hashfiles(files, hashclass):
  ''' Generator that iterates over `files` and yields
        file, hash
      for each file, `file` being the item from `files` and `hash`
      being a hash object updated from the file content.  `file`
      may be a filename string or an open file. An open file will
      be consumed.
  '''
  for fp in files:
    return fp, hashfile(fp, hashclass())
