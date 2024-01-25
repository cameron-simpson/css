#!/usr/bin/python

''' Convenience hashing facilities.
'''

import hashlib
import mmap
import os

from cs.buffer import CornuCopyBuffer
from cs.deco import promote

class BaseHashCode(bytes):
  ''' Base class for hashcodes, subclassed by `SHA1`, `SHA256` et al.
  '''

  __slots__ = ()

  # registry of classes
  classes_by_hashname = {}

  def __init_subclass__(
      cls, *, hashfunc, hashname=None, hashclasses=None, **kw
  ):
    super().__init_subclass__(**kw)
    if hashname is None:
      hashname = cls.__name__.lower()
    if hashclasses is None:
      hashclasses = cls.classes_by_hashname
    try:
      hashcls = hashclasses[hashname]
    except KeyError:
      hashcls = None
    else:
      if hashcls is not cls:
        raise ValueError(
            f'class {hashclass} already exists for hashname {hashname!r}'
        )
    cls.hashname = hashname
    cls.hashfunc = hashfunc
    cls.hashlen = len(hashfunc(b'').digest())
    if not cls.__doc__:
      cls.__doc__ = f'{hashfunc.__name__} hashcode class, subclass of `bytes`.'
    if hashcls is None:
      hashclasses[hashname] = cls

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
  @promote
  def from_buffer(cls, bfr: CornuCopyBuffer):
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
      S = os.fstat(fd)
      if S.st_size == 0:
        return cls.from_data(b'')
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

def hashclass(hashname: str):
  ''' Return the class for the hash function named `hashname`.
  '''
  try:
    cls = BaseHashCode.classes_by_hashname[hashname]
  except KeyError:

    class cls(
        BaseHashCode,
        hashfunc=getattr(hashlib, hashname),
        hashname=hashname,
    ):
      ''' Hash class implementation.
      '''
      __slots__ = ()

  return cls

SHA1 = hashclass('sha1')
SHA256 = hashclass('sha256')
