#!/usr/bin/env python3

''' Basic utility functions.
'''

from fcntl import flock, LOCK_EX, LOCK_UN
import os
from os import (
    SEEK_END,
    O_RDONLY,
    O_WRONLY,
    O_APPEND,
    O_CLOEXEC,
)
from cs.pfx import Pfx
from cs.logutils import warning

def openfd_append(pathname):
  ''' Low level OS open of `pathname` for append.
  '''
  with Pfx("os.open(%r,O_WRONLY|O_APPEND|O_CLOEXEC)", pathname):
    return os.open(pathname, O_WRONLY | O_APPEND | O_CLOEXEC)

def openfd_read(pathname):
  ''' Low level OS open of `pathname` for read.
  '''
  with Pfx("os.open(%r,O_RDONLY|O_CLOEXEC)", pathname):
    return os.open(pathname, O_RDONLY | O_CLOEXEC)

def append_data(wfd, bs):
  ''' Append the bytes `bs` to the writable file descriptor `wfd`.

      An OS level os.flock() call is made to exclude other cooperating writers.
  '''
  try:
    flock(wfd, LOCK_EX)
  except OSError:
    is_locked = False
  else:
    is_locked = True
  offset = os.lseek(wfd, 0, SEEK_END)
  written = os.write(wfd, bs)
  # notice short writes, which should never happen with a regular file...
  while written < len(bs):
    warning(
        "fd %d: tried to write %d bytes but only wrote %d, retrying", wfd,
        len(bs), written
    )
    if written == 0:
      raise ValueError("zero length write, aborting write attempt")
    bs = bs[written:]
    written = os.write(wfd, bs)
  if is_locked:
    flock(wfd, LOCK_UN)
  return offset
