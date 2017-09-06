#!/usr/bin/python
#
# Python API for the flag(1cs) command.
#       - Cameron Simpson <cs@cskk.id.au> 24apr2014
#

r'''
Persistent filesystem based flags for state and control.

Filesystem visible boolean flags
for control and status,
allowing easy monitoring of services or other status,
and control by flag management
for programmes which also monitor the flags.

The flags are expressed as individual files with uppercase names
in a common directory ($HOME/var/flags by default);
an empty or missing file is "false"
and a nonempty file is "true".

The Flags class provides easy Pythonic access to this directory.
It presents as a modifiable mapping whose keys are the flag names:

  flags = Flags()
  flags['UNTOPPOST'] = True

The is also a FlaggedMixin class providing convenient methods and attributes
for maintaining a collection of flags associated with some object
with flag names prefixed by the object's .name attribute uppercased and with an underscore appended::

  class SvcD(...,FlaggedMixin):
    def __init__(self, name, ...)
      self.name = name
      FlaggedMixin.__init__(self)
      ...
    def disable(self):
      self.flag_disabled = True
    def restart(self):
      self.flag_restart = True
    def _restart(self):
      self.flag_restart = False
      ... restart the SvcD ...

'''

from __future__ import print_function
from collections import MutableMapping, defaultdict
from contextlib import contextmanager
import errno
from getopt import GetoptError
import os
import os.path
import sys
from threading import Thread
from time import sleep
from cs.env import FLAGDIR
from cs.lex import get_uc_identifier

DISTINFO = {
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': ['cs.env', 'cs.lex'],
}


USAGE = '''Usage:
  %s            Recite all flag values.
  %s flagname   Test value of named flag.
  %s flagname {0|1|false|true}
                Set value of named flag.'''

def main(argv):
  ''' Main program: inspect or modify flags.
  '''
  argv = list(argv)
  cmd = argv.pop(0)
  usage = USAGE % (cmd, cmd, cmd)
  xit = 0
  flagdir = None
  F = Flags(flagdir=flagdir)
  badopts = False
  try:
    if not argv:
      ks = sorted(F.keys())
      for k in ks:
        print(k, "TRUE" if F[k] else "FALSE")
    else:
      k = argv.pop(0)
      if not argv:
        xit = 0 if F[k] else 1
      else:
        value = argv.pop(0)
        if not argv:
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
              raise GetoptError(
                  "invalid key value, expected 0, 1, true or false, got: %s"
                  % (value,))
          F[k] = value
        else:
          raise GetoptError("unexpected values after key value: %s"
                            % (' '.join(argv),))
  except GetoptError as e:
    print("%s: warning: %s" % (cmd, e), file=sys.stderr)
    badopts = True
  if badopts:
    print(usage, file=sys.stderr)
    return 2
  return xit

def uppername(s):
  ''' Uppercase letters, transmute some characters to '_' or '__'.
  '''
  return s.upper().replace('-', '_').replace('.', '_').replace('/', '__')

def lowername(s):
  ''' Lowercase letters, transmute '_' to '-'. Note: NOT the reverse of uppername.
  '''
  return s.replace('_', '-').lower()

class FlaggedMixin(object):
  ''' A mixin class adding flag_* and flagname_* attributes.
  '''

  def __init__(self, flags=None, debug=None):
    ''' Initialise the mixin.
        `flags`: optional parameter; if None defaults to a new default Flags().
    '''
    if flags is None:
      flags = Flags(debug=debug)
    else:
      if debug is not None:
        flags.debug = debug
    self.flags = flags

  def __flagname(self, suffix):
    ''' Compute a flag name from `suffix`.
        The object's .name attribute is used as the basis, so a
        `suffix` of 'bah' with a .name attribute of 'foo' returns
        'FOO_BAH'.
    '''
    try:
      name = self.name
    except AttributeError:
      flagname = suffix
    else:
      flagname = name + '_' + suffix
    return uppername(flagname)

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
    raise AttributeError("FlaggedMixin: no %r" % ('.' + attr, ))

  def __setattr__(self, attr, value):
    ''' Support .flag_suffix=value.
    '''
    if attr.startswith('flag_'):
      flagname = self.__flagname(attr[5:])
      self.flags[flagname] = value
    else:
      super().__setattr__(attr, value)

# factory to make a dummy flagslike object without persistent storage
DummyFlags = lambda: defaultdict(lambda: False)

class Flags(MutableMapping, FlaggedMixin):
  ''' A mapping which directly inspects the flags directory.
  '''

  def __init__(self, flagdir=None, environ=None, lock=None, debug=None):
    MutableMapping.__init__(self)
    @contextmanager
    def mutex():
      if lock:
        lock.acquire()
      yield
      if lock:
        lock.release()
    self._mutex = mutex
    if debug is None:
      debug = False
    self.debug = debug
    FlaggedMixin.__init__(self, flags=self)
    if flagdir is None:
      flagdir = FLAGDIR(environ=environ)
    self.dirpath = flagdir
    self.debug = debug
    self._old_flags = {}

  def init(self):
    ''' Ensure the flag directory exists.
    '''
    if not os.path.isdir(self.dirpath):
      os.makedirs(self.dirpath)

  def _flagpath(self, k):
    ''' Compute the pathname of the flag backing file.
        Raise KeyError on invalid flag names.
    '''
    if not k:
      raise KeyError(k)
    name, offset = get_uc_identifier(k)
    if offset != len(k):
      raise KeyError(k)
    return os.path.join(self.dirpath, name)

  def __iter__(self):
    ''' Iterator returning the flag names in the directory.
    '''
    try:
      with self._mutex():
        listing = list(os.listdir(self.dirpath))
    except OSError as e:
      if e.errno == errno.ENOENT:
        return
      raise
    for k in listing:
      if k:
        name, offset = get_uc_identifier(k)
        if offset == len(k):
          yield name

  def __len__(self):
    ''' Return the number of flag files.
    '''
    n = 0
    for _ in self:
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
      value = False
    else:
      value = S.st_size > 0
    self._track(k, value)
    return value

  def __setitem__(self, k, truthy):
    ''' Set the flag value.
        If true, write "1\n" to the flag file.
        If false, remove the flag file.
    '''
    if truthy:
      value = True
      with self._mutex():
        if not self[k]:
          flagpath = self._flagpath(k)
          with open(flagpath, 'w') as fp:
            fp.write("1\n")
    else:
      value = False
      with self._mutex():
        if self[k]:
          flagpath = self._flagpath(k)
          try:
            os.remove(flagpath)
          except OSError as e:
            if e.errno != errno.ENOENT:
              raise
    self._track(k, value)

  def __delitem__(self, k):
    self[k] = False

  def _track(self, k, value):
    with self._mutex():
      old_value = self._old_flags.get(k, False)
      if value != old_value:
        self._old_flags[k] = value
    if value != old_value and self.debug:
      print("%s -> %d" % (k, (1 if value else 0)), file=sys.stderr)

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
    self._poll_flags()
    T = Thread(target=self._monitor_flags, kwargs={'delay': poll_interval})
    T.daemon = True
    T.start()

  def _monitor_flags(self, delay=1.1):
    ''' Monitor self._flags regularly, updating self.flags.
    '''
    while True:
      sleep(delay)
      self._poll_flags()

  def _poll_flags(self):
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

if __name__ == '__main__':
  sys.exit(main(sys.argv))
