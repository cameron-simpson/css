#!/usr/bin/python

''' Convenience hashing facilities.
'''

import hashlib

from cs.buffer import CornuCopyBuffer
from cs.lex import hexify

class _HashCode(bytes):

  __slots__ = ()
  hashfunc = None

  def __str__(self):
    return f'{type(self).__name__.lower()}:{hexify(self)}'

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
  def from_pathname(cls, pathname, **kw):
    ''' Compute hashcode from the contents of the file `pathname`.
    '''
    return cls.from_buffer(CornuCopyBuffer.from_filename(pathname, **kw))

class SHA256(_HashCode):
  ''' SHA256 hashcode class, subclass of `bytes`.
  '''

  __slots__ = ()
  hashfunc = hashlib.sha256
