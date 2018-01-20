#!/usr/bin/python
#
# Transcribe objects as text.
#   - Cameron Simpson <cs@cskk.id.au> 14jan2018
#

''' Classes to transcribe and parse textual forms of objects.
'''

from abc import ABC, abstractmethod
from collections import namedtuple, OrderedDict
from io import StringIO
from uuid import UUID
from cs.lex import get_identifier, is_identifier, \
                   get_decimal_value, get_qstr
from cs.pfx import Pfx

class Transcriber(ABC):
  ''' Abstract base class for objects which can be used with the Transcribe class.
  '''

  __slots__ = ()

  @classmethod
  def register_transcriber(cls, prefix=None, T=None):
    ''' Register this class as `prefix`.
    '''
    global _TRANSCRIBE
    if prefix is None:
      prefix = cls.transcribe_prefix
    if T is None:
      T = _TRANSCRIBE
    T.register(cls, prefix)

  @abstractmethod
  def transcribe_inner(self, fp):
    ''' Write the inner textual form of this object to the file `fp`.
        The result becomes "prefix{transcribe_inner()}".
    '''
    raise NotImplementedError()

  @staticmethod
  @abstractmethod
  def parse_inner(s, offset, stopchar):
    ''' Read the inner textual form of an object from `s` at offset `offset`. Return the object and new offset.
        `s`: the source text
        `offset`: the parse position within `s`
        `stopchar`: the end of object marker, usually '}'
    '''
    raise NotImplementedError()

class UUIDTranscriber:

  @staticmethod
  def transcribe_inner(uu, fp):
    fp.write(str(uu))

  @staticmethod
  def parse_inner(s, offset, stopchar):
    end_offset = s.find('}', offset)
    if end_offset < offset:
      raise ValueError("offset %d: closing '}' not found" % (offset,))
    uu = UUID(s[offset:end_offset])
    return uu, end_offset

ClassTranscriber = namedtuple('ClassTranscriber', 'cls transcribe_s parse')

class Transcribe:
  ''' Class to transcribe and parse textual forms of objects.
  '''

  def __init__(self):
    self.prefix_map = {}
    self.class_map = {}
    self.class_transcribers = {
        int: str,
        str: repr,
        UUID: lambda u: 'U{' + str(u) + '}',
    }
    self.register(UUIDTranscriber, 'U')

  def register(self, baseclass, prefix):
    ''' Register a `prefix` and its `baseclass`.
    '''
    assert isinstance(prefix, str)
    if prefix in self.prefix_map:
      raise ValueError("prefix %r already taken" % (prefix,))
    if ( not isinstance(baseclass, Transcriber)
         and ( not hasattr(baseclass, 'transcribe_inner')
               or not hasattr(baseclass, 'parse_inner')
             )
       ):
      raise ValueError("baseclass %s not a subclass of Transcriber" % (baseclass,))
    self.prefix_map[prefix] = baseclass
    if baseclass not in self.class_map:
      self.class_map[baseclass] = prefix

  def register_class(self, cls, transcribe, parse):
    ''' Register transcribers for a class `cls`.
    '''
    class_transcribers = self.class_transcribers
    if cls in class_transcribers:
      raise ValueError("class %s already registered" % (cls,))
    class_transcribers[cls] = ClassTranscriber(cls, transcribe, parse)

  def transcribe(self, o, prefix, fp):
    ''' Transcribe object `o` to file `fp` with `prefix`.
        `o`: the object to transcribe
        `fp`: file to which to transcribe the object
        `prefix`: prefix leading the '{...}' transcription
          If `prefix` is None, use `o.transcribe_prefix` if defined,
          otherwise look up `type(o)` in the class_transcribers and
          use that directly.
    '''
    with Pfx("transcribe(%s,%r,fp)", type(o), prefix):
      if prefix is None:
        # see if there is an o.transcribe_prefix
        prefix = getattr(o, 'transcribe_prefix', None)
        if prefix is None:
          # see if this class is in class_transcribers
          tr = self.class_transcribers.get(type(o))
          if tr is None:
            raise ValueError("prefix is None and no o.transcribe_prefix and no class transcriber")
          fp.write(tr(o))
          return
      # use prefix and rely on o.transcribe_inner
      baseclass = self.prefix_map.get(prefix)
      if baseclass is not None:
        if not isinstance(o, baseclass):
          raise ValueError("type(o)=%s, not an instanceof(%r)" % (type(o), baseclass))
      fp.write(prefix)
      fp.write('{')
      o.transcribe_inner(fp)
      fp.write('}')

  def transcribe_s(self, o, prefix):
    ''' Convenience function to transcribe object `o` with `prefix`, returns a string.
    '''
    fp = StringIO()
    self.transcribe(o, prefix, fp)
    s = fp.getvalue()
    fp.close()
    return s

  def transcribe_mapping(self, m, fp):
    ''' Transcribe a mapping of identifiers to transcribable objects.
    '''
    first = True
    for k, v in m.items():
      if not is_identifier(k):
        raise ValueError("not an identifier key: %r" % (k,))
      if not first:
        fp.write(',')
      fp.write(k)
      fp.write(':')
      self.transcribe(v, None, fp)
      first = False

  def parse(self, s, offset=0):
    ''' Parse an object transcription from the string `s` at the offset `offset`. Return the object and the new offset.
    '''
    if s.startswith("'", offset) or s.startswith('"', offset):
      return get_qstr(s, offset=offset, q=s[offset])
    if s[offset:offset+1].isdigit():
      return get_decimal_value(s, offset)
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
      if offset >= len(s) or s[offset] != '}':
        raise ValueError("missing closing '}' at offset %d" % (offset,))
      offset += 1
      return o, offset

  def parse_mapping(self, s, offset=0, stopchar=None):
    d = OrderedDict()
    while offset < len(s) and (stopchar is None or s[offset] != stopchar):
      k, offset = get_identifier(s, offset)
      if not k:
        raise ValueError("offset %d: not an identifier" % (offset,))
      if offset >= len(s) or s[offset] != ':':
        raise ValueError("offset %d: expected ':'" % (offset,))
      offset += 1
      v, offset = self.parse(s, offset)
      d[k] = v
      if offset >= len(s):
        break
      c = s[offset]
      if c == stopchar:
        break
      if c != ',':
        raise ValueError("offset %d: expected ','" % (offset,))
      offset += 1
    return d, offset

_TRANSCRIBE = Transcribe()

def transcribe(o, prefix, fp, T=None):
  ''' Transcribe the object `o` to file `fp`.
  '''
  global _TRANSCRIBE
  if T is None:
    T = _TRANSCRIBE
  return T.transcribe(o, prefix, fp)

def transcribe_s(o, prefix, T=None):
  ''' Transcribe the object `o` to file `fp`.
  '''
  global _TRANSCRIBE
  if T is None:
    T = _TRANSCRIBE
  return T.transcribe_s(o, prefix)

def transcribe_mapping(m, fp, T=None):
  global _TRANSCRIBE
  if T is None:
    T = _TRANSCRIBE
  return T.transcribe_mapping(m, fp)

def parse(s, offset=0, T=None):
  ''' Parse an object from the string `s`. return the object and the offset
  '''
  global _TRANSCRIBE
  if T is None:
    T = _TRANSCRIBE
  return T.parse(s, offset)

if __name__ == '__main__':
  from .transcribe_tests import selftest
  selftest()
