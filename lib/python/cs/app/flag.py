#!/usr/bin/python
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
from threading import Thread
from time import sleep
from cs.env import FLAGDIR
from cs.lex import get_uc_identifier

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

def uppername(s):
  ''' Uppercase letters, transmute some characters to '_' or '__'.
  '''
  return s.upper().replace('-', '_').replace('.', '_').replace('/', '__')

def lowername(s):
  ''' Lowercase letters, transmute '_' to '-'. Note: NOT the reverse of uppername.
  '''
  return s.replace('_', '-').lower()

class Flags(MutableMapping):
  ''' A mapping which directly inspects the flags directory.
  '''

  def __init__(self, flagdir=None, environ=None):
    if flagdir is None:
      flagdir = FLAGDIR(environ=environ)
    self.dirpath = flagdir

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
          if e.errno != errno.ENOENT:
            raise
  
  def __delitem__(self, k):
    self[k] = False

class PolledFlags(dict):
  ''' A mapping which maintains a dict of the current state of the flags directory and updates it regularly.
      This allows an application to consult the flags very frequently
      without hammering the filesystem.
  '''

  # default sleep between flag status polling
  DEFAULT_POLL_INTERVAL = 1.1

  def __init__(self, flagdir=None, poll_interval=None):
    dict.__init__(self)
    if poll_interval is None:
      poll_interval = PolledFlags.DEFAULT_POLL_INTERVAL
    self._flags = Flags(flagdir)
    self._poll_flags(silent=True)
    T = Thread(target=self._monitor_flags, kwargs={'delay': poll_interval})
    T.daemon = True
    T.start()

  def _monitor_flags(self, delay=1.1):
    ''' Monitor self._flags regularly, updating self.flags.
    '''
    while True:
      sleep(delay)
      self._poll_flags()

  def _poll_flags(self, silent=False):
    ''' Poll the filesystem flags and update the .flags attribute.
    '''
    new_flags = dict(self._flags)
    ks = set(self.keys())
    ks.update(new_flags.keys())
    for k in sorted(ks):
      old = bool(self.get(k))
      new = bool(new_flags.get(k))
      if old ^ new:
        self[k] = new

class FlaggedMixin(object):
  ''' A mixin class adding flag_* and flagname_* attributes.
  '''

  def __init__(self, flags=None):
    ''' Initialise the mixin.
        `flags`: optional parameter; if None defaults to a new default Flags().
    '''
    if flags is None:
      flags = Flags()
    self.flags = flags

  def __flagname(self, suffix):
    ''' Compute a flag name from `suffix`.
        The object's .name attribute is used as the basis, so a
        `suffix` of 'bah' with a .name attribute of 'foo' returns
        'FOO_BAH'.
        This function returns None if there is no .name attribute or it is None.
    '''
    try:
      name = self.name
    except AttributeError:
      return None
    if name is None:
      return None
    return uppername(name + '_' + suffix)

  def __getattr__(self, attr):
    ''' Support .flag_suffix and .flagname_suffix.
    '''
    if attr.startswith('flagname_'):
      # compute the flag name
      flagname = self.__flagname(attr[9:])
      if flagname:
        return flagname
    elif attr.startswith('flag_'):
      # test a flag
      flagname = self.__flagname(attr[5:])
      if flagname:
        return self.flags[flagname]
    raise AttributeError("FlaggedMixin: no %r" % ('.'+attr,))

  def __setattr__(self, attr, value):
    ''' Support .flag_suffix=value.
    '''
    if attr.startswith('flag_'):
      self.flags[self.__flagname(attr[5:])] = value
    super().__setattr__(attr, value)

if __name__ == '__main__':
  sys.exit(main(sys.argv))
