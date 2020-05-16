#!/usr/bin/env python3
#
# Python API for the lastvalue(1cs) command.
#       - Cameron Simpson <cs@cskk.id.au> 24apr2014
#

''' A filesystem based set of named values used to track various state values.
'''

import os
import os.path
import errno
from collections.abc import MutableMapping
from cs.env import envsub

DFLT_LASTVALUEDIR_SPEC = '$HOME/var/log/lastvalue'

def main(argv):
  ''' `lastvalue` main command line programme.
  '''
  argv = list(argv)
  xit = 0
  lastvaluedir = None
  LV = LastValues(lastvaluedir=lastvaluedir)
  if not argv:
    ks = sorted(LV.keys())
    for k in ks:
      print("%s: %s" % (k, LV[k]))
  else:
    k = argv.pop(0)
    if not argv:
      print(LV.get(k, ""))
    else:
      value = argv.pop(0)
      if not argv:
        LV[k] = value
      else:
        raise ValueError(
            "unexpected values after key value: %s" % (' '.join(argv),)
        )
  return xit

def lastvaluedirpath(path=None, environ=None):
  ''' Return the pathname of the lastvalue directory.
  '''
  if environ is None:
    environ = os.environ
  if path is None:
    lastvaluedir = envsub(DFLT_LASTVALUEDIR_SPEC)
  elif not os.path.isabs(path):
    lastvaluedir = os.path.join(envsub('$HOME'), path)
  else:
    lastvaluedir = path
  return lastvaluedir

class LastValues(MutableMapping):
  ''' A mapping which directly inspects the lastvalues directory.
  '''

  def __init__(self, lastvaluedir=None, environ=None):
    self.dirpath = lastvaluedirpath(lastvaluedir, environ)

  def init(self):
    ''' Ensure the lastvalue directory exists.
    '''
    if not os.path.isdir(self.dirpath):
      os.makedirs(self.dirpath)

  def _lastvaluepath(self, k):
    ''' Compute the pathname of the lastvalue backing file.
        Raise KeyError on invalid lastvalue names.
    '''
    if not k or k.startswith('.') or os.path.sep in k:
      raise KeyError(k)
    return os.path.join(self.dirpath, k)

  def __iter__(self):
    ''' Iterator returning the lastvalue names in the directory.
    '''
    try:
      listing = os.listdir(self.dirpath)
    except OSError as e:
      if e.errno == errno.ENOENT:
        return
      raise
    for k in listing:
      if k and not k.startswith('.'):
        yield k

  def __len__(self):
    ''' Return the number of lastvalue files.
    '''
    n = 0
    for _ in self:
      n += 1
    return n

  def __getitem__(self, k):
    ''' Return the last line from the lastvalue file.
    '''
    lastvaluepath = self._lastvaluepath(k)
    line = ""
    try:
      with open(lastvaluepath) as lvfp:
        for line in lvfp:
          pass
    except OSError:
      raise KeyError(k)
    if line.endswith('\n'):
      line = line[:-1]
    return line

  def __setitem__(self, k, s):
    ''' Set the lastvalue value `k` to `s`.
    '''
    if '\n' in s:
      raise ValueError("invalid lastvalue, may not contain newline: %r" % (s,))
    lastvaluepath = self._lastvaluepath(k)
    with open(lastvaluepath, "a") as lvfp:
      lvfp.write(s)
      lvfp.write('\n')

  def __delitem__(self, k):
    lastvaluepath = self._lastvaluepath(k)
    try:
      os.remove(lastvaluepath)
    except OSError as e:
      if e.errno != errno.ENOENT:
        raise
