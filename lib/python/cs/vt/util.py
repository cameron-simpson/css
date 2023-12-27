#!/usr/bin/env python3

''' Basic utility functions.
'''

from fcntl import flock, LOCK_EX, LOCK_UN
import os
from os import (
    SEEK_END,
    O_CREAT,
    O_EXCL,
    O_RDONLY,
    O_WRONLY,
    O_APPEND,
    O_CLOEXEC,
)
from cs.buffer import CornuCopyBuffer
from cs.pfx import Pfx
from cs.logutils import warning

def createpath(pathname):
  ''' Create the file `pathname`.
      The file must not already exist.
  '''
  with Pfx("createpath(%r)", pathname):
    os.close(os.open(pathname, O_CREAT | O_EXCL | O_WRONLY | O_CLOEXEC))

def openfd_append(pathname, create=False):
  ''' Low level OS open of `pathname` for append.

     `create`: default `False`; if true, add `O_CREAT|O_EXCL` to the open mode
  '''
  mode = O_WRONLY | O_APPEND | O_CLOEXEC
  if create:
    mode |= O_CREAT | O_EXCL
  with Pfx("os.open(%r,0o%o)", pathname, mode):
    return os.open(pathname, mode)

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
