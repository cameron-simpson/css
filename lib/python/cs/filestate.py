#!/usr/bin/python
#
# A class for tracking file state.
#   - Cameron Simpson <cs@zip.com.au>
#

''' Facility to track file state.

    This is used to watch for size or modification time changes,
    or to notice when a file path no longer points at the same file.
'''

from collections import namedtuple
import os

_FileState = namedtuple('FileState', 'stat mtime size dev ino')
_FileState.samefile = lambda self, other: self.dev == other.dev and self.ino == other.ino

def FileState(path, do_lstat=False):
  ''' Return a signature object for a file state derived from os.stat
      (or os.lstat if `do_lstat` is true).
      `path` may also be an int, in which case os.fstat is used.
      lThis returns an object with mtime, size, dev and ino attributes
      and can be compared for equality with other signatures.
  '''
  if isinstance(path) is int:
    S = os.fstat(path)
  else:
    S = os.lstat(path) if do_lstat else os.stat(path)
  return _FileState(S, S.st_mtime, S.st_size, S.st_dev, S.st_ino)
