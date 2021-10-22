#!/usr/bin/python
#
# A class for tracking file state.
#   - Cameron Simpson <cs@cskk.id.au>
#

''' Facility to track file state.

    This is used to watch for size or modification time changes,
    or to notice when a file path no longer points at the same file.
'''

from collections import namedtuple
import errno
import os

DISTINFO = {
    'description': "Trivial FileState class used to watch for file changes.",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
        ],
    'install_requires': [],
}

_FileState = namedtuple('FileState', 'stat mtime size dev ino')
_FileState.__eq__ = lambda self, other: other is not None and self[1:] == other[1:]

def FileState(path, do_lstat=False, missing_ok=False):
  ''' Return a signature object for a file state derived from os.stat
      (or os.lstat if `do_lstat` is true).
      `path` may also be an int, in which case os.fstat is used.
      This returns an object with mtime, size, dev and ino attributes
      and can be compared for equality with other signatures.
      `missing_ok`: return None if the target file is missing,
        otherwise raise. Default False.
  '''
  if isinstance(path, int):
    S = os.fstat(path)
  else:
    try:
      S = os.lstat(path) if do_lstat else os.stat(path)
    except OSError as e:
      if e.errno == errno.ENOENT and missing_ok:
        return None
      raise
  return _FileState(S, S.st_mtime, S.st_size, S.st_dev, S.st_ino)
