#!/usr/bin/python

''' Convenience hashing facilities.
'''

import hashlib
import mmap
import os

from cs.buffer import CornuCopyBuffer

class _HashCode(bytes):

  __slots__ = ()

  hashfunc = lambda bs=None: None  # pylint: disable=unnecessary-lambda-assignment

  def __str__(self):
    return f'{self.hashname}:{self.hex()}'

  @property
  def hashname(self):
    ''' The hash code type name, derived from the class name.
    '''
    return self.__class__.__name__.lower()

  @classmethod
  def from_data(cls, bs):
    ''' Compute hashcode from the data `bs`.
    '''
    return cls(cls.hashfunc(bs).digest())

  @classmethod
  def from_buffer(cls, bfr):
    ''' Compute hashcode from the contents of the `CornuCopyBuffer` `bfr`.
    '''
    h = cls.hashfunc()
    for bs in bfr:
      h.update(bs)
    return cls(h.digest())

  @classmethod
  def from_fspath(cls, fspath, **kw):
    ''' Compute hashcode from the contents of the file `fspath`.
    '''
    # try to mmap the file and hash the whole thing in one go
    fd = None
    try:
      fd = os.open(fspath, os.O_RDONLY)
    except OSError:
      pass
    else:
      try:
        with mmap.mmap(fd, 0, flags=mmap.MAP_PRIVATE,
                       prot=mmap.PROT_READ) as mmapped:
          return cls.from_data(mmapped)
      except OSError:
        pass
    finally:
      if fd is not None:
        os.close(fd)
    # mmap fails, try plain open of file
    return cls.from_buffer(CornuCopyBuffer.from_filename(fspath, **kw))

class SHA256(_HashCode):
  ''' SHA256 hashcode class, subclass of `bytes`.
  '''

  __slots__ = ()
  hashfunc = hashlib.sha256
