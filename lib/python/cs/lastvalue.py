#!/usr/bin/env python3

''' A simple minded filesystem based store of the last value for some globals.
    It consists of a directory containing a text file per global,
    with the last line storing the latest value.
'''

import os
from os.path import expanduser
import sys

from cs.fs import FSPathBasedSingleton

class LastValue(FSPathBasedSingleton):

  FSPATH_DEFAULT = '~/var/log/lastvalue'
  FSPATH_ENVVAR = 'LASTVALUEDIR'

  def __getitem__(self, key: str):
    assert key and '/' not in key and not key.startswith('.')
    try:
      with open(self.pathto(key), 'r') as f:
        last = None
        for line in f:
          last = line.rstrip('\n')
        return last
    except FileNotFoundError as e:
      raise KeyError(key) from e

  def get(self, key: str, default=None):
    try:
      return self[key]
    except KeyError:
      return default

  def __setitem__(self, key, value):
    assert key and '/' not in key and not key.startswith('.')
    assert '\n' not in value
    with open(self.pathto(key), 'a', encoding='utf8') as f:
      print(value, file=f)

  def keys(self):
    return [name for name in self.listdir() if not name.startswith('.')]

  def __iter__(self):
    return iter(self.keys())

  def items(self):
    for key in self:
      yield key, self[key]

  def values(self):
    for _, value in self.items():
      yield value

def main(argv=None):
  if argv is None:
    argv = sys.argv
  cmd = argv.pop(0)
  lv = LastValue()
  if not argv:
    for key, value in sorted(lv.items()):
      print(key, value)
  else:
    key = argv.pop(0)
    if not argv:
      print(lv[key])
    else:
      value = argv.pop(0)
      if argv:
        print(f'{cmd}: extra arguments after value: {argv!r}', file=sys.stderr)
        return 2
      lv[key] = value

if __name__ == '__main__':
  sys.exit(main(sys.argv))
