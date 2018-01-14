#!/usr/bin/python
#
# Transcribe objects as text.
#   - Cameron Simpson <cs@cskk.id.au> 14jan2018
#

''' Classes to transcribe and parse textual forms of objects.
'''

from abc import ABC, abstractmethod
from cs.lex import get_identifier
from cs.logutils import warning
from cs.pfx import Pfx

class Transcriber(ABC):
  ''' Abstract base class for objects which can be used with the Transcribe class.
  '''

  @abstractmethod
  def transcribe_inner(self, fp):
    ''' Write the textual form of this object to the file `fp`.
    '''
    raise NotImplementedError()

  @abstractmethod
  def parse_inner(self, s, offset, stopchar):
    ''' Read the textual form of an object from `s` at offset `offset`. Return the object and new offset.
        `s`: the source text
        `offset`: the parse position within `s`
        `stopchar`: the end of object marker, usually '}'
    '''
    raise NotImplementedError()

class Transcribe:
  ''' Class to transcribe and parse textual forms of objects.
  '''

  def __init__(self):
    self.prefix_map = {}

  def register(self, prefix, baseclass):
    ''' Register a `prefix` and its `baseclass`.
    '''
    if prefix in self.prefix_map:
      raise ValueError("prefix %r already taken" % (prefix,))
    if not isinstance(baseclass, Transcriber):
      raise ValueError("baseclass %s not a subclass of Transcriber" % (baseclass,))
    self.prefix_map[prefix] = baseclass

  def transcribe(self, fp, prefix, o):
    ''' Transcribe object `o` to file `fp` with `prefix`.
    '''
    with Pfx("transcribe(fp,%r,%s)", prefix, o):
      baseclass = self.prefix_map.get(prefix)
      if baseclass is None:
        raise ValueError("unregistered prefix")
      if not isinstance(o, baseclass):
        raise ValueError("type(o)=%s, not an instanceof(%r)" % (type(o), baseclass))
      print(prefix, '{', file=fp, sep='')
      o.transcribe_inner(fp)
      print('}', file=fp, sep='')

  def parse(self, s, offset=0):
    ''' Parse an object transcription from the string `s` at the offset `offset`. Return the object and the new offset.
    '''
    prefix, offset = get_identifier(s, offset)
    if not prefix:
      raise ValueError("no prefix at offset %d" % (offset,))
    with Pfx("prefix %r", prefix):
      if offset >= len(s) or s[offset] != '{':
        raise ValueError("missing opening '{' at offset %d" % (offset,))
      offset += 1
      baseclass = self.prefix_map.get(prefix)
      if baseclass is None:
        raise ValueError("prefix not registered: %r" % (prefix,))
      o, offset = baseclass.parse_inner(s, offset, '}')
      if offset > len(s):
        raise ValueError("parse_inner returns offset beyond text")
      if not isinstance(o, baseclass):
        warning("expected instance of %s but got %s", baseclass, type(o))
      if offset >= len(s) or s[offset] != '}':
        raise ValueError("missing closing '}' at offset %d" % (offset,))
      offset += 1
      return o, offset

_TRANSCRIBE = Transcribe()

def register(prefix, baseclass, T=None):
  ''' Register a `prefix` and its `baseclass`.
  '''
  global _TRANSCRIBE
  if T is None:
    T = _TRANSCRIBE
  T.register(prefix, baseclass)
