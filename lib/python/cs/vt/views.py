#!/usr/bin/env python3
#

''' Various views into a Store, presented as a mapping like a Dir:
    strings to things.
'''

from threading import Lock
from .block import Block, IndirectBlock
from .dir import Dir
from .hash import HashCode

class FilenameMapping:
  ''' A mapping of filenames to objects.
  '''

  def __init__(self, keep_hits=False):
    self.keep_hits = keep_hits
    self._hits = {}
    self._lock = Lock()

  def __getitem__(self, filename):
    with self._lock:
      o = self._hits.get(filename)
    if o is not None:
      return o
    try:
      o = self.deref(filename)
    except ValueError as e:
      raise KeyError("invalid filename") from e
    if self.keep_hits:
      with self._lock:
        self._hits[filename] = o
    return o

  def deref(self, filename):
    ''' Dereference `filename` and return an object.
        Raise `KeyError` for a name which does not resolve to a known object.
        Raise `ValueError` for invalid filenames.
    '''
    raise NotImplementedError("no %s.deref method" % (type(self),))

class DirectHashCodeMapping(FilenameMapping):
  ''' A `FilenameMapping` which maps hex hashcodes to `Block`s.
  '''

  def deref(self, filename):
    ''' Dereference `filename` and return a `Block`.
    '''
    return Block(hashcode=HashCode.from_filename(filename))

class IndirectHashCodeMapping(FilenameMapping):
  ''' A `FilenameMapping` which maps hex hashcodes to `IndirectBlock`s.
  '''

  def deref(self, filename):
    ''' Dereference `filename` and return an `IndirectBlock`.
    '''
    return IndirectBlock.from_hashcode(HashCode.from_filename(filename))

class DirMapping(FilenameMapping):
  ''' A `FilenameMapping` which maps hex hashcodes to `Dir`s
      identified by their `IndirectBlock`.
  '''

  def deref(self, filename):
    ''' Dereference `filename` and return a `Dir`.
    '''
    return Dir(
        filename,
        IndirectBlock.from_hashcode(HashCode.from_filename(filename))
    )
