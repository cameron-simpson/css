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

There is also a FlaggedMixin class providing convenient methods and attributes
for maintaining a collection of flags associated with some object
with flag names prefixed by the object's .name attribute
uppercased and with an underscore appended:

      class SvcD(...,FlaggedMixin):
        def __init__(self, name, ...)
          self.name = name
          FlaggedMixin.__init__(self)
          ...
        def disable(self):
          self.flag_disable = True
        def restart(self):
          self.flag_restart = True
        def _restart(self):
          self.flag_restart = False
          ... restart the SvcD ...

so that an object set up as:

      svcd = SvcD("portfwd")
      print(svcd.flag_disable)

accesses the flag named "PORTFWD_DISABLE".
'''

from __future__ import print_function
from collections import defaultdict
from collections.abc import MutableMapping
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
from cs.pfx import Pfx

__version__ = '20201228-post'

DISTINFO = {
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': ['cs.env', 'cs.lex', 'cs.pfx'],
    'entry_points': {
        'console_scripts': ['flagset = cs.app.flag:main_flagset'],
    },
}

FLAG_USAGE = '''Usage:
  %s            Recite all flag values.
  %s flagname   Test value of named flag.
  %s flagname {0|1|false|true}
                Set value of named flag.'''

def main(argv=None):
  ''' Main program: inspect or modify flags.
  '''
  if argv is None:
    argv = sys.argv
  else:
    argv = list(argv)
  cmd = argv.pop(0)
  usage = FLAG_USAGE % (cmd, cmd, cmd)
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
        if argv:
          raise GetoptError(
              "unexpected values after key value: %s" % (' '.join(argv),)
          )
        F[k] = truthy(value)
  except GetoptError as e:
    print("%s: warning: %s" % (cmd, e), file=sys.stderr)
    badopts = True
  if badopts:
    print(usage, file=sys.stderr)
    return 2
  return xit

FLAGSET_USAGE = '''Usage: %s prefix [{set|clear}[-all]] [names...]
  prefix    Prefix of flags involved: implies flags commencing {prefix}_
  set       Set all flags whose suffixes are named on the input.
  set-all   Set all flags whose suffixes are named on the input,
            clear the remainder.
  clear     Clear all flags whose suffixes are named on the input.
  clear-all Clear all flags whose suffixes are named on the input,
            set the remainder.
  If no names are supplied, read the names from standard input.'''

# pylint: disable=too-many-locals,too-many-branches,too-many-statements
def main_flagset(argv=None, stdin=None):
  ''' Main program for "flagset" command.
  '''
  if argv is None:
    argv = sys.argv
  else:
    argv = list(argv)
  if stdin is None:
    stdin = sys.stdin
  cmd = argv.pop(0)
  usage = FLAGSET_USAGE % (cmd,)
  xit = 0
  flagdir = None
  F = Flags(flagdir=flagdir)
  badopts = False
  try:
    if not argv:
      raise GetoptError("missing prefix")
    prefix = argv.pop(0)
    if not prefix:
      raise GetoptError("invalid empty prefix")
    all_names = sorted(
        [flagname for flagname in F if flagname.startswith(prefix)]
    )
    if not argv:
      # print current flag values
      for flagname in all_names:
        print(flagname, "TRUE" if F[flagname] else "FALSE")
      return 0
    op = argv.pop(0)
    with Pfx(op):
      if op == 'set':
        value = True
        omitted = None
      elif op == 'set-all':
        value = True
        omitted = False
      elif op == 'clear':
        value = False
        omitted = None
      elif op == 'clear-all':
        value = False
        omitted = True
      else:
        raise GetoptError(
            "invalid operator, expected one of set, set-all, clear, clear-all"
        )
      updates = []
      if argv:
        for flagname in argv:
          updates.append((flagname, value))
      else:
        for lineno, line in enumerate(stdin, 1):
          with Pfx("%s:%d" % (stdin, lineno)):
            if not line.endswith('\n'):
              raise ValueError("missing newline")
            flagname = line.rstrip()
            updates.append((flagname, value))
      F.update_prefix(prefix, updates, omitted_value=omitted)
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

def truthy(value):
  ''' Decide whether a value is considered true.

      Strings are converted to:
      * `'0'`: `False`
      * `'1'`: `True`
      * `'true'`: `True` (case insensitive)
      * `'false'`: `False` (case insensitive)
      * other string values are unchanged.

      Other types are converted with `bool()`.
  '''
  if isinstance(value, str):
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
    value = bool(value)
  return value

class FlaggedMixin(object):
  ''' A mixin class adding flag_* and flagname_* attributes.

      This defines the following attributes on instances:
      * `flags`: the `Flags` instance providing the flag values.
      * `flags_prefix`: the prefix for the flags of interest.
      * `flagname_`*name*: the full name within `.flags`
        of the flag referred to as *name*.
        This is `.flags_prefix + '_' + `*name*
        if `.flags_prefix` is not empty,
        or just *name* otherwise.
     * `flag_`*name*: the value from `.flags`
        of the flag referred to as *name*.
        This is a setable attribute
        with changes propagated to `.flags`.
  '''

  def __init__(self, flags=None, debug=None, prefix=None):
    ''' Initialise the mixin.

        Parameters:
        * `flags`: optional parameter;
          if `None` defaults to a new default `Flags()`.
        * `prefix`: optional prefix;
          if not provided the prefix is derived
          from the object's `.name` attribute,
          or is empty if there is no `.name`
    '''
    if flags is None:
      flags = Flags(debug=debug)
    else:
      if debug is not None:
        flags.debug = debug
    self.flags = flags
    self.flags_prefix = prefix

  def __flagname(self, suffix):
    ''' Compute a flag name from `suffix`.

        The object's .name attribute is used as the basis, so a
        `suffix` of 'bah' with a .name attribute of 'foo' returns
        'FOO_BAH'.
    '''
    name = self.flags_prefix
    if name is None:
      name = getattr(self, 'name', None)
    if name is None:
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
    raise AttributeError("%s: no %r" % (
        type(self).__name__,
        '.' + attr,
    ))

  def __setattr__(self, attr, value):
    ''' Support .flag_suffix=value.
    '''
    if attr.startswith('flag_'):
      flagname = self.__flagname(attr[5:])
      self.flags[flagname] = value
    else:
      object.__setattr__(self, attr, value)

# factory to make a dummy flagslike object without persistent storage
DummyFlags = lambda: defaultdict(lambda: False)

# pylint: disable=too-many-ancestors
class Flags(MutableMapping, FlaggedMixin):
  ''' A mapping which directly inspects the flags directory.
  '''

  def __init__(self, flagdir=None, environ=None, lock=None, debug=None):
    ''' Initialise the `Flags` instance.

        Parameters:
        * `flagdir`: the directory holding flag state files;
          if omitted use the value from `cs.env.FLAGDIR(environ)`
        * `environ`: the environment mapping to use,
          default `os.environ`
        * `lock`: a `Lock`like mutex to control multithreaded access;
          if omitted no locking is down
        * `debug`: debug mode, default `False`
    '''
    MutableMapping.__init__(self)

    @contextmanager
    def mutex():
      ''' Mutex context manager.
      '''
      if lock:
        lock.acquire()
      try:
        yield
      finally:
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
    self._old_flags = {}

  def __repr__(self):
    return "%s(dir=%r)" % (self.__class__.__name__, self.dirpath)

  def init(self):
    ''' Ensure the flag directory exists.
    '''
    if not os.path.isdir(self.dirpath):
      with Pfx("makedirs(%r)", self.dirpath):
        os.makedirs(self.dirpath)

  def _flagpath(self, k):
    ''' Compute the pathname of the flag backing file.
        Raise `KeyError` on an invalid flag names.
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
    except OSError as e:
      value = False
      if e.errno != errno.ENOENT:
        print("os.stat(%r): %s", flagpath, e, file=sys.stderr)
    else:
      value = S.st_size > 0
    self._track(k, value)
    return value

  def __setitem__(self, k, value):
    ''' Set the flag value.

        If true, write `"1\n"` to the flag file.
        If false, truncate the flag file.
    '''
    if truthy(value):
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
          with open(flagpath, 'w') as fp:
            pass
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

  def update_prefix(self, prefix, updates, omitted_value=False):
    ''' Update all flag values commencing with `prefix`,
        falsifying any unmentioned flags.

        Parameters:
        * `prefix`: common prefix for updated flags.
        * `updates`: iterable of `(flagname,flagvalue)`.
        * `omitted_value`: value to be assigned to any unmentioned flags,
          default `False`.
          Set this to `None` to leave unmentioned flags alone.
    '''
    all_names = set(name for name in self if name.startswith(prefix))
    named = set()
    for flagname, flagvalue in updates:
      if not flagname.startswith(prefix):
        raise ValueError(
            "update flag %r does not start with prefix %r" %
            (flagname, prefix)
        )
      self[flagname] = flagvalue
      named.add(flagname)
    if omitted_value is not None:
      for flagname in all_names:
        if flagname not in named:
          self[flagname] = omitted_value

class PolledFlags(dict):
  ''' A mapping which maintains a dict of the current state of the flags directory
      and updates it regularly.

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
    ''' Monitor `self._flags` regularly, updating `self.flags`.
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
