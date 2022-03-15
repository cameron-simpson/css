#!/usr/bin/env python3

''' Utilities for working with EBooks.
'''

import os
from os.path import (
    expanduser,
    isabs as isabspath,
    join as joinpath,
    realpath,
)
from threading import Lock
from typing import Optional

from icontract import require
from typeguard import typechecked

from cs.obj import SingletonMixin

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

class FSPathBasedSingleton(SingletonMixin, HasFSPath):

  @classmethod
  def _get_default_fspath(cls):
    ''' Obtain the default filesystem path.
    '''
    fspath = os.environ.get(cls.FSPATH_ENVVAR)
    if fspath is None:
      fspath = expanduser(cls.FSPATH_DEFAULT)
    return fspath

  @classmethod
  def _singleton_key(cls, fspath=None):
    ''' Each instance is identified by `realpath(fspath)`.
    '''
    if fspath is None:
      fspath = cls._get_default_fspath()
    return realpath(fspath)

  @typechecked
  def __init__(self, fspath: Optional[str] = None):
    if hasattr(self, '_lock'):
      return
    if fspath is None:
      fspath = self._get_default_fspath()
    HasFSPath.__init__(self, fspath)
    self._lock = Lock()
