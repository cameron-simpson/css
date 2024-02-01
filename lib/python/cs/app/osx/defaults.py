#!/usr/bin/env python3

''' Access the MacOS degfaults via the `defaults` command.
'''

from subprocess import PIPE

from cs.app.osx.plist import ingest_plist
from cs.deco import cachedmethod
from cs.lex import r
from cs.psutils import run

from typeguard import typechecked

__version__ = '20240201'

DISTINFO = {
    'keywords': ["python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.app.osx.plist',
        'cs.deco',
        'cs.lex',
        'cs.psutils',
        'typeguard',
    ],
}

def defaults(argv, *, host=None, doit=True, **subp):
  ''' Run the `defaults` command with the arguments `argv`.
      If the optional `host` parameter is supplied,
      a value of `'.'` uses the `-currentHost` option
      and other values are used with the `-host` option.
      Return the `CompletedProcess` result or `None` if `doit` is false.
  '''
  argv = list(argv)
  pre_argv = ['defaults']
  if host is not None:
    if host == '.':
      pre_argv.append('-currentHost')
    else:
      pre_argv.extend(['-host', host])
  return run(pre_argv + argv, doit=doit, **subp)

class Defaults:
  ''' A view of the defaults.
  '''

  def __init__(self, host=None):
    self.host = host

  def run(self, argv, doit=True, quiet=False) -> str:
    ''' Run a `defaults` subcommand, return the output decoded from UTF-8.
    '''
    return defaults(
        argv,
        host=self.host,
        doit=doit,
        quiet=quiet,
        stdout=PIPE,
        encoding='utf-8',
    ).stdout

  @property
  def domains(self):
    ''' Return a list of the domains present in the defaults.
    '''
    domains_s = self.run(['domains'])
    return sorted(domain.strip() for domain in domains_s.split(','))

class DomainDefaults:
  ''' A view of the defaults for a particular domain.
  '''

  def __init__(self, domain, host=None):
    self.domain = domain  # TODO: check for valid domain string
    self.defaults = Defaults(host=host)

  def __str__(self):
    return f'{self.__class__.__name__}({self.domain!r})'

  def flush(self):
    ''' Forget any cached information.
    '''
    self._as_dict = None

  @cachedmethod
  def as_dict(self):
    ''' Return the current defaults as a `dict`.
    '''
    plist = self.defaults.run(['export', self.domain, '-'])
    return ingest_plist(plist.encode('utf-8'))

  @typechecked
  def __getitem__(self, key: str):
    return self.as_dict()[key]

  def get(self, key: str, default=None):
    try:
      return self[key]
    except KeyError:
      return default

  @typechecked
  def __setitem__(self, key: str, new_value):
    try:
      old_value = self[key]
    except KeyError:
      pass
    else:
      if type(new_value) is not type(old_value):
        raise TypeError(
            "%s[%r]=%s: new value is not the same type as old value %s" %
            (self, key, r(new_value), r(old_value))
        )
    t = type(new_value)
    if t is str:
      value_args = '-string', new_value
    elif t is bytes:
      value_args = '-data', new_value.hex()
    elif t is bool:
      value_args = '-bool', str(new_value).lower()
    # _after_ bool, since bool subclasses int
    elif isinstance(t, int):
      value_args = '-int', str(int(new_value))
    elif isinstance(t, float):
      value_args = '-float', str(float(new_value))
    # TODO: date, datetime, append for arrays etc
    else:
      raise TypeError(
          "%s[%r]=%s: unsupported type" % (self, key, r(new_value))
      )
    self.defaults.run(['write', self.domain, key, *value_args])

if __name__ == '__main__':
  from pprint import pprint
  import sys
  print(Defaults().domains)
  kdefaults = DomainDefaults('com.amazon.Kindle')
  args = sys.argv[1:]
  if args:
    for k in args:
      print(k, r(kdefaults[k]))
  else:
    pprint(kdefaults.as_dict())
