#!/usr/bin/env python3

''' Access the MacOS degfaults via the `defaults` command.
'''

from subprocess import PIPE
from typing import List

from cs.deco import cachedmethod
from cs.psutils import run

from .plist import ingest_plist

from cs.x import X
from pprint import pprint, pformat

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

  def run(self, argv, doit=True) -> str:
    ''' Run a `defaults` subcommand, return the output decoded from UTF-8.
    '''
    return defaults(
        argv, host=self.host, doit=doit, stdout=PIPE
    ).stdout.decode('utf-8')

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

if __name__ == '__main__':
  print(Defaults().domains)
  pprint(DomainDefaults('com.amazon.Kindle').as_dict())
