#!/usr/bin/python
#
# Transcribe objects as text.
#   - Cameron Simpson <cs@cskk.id.au> 14jan2018
#

''' Classes to transcribe and parse textual forms of objects.

    The default syntax is slightly JSONlike syntax, though more compact and
    with less quoting noise.
    The usual form of a transcription is:

        prefix{inner}

    where `prefix` is an identifier indicating the type
    and `inner` holds the relevant details
    used to initialise the instance of the type.
    Usually `inner` takes the form:

        field:value,field:value...

    where `field` is an identifier and `value` is itself a Transcription.
    The actual form is controlled by a transcriber's `parse_inner` method.

    Some values have a more direct transcription:
    * `str`: transcribed as a quoted string
    * `int`: nonnegative integers are transcribed as bare decimal values
'''

from abc import ABC, abstractmethod
from collections import namedtuple, OrderedDict
from io import StringIO
import json
from string import ascii_letters, digits
import sys
from uuid import UUID

from typeguard import typechecked

from cs.deco import decorator
from cs.lex import get_identifier, is_identifier, \
                   get_decimal_or_float_value, get_qstr, \
                   texthexify
from cs.pfx import Pfx, pfx_call, pfx_method

from cs.x import X

# Characters that may appear in text sections of a texthexify result.
# Because we transcribe Dir blocks this way it includes some common
# characters used for metadata, notably including the double quote
# because it is heavily using in JSON.
# It does NOT include '/' because these appear at the start of paths.
_TEXTHEXIFY_WHITE_CHARS = ascii_letters + digits + '_+-.,=:;{"}*'

def hexify(data):
  ''' Represent a byte sequence as a hex/text string.
  '''
  return texthexify(data, whitelist=_TEXTHEXIFY_WHITE_CHARS)

class Transcriber(ABC):
  ''' Abstract base class for objects which can be used with the Transcribe class.

      Transcribers implement the following methods:
      * `transcribe_inner(T, fp)`: to transcribe to the file `fp`.
      * `parse_inner(T, s, offset, stopchar)`:
        to parse this class up to `stopchar`.

      Optional attribute:
      * `transcribe_prefix`:
        the default prefix for the "prefix{....}" markers.
  '''

  __slots__ = ()

  def __str__(self):
    return transcribe_s(self)

  @abstractmethod
  def transcribe_inner(self, T, fp):
    ''' Write the inner textual form of this object to the file `fp`.
        The result becomes "prefix{transcribe_inner()}".

        Parameters:
        * `T`: the Transcribe context
        * `fp`: the output file
    '''
    raise NotImplementedError()

  @staticmethod
  @abstractmethod
  def parse_inner(T, s, offset, stopchar, prefix):
    ''' Read the inner textual form of an object from `s` at `offset`.
        Return the object and new offset.

        Parameters:
        * `T`: the Transcribe context
        * `s`: the source text
        * `offset`: the parse position within `s`
        * `stopchar`: the end of object marker, usually '}'
        * `prefix`: the active prefix
    '''
    raise NotImplementedError()

class UUIDTranscriber:
  ''' A transcriber for uuid.UUID instances.
  '''

  @staticmethod
  def transcribe_inner(uu, fp):
    ''' Transcribe a UUID.
    '''
    fp.write(str(uu))

  @staticmethod
  def parse_inner(T, s, offset, stopchar, prefix):
    ''' Parse a UUID from `s` at `offset`.
        Return the UUID and the new offset.
    '''
    end_offset = s.find(stopchar, offset)
    if end_offset < offset:
      raise ValueError("offset %d: closing %r not found" % (offset, stopchar))
    uu = UUID(s[offset:end_offset])
    return uu, end_offset

ClassTranscriber = namedtuple('ClassTranscriber', 'cls transcribe_s parse')

class Transcribe:
  ''' Class to transcribe and parse textual forms of objects.
  '''

  def __init__(self):
    self.prefix_map = {}  # prefix -> baseclass
    self.class_map = {}  # baseclass -> prefix
    self.class_transcribers = {
        int: str,
        float: lambda f: "%f" % f,
        str: json.dumps,
        bool: lambda v: '1' if v else '0',
        bytes: hexify,
        dict: lambda m: json.dumps(m, separators=(',', ':')),
        UUID: lambda u: 'U{' + str(u) + '}',
    }
    self.register(UUIDTranscriber, 'U')

  def register(self, baseclass, prefixes):
    ''' Register a class and its default prefix.

        Parameters:
        * `baseclass`: the class to register, which should be a Transcriber.
        * `prefixes`: an iterable of string prefixes to lead
          "prefix{....}" transcriptions;
          the first prefix is the default.
          This may also be a single string.
    '''
    if isinstance(prefixes, str):
      prefixes = (prefixes,)
    for prefix in prefixes:
      if prefix in self.prefix_map:
        raise ValueError("prefix %r already taken" % (prefix,))
      if (not isinstance(baseclass, Transcriber)
          and (not hasattr(baseclass, 'transcribe_inner')
               or not hasattr(baseclass, 'parse_inner'))):
        raise ValueError(
            "baseclass %s not a subclass of Transcriber" % (baseclass,)
        )
      self.prefix_map[prefix] = baseclass
      if baseclass not in self.class_map:
        self.class_map[baseclass] = prefix

  def register_class(self, cls, transcribe_s, parse):
    ''' Register transcribers for a class `cls`.
    '''
    class_transcribers = self.class_transcribers
    if cls in class_transcribers:
      raise ValueError("class %s already registered" % (cls,))
    class_transcribers[cls] = ClassTranscriber(cls, transcribe_s, parse)

  def transcribe(self, o, prefix=None, fp=None):
    ''' Transcribe the object `o` to file `fp`.
        `o`: the object to transcribe.
        `prefix`: prefix leading the 'prefix{...}' transcription.
          If `prefix` is None, use `o.transcribe_prefix` if defined,
          otherwise look up `type(o)` in the class_transcribers and
          use that directly.
        `fp`: optional file, default sys.stdout
    '''
    with Pfx("transcribe(%s,%r,fp)", type(o), prefix):
      if fp is None:
        fp = sys.stdout
      if prefix is None:
        # see if there is an o.transcribe_prefix
        prefix = getattr(o, 'transcribe_prefix', None)
        if prefix is None:
          # see if this class is in class_transcribers
          tr = self.class_transcribers.get(type(o))
          if tr is None:
            raise ValueError(
                "prefix is None and no o.transcribe_prefix and no class transcriber"
            )
          fp.write(tr(o))
          return
      # use prefix and rely on o.transcribe_inner
      baseclass = self.prefix_map.get(prefix)
      if baseclass is not None:
        if not isinstance(o, baseclass):
          raise ValueError(
              "type(o)=%s, not an instanceof(%r)" % (type(o), baseclass)
          )
      fp.write(prefix)
      fp.write('{')
      o.transcribe_inner(self, fp)
      fp.write('}')

  def transcribe_s(self, o, prefix=None):
    ''' Transcribe the object `o` to a string.
        `o`: the object to transcribe.
        `prefix`: optional marker prefix
    '''
    fp = StringIO()
    self.transcribe(o, prefix=prefix, fp=fp)
    s = fp.getvalue()
    fp.close()
    return s

  def transcribe_mapping(self, m, fp):
    ''' Transcribe the mapping `m` to the file `fp`.
        `m`: the mapping to transcribe.
        `fp`: optional file, default sys.stdout
        The keys of the mapping must be identifiers.
        Values which are None are skipped.
    '''
    with Pfx("transcribe_mapping(%r)", m):
      first = True
      for k, v in m.items():
        if not is_identifier(k):
          raise ValueError("not an identifier key: %r" % (k,))
        if v is None:
          continue
        if not first:
          fp.write(',')
        fp.write(k)
        fp.write(':')
        self.transcribe(v, None, fp)
        first = False

  @pfx_method
  def parse(self, s, offset=0):
    ''' Parse an object from the string `s` starting at `offset`.
        Return the object and the new offset.

        Parameters:
        * `s`: the source string
        * `offset`: optional string offset, default 0
    '''
    # strings
    value, offset2 = self.parse_qs(s, offset, optional=True)
    if value is not None:
      return value, offset2
    # decimal values
    if s[offset:offset + 1].isdigit():
      return get_decimal_or_float_value(s, offset)
    # {json}
    if s.startswith('{', offset):
      sub = s[offset:]
      m, suboffset = pfx_call(json.JSONDecoder().raw_decode, sub)
      offset += suboffset
      return m, offset
    # prefix{....}
    prefix, offset = get_identifier(s, offset)
    if not prefix:
      raise ValueError("no type prefix at offset %d" % (offset,))
    with Pfx("prefix %r", prefix):
      if offset >= len(s) or s[offset] != '{':
        raise ValueError("missing opening '{' at offset %d" % (offset,))
      offset += 1
      baseclass = self.prefix_map.get(prefix)
      if baseclass is None:
        raise ValueError("prefix not registered: %r" % (prefix,))
      with Pfx("baseclass=%s", baseclass.__name__):
        o, offset = baseclass.parse_inner(self, s, offset, '}', prefix)
      if offset > len(s):
        raise ValueError("parse_inner returns offset beyond text")
      if offset >= len(s) or s[offset] != '}':
        raise ValueError("missing closing '}' at offset %d" % (offset,))
      offset += 1
      return o, offset

  @staticmethod
  def parse_qs(s, offset=0, optional=False):
    ''' Parse a quoted string from `s` at `offset`.
        Return the string value and the new offset.

        Parameters:
        * `s`: the source string
        * `offset`: optional string offset, default 0
        * `optional`: if true (default False), return None if there
          is no quoted string at offset instead of raising a ValueError
    '''
    if s.startswith("'", offset) or s.startswith('"', offset):
      return get_qstr(s, offset=offset, q=s[offset])
    if optional:
      return None, offset
    raise ValueError("offset %d: expected quoted string" % (offset,))

  def parse_mapping(
      self, s, offset=0, stopchar=None, required=None, optional=None
  ):
    ''' Parse a mapping from the string `s`.
        Return the mapping and the new offset.

        Parameters:
        * `s`: the source string
        * `offset`: optional string offset, default 0
        * `stopchar`: ending character, not to be consumed
        * `required`: if specified, validate that the mapping contains
          all the keys in this list
        * `optional`: if specified, validate that the mapping contains
          no keys which are not required or optional

        If `required` or `optional` is specified the return takes the form:

            offset, required_values..., optional_values...

        where missing optional values are presented as None.
    '''
    if optional is not None and required is None:
      raise ValueError(
          "required is None but optional is specified: %r" % (optional,)
      )
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
        raise ValueError(
            "offset %d: expected ',' but found: %r" % (offset, s[offset:])
        )
      offset += 1
    if required is None and optional is None:
      return d, offset
    for k in required:
      if k not in d:
        raise ValueError("missing required field %r" % (k,))
    if optional is not None:
      for k in d.keys():
        if k not in required and k not in optional:
          raise ValueError("unexpected field %r" % (k,))
    ret = [offset]
    for k in required:
      ret.append(d[k])
    for k in optional:
      ret.append(d.get(k))
    return ret

# global default Transcribe context
_TRANSCRIBE = Transcribe()

def register(cls, prefix=None, T=None):
  ''' Register a class and prefix with a Transcribe context.

      Parameters:
      * `cls`: the class to register
      * `prefix`: the marker prefix. If not specified, use `cls.transcribe_prefix`.
      * `T`: the transcribe context, default: `_TRANSCRIBE`
  '''
  global _TRANSCRIBE
  if prefix is None:
    prefix = cls.transcribe_prefix
  if T is None:
    T = _TRANSCRIBE
  return T.register(cls, prefix)

def transcribe(o, prefix=None, fp=None, T=None):
  ''' Transcribe the object `o` to file `fp`.

      Parameters:
      * `o`: the object to transcribe.
      * `prefix`: optional marker prefix
      * `fp`: optional file, default sys.stdout
      * `T`: the transcribe context, default: `_TRANSCRIBE`
  '''
  global _TRANSCRIBE
  if fp is None:
    fp = sys.stdout
  if T is None:
    T = _TRANSCRIBE
  return T.transcribe(o, prefix, fp)

def transcribe_s(o, prefix=None, T=None):
  ''' Transcribe the object `o` to a string.

      Parameters:
      * `o`: the object to transcribe.
      * `prefix`: optional marker prefix
      * `T`: the transcribe context, default: `_TRANSCRIBE`
  '''
  global _TRANSCRIBE
  if T is None:
    T = _TRANSCRIBE
  return T.transcribe_s(o, prefix)

def transcribe_mapping(m, fp, T=None):
  ''' Transcribe the mapping `m` to the file `fp`.

      Parameters:
      * `m`: the mapping to transcribe.
      * `fp`: optional file, default sys.stdout
      * `T`: the transcribe context, default: `_TRANSCRIBE`

      The keys of the mapping must be identifiers.
  '''
  global _TRANSCRIBE
  if T is None:
    T = _TRANSCRIBE
  return T.transcribe_mapping(m, fp)

def parse(s, offset=0, T=None):
  ''' Parse an object from the string `s`. Return the object and the new offset.

      Parameters:
      * `s`: the source string
      * `offset`: optional string offset, default 0
      * `T`: the transcribe context, default: `_TRANSCRIBE`
  '''
  global _TRANSCRIBE
  if T is None:
    T = _TRANSCRIBE
  return T.parse(s, offset)

def parse_mapping(
    s, offset=0, stopchar=None, T=None, required=None, optional=None
):
  ''' Parse a mapping from the string `s`.
      Return the mapping and the new offset.

      Parameters:
      * `s`: the source string
      * `offset`: optional string offset, default 0
      * `stopchar`: ending character, not to be consumed
      * `T`: the transcribe context, default: `_TRANSCRIBE`
  '''
  global _TRANSCRIBE
  if T is None:
    T = _TRANSCRIBE
  return T.parse_mapping(
      s, offset, stopchar, required=required, optional=optional
  )

@decorator
def mapping_transcriber(
    cls,
    *,
    prefix=None,
    T=None,
    transcription_mapping=None,
    required=None,
    optional=None,
    factory=None,
):
  ''' A class decorator to provide mapping style `parse_inner` and
      `transcribe_inner` methods and to register the class against
      a Transcribe instance.

      Parameters:
      * `prefix`: the prefix string
      * `T`: the transcribe instance, default: `_TRANSCRIBE`
      * `transcription_mapping`: a function of `self` to produce
        the mapping to be transcribed
      * `required`: optional list of keys required in the mapping
      * `optional`: optional list of keys which may be present in
        the mapping
      * `factory`: a factory to construct an instance of `cls` given
        keywords arguments supplied by the parsed mapping;
        default: `cls`

      Example:

          @mapping_transcriber(
              prefix="Ino",
              transcription_mapping=lambda self: {
                  'refcount': self.recount,
                  'E': self.E,
              },
              required=('refcount', 'E'),
              optional=(),
          )
          class Inode(Transcriber):
  '''
  if prefix is None:
    raise ValueError("missing prefix")
  if transcription_mapping is None:
    raise ValueError(
        "missing transcription_mapping, expected a function of self"
    )
  if factory is None:
    factory = cls

  @classmethod
  def parse_inner(cls, T, s, offset, stopchar, parsed_prefix):
    ''' Parse the inner section as a mapping.
    '''
    if parsed_prefix != prefix:
      raise ValueError(
          "expected prefix=%r, got: %r" % (
              prefix,
              parsed_prefix,
          )
      )
    m, offset = parse_mapping(
        s,
        offset,
        stopchar=stopchar,
        T=T,
        required=required,
        optional=optional
    )
    return factory(**m), offset

  cls.parse_inner = parse_inner

  def transcribe_inner(self, T, fp):
    return transcribe_mapping(transcription_mapping(self), fp, T=T)

  cls.transcribe_inner = transcribe_inner
  register(cls, prefix=prefix, T=T)
  return cls

if __name__ == '__main__':
  from .transcribe_tests import selftest
  selftest()
