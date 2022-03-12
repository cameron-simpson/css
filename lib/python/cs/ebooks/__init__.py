#!/usr/bin/env python3

''' Utilities for working with EBooks.
'''

from os.path import isabs as isabspath, join as joinpath

from icontract import require

class HasFSPath:
  ''' An object with a `.fspath` attribute representing a filesystem location.
  '''

  @require(lambda fspath: isabspath(fspath))
  def __init__(self, fspath):
    self.fspath = fspath

  def pathto(self, subpath):
    ''' The full path to `subpath`, a relative path below `self.fspath`.
    '''
    return joinpath(self.fspath, subpath)
