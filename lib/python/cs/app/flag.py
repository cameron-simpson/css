#!/usr/bin/sh
#
# Python API for the flag(1cs) command.
#       - Cameron Simpson <cs@zip.com.au> 24apr2014
#

from __future__ import print_function
import sys
import os
import os.path
import errno
from collections import MutableMapping
from cs.env import envsub
from cs.lex import get_uc_identifier

DFLT_FLAGDIR_SPEC = '$HOME/var/flags'

def main(argv):
  argv = list(argv)
  cmd = argv.pop(0)
  xit = 0
  flagdir = None
  F = Flags(flagdir=flagdir)
  if len(argv) == 0:
    ks = sorted(F.keys())
    for k in ks:
      print(k, "TRUE" if F[k] else "FALSE")
  else:
    k = argv.pop(0)
    if argv == 0:
      xit = 0 if F[k] else 1
    else:
      value = argv.pop(0)
      if len(argv) == 0:
        if value == '0':
          value = False
        elif value == '1':
          value = True
        else:
          value = value.lower()
          if value == 'false':
            value = False
          elif value == 'true':
            value = True
          else:
            raise ValueError("invalid key value, expected 0, 1, true or false, got: %s", value)
        F[k] = value
      else:
        raise ValueError("unexpected values after key value: %s" % (' '.join(argv),))
  return xit

def flagdirpath(path=None, environ=None):
  ''' Return the pathname of the flags directory.
  '''
  if environ is None:
    environ = os.environ
  if path is None:
    flagdir = environ.get('FLAGDIR')
    if flagdir is None:
      flagdir = envsub(DFLT_FLAGDIR_SPEC)
  elif not os.path.isabs(path):
    flagdir = os.path.join(envsub('$HOME'), path)
  else:
    flagdir = path
  return flagdir

class Flags(MutableMapping):

  def __init__(self, flagdir=None, environ=None):
    self.dirpath = flagdirpath(flagdir, environ)

  def init(self):
    ''' Ensure the flag directory exists.
    '''
    if not os.path.isdir(self.dirpath):
      os.makedirs(self.dirpath)

  def _flagpath(self, k):
    ''' Compute the pathname of the flag backing file.
        Raise KeyError on invalid flag names.
    '''
    if len(k) == 0:
      raise KeyError(k)
    name, offset = get_uc_identifier(k)
    if offset != len(k):
      raise KeyError(k)
    return os.path.join(self.dirpath, k)

  def __iter__(self):
    ''' Iterator returning the flag names in the directory.
    '''
    try:
      listing = os.listdir(self.dirpath)
    except OSError as e:
      if e.errno == errno.ENOENT:
        return
      raise
    for k in listing:
      if len(k) > 0:
        name, offset = get_uc_identifier(k)
        if offset == len(k):
          yield name

  def __len__(self):
    ''' Return the number of flag files.
    '''
    n = 0
    for k in self:
      n += 1
    return n

  def __getitem__(self, k):
    ''' Return the truthiness of this flag.
        True means a non-empty file exists.
    '''
    flagpath = self._flagpath(k)
    try:
      S = os.stat(flagpath)
    except OSError:
      return False
    return S.st_size > 0

  def __setitem__(self, k, truthy):
    ''' Set the flag value.
        If true, write "1\n" to the flag file.
        If false, remove the flag file.
    '''
    if truthy:
      if not self[k]:
        flagpath = self._flagpath(k)
        with open(flagpath, 'w') as fp:
          fp.write("1\n")
    else:
      if self[k]:
        flagpath = self._flagpath(k)
        try:
          os.remove(k)
        except OSError as e:
          if e.errno != ENOENT:
            raise
  
  def __delitem__(self, k):
    self[k] = False

if __name__ == '__main__':
  sys.exit(main(sys.argv))
