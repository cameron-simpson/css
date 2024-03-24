#!/usr/bin/env python3
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
import re
from string import ascii_letters, digits
import sys
from typing import (
    Any,
    Iterable,
    Mapping,
    Optional,
    Tuple,
    Type,
    Union,
)
from uuid import UUID

from cs.deco import decorator, Promotable
from cs.gimmicks import warning
from cs.lex import (
    get_identifier,
    get_decimal_or_float_value,
    get_qstr,
    is_identifier,
    r,
    texthexify,
)
from cs.pfx import Pfx, pfx, pfx_call, pfx_method

# a regular expression to match a UUID
UUID_re = re.compile(
    r'[\da-f]{8}-[\da-f]{4}-[\da-f]{4}-[\da-f]{4}-[\da-f]{12}', re.I
)

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

class Transcriber(Promotable):  ##, ABC):
  ''' Abstract base class for objects which can be transcribed.

      Transcribers implement the following methods:
      * `transcribe_inner(T, fp)`: to transcribe to the file `fp`.
      * `parse_inner(T, s, offset, stopchar)`:
        to parse this class up to `stopchar`.

      Optional attribute:
      * `transcribe_prefix`:
        the default prefix for the "prefix{....}" markers.
  '''

  __slots__ = ()

  class_by_prefix = {}  # prefix -> class
  prefix_by_class = {}  # class -> prefix
  class_transcribers = {
      int: str,
      float: lambda f: "%f" % f,
      str: json.dumps,
      bool: lambda v: '1' if v else '0',
      bytes: hexify,
      dict: lambda m: json.dumps(m, separators=(',', ':')),
      UUID: lambda u: 'U{' + str(u) + '}',
  }

  @classmethod
  def __init_subclass__(cls, *, prefix: Union[None, str, Iterable[str]], **kw):
    ''' Register a subclass and its default prefix.

        `prefix` should be a string or an iterable of string prefixes to lead
        "prefix{....}" transcriptions.  The first prefix is the default.
    '''
    super().__init_subclass__(**kw)
    if prefix is None:
      # explicitly no prefix: a superclass of some other transcriber subclasses
      return
    if isinstance(prefix, str):
      prefixes = (prefix,)
    else:
      prefixes = list(prefix)
      if not prefixes:
        raise ValueError('prefixes may not be empty')
    for prefix in prefixes:
      try:
        known_cls = cls.class_by_prefix[prefix]
      except KeyError:
        cls.class_by_prefix[prefix] = cls
      else:
        if not issubclass(cls, known_cls):
          warning(
              f'cls={cls!r}: prefix {prefix!r} already taken by a non-superclass: {known_cls!r}'
          )
      if cls not in cls.prefix_by_class:
        # note the default prefix for the class
        cls.prefix_by_class[cls] = prefix

  def __str__(self):
    ''' Return the transcription of this object.
    '''
    return type(self).transcribe_obj(self)

  @classmethod
  def from_str(cls, s: str):
    ''' Parse a transcription into an instance.
    '''
    with Pfx("%s.from_str(%r)", cls.__name__, s):
      obj, offset = cls.parse(s)
      if offset < len(s):
        raise ValueError(f'unparsed data after transcription: {s[offset:]}')
      if not isinstance(obj, cls):
        raise TypeError(
            f'did not decode to instance of {cls.__name__}, got: {r(obj)}'
        )
      return obj

  @classmethod
  def transcribe_obj(cls, obj, prefix: Optional[str] = None) -> str:
    ''' Class method to transcribe `obj` as a string.

        If `obj` is an instance of one of the predefined compact
        types (`int` et al) defined in `cls.class_transcribers`
        then use the compact transcription otherwise use
        *prefix*`{`*obj.transcribe_inner()*`}`.
    '''
    obj_type = type(obj)
    to_str = cls.class_transcribers.get(obj_type)
    if to_str is None:
      # prefix based use of obj.transcribe_inner()
      if prefix is None:
        prefix = cls.prefix_by_class[obj_type]
      return f'{prefix}{{{obj.transcribe_inner()}}}'
    # use the predefined transcription; prefix should be None
    if prefix is not None:
      raise ValueError(
          f'{cls.__name__}.class_transcribers[{obj_type!r}] exists, prefix should be None, was {prefix!r}'
      )
    return to_str(obj)

  @abstractmethod
  def transcribe_inner(self) -> str:
    ''' Write the inner textual form of this object to the file `fp`.
        The result becomes "prefix{transcribe_inner()}".
    '''
    raise NotImplementedError

  @classmethod
  @abstractmethod
  def parse_inner(cls, s, offset=0, *, stopchar='}', prefix=None):
    ''' Read the inner textual form of an object from `s` at `offset`.
        Return the new instance of `cls` and new offset.

        Parameters:
        * `s`: the source text
        * `offset`: the parse position within `s`
        * `stopchar`: the end of object marker, default '}'
        * `prefix`: the active prefix, if any
    '''
    raise NotImplementedError

  @pfx
  def transcribe_mapping_inner(self, m: Mapping[str, Any]) -> str:
    ''' Transcribe the mapping `m` as a `str`.
        This returns the inner items comma separated without the
        usual surronding `{...}` markers.
        The keys of the mapping must be identifiers.
        Values which are `None` are skipped.
    '''
    tokens = []
    for k, v in m.items():
      with Pfx("%r=%r", k, v):
        if not is_identifier(k):
          raise ValueError("key is not an identifier")
        if v is None:
          continue
        tokens.append(f'{k}:{self.transcribe_obj(v)}')
    return ','.join(tokens)

  @classmethod
  @pfx_method
  def parse(
      cls,
      s: str,
      offset: int = 0,
      *,
      expected_cls: Optional[Type] = None,
  ) -> Tuple[Any, int]:
    ''' Parse an object from the string `s` starting at `offset`.
        Return the object and the new offset.

        Parameters:
        * `s`: the source string
        * `offset`: optional string offset, default 0
        * `expected_cls`: optional; if provided, require an instance of `expected_cls`

        If `parse` is called via a subclass of `Transcriber` and
        `expected_cls` omitted then it defaults to the subclass,
        so that:

            _Dirent.parse(s)

        will ensure that some instance of `_Dirent` is found.
    '''
    if expected_cls is Any:
      expected_cls = None
    elif expected_cls is None and cls is not Transcriber:
      expected_cls = cls
    # strings
    obj, offset2 = cls.parse_qs(s, offset, optional=True)
    if obj is not None:
      offset = offset2
    # decimal values
    elif s[offset:offset + 1].isdigit():
      obj, offset = get_decimal_or_float_value(s, offset)
    # bare {json}
    elif s.startswith('{', offset):
      sub = s[offset:]
      obj, suboffset = pfx_call(json.JSONDecoder().raw_decode, sub)
      offset += suboffset
    elif offset < len(s) and s[offset].isalpha():
      # prefix{....}
      prefix, offset = get_identifier(s, offset)
      assert prefix
      with Pfx("prefix %r", prefix):
        if not s.startswith('{', offset):
          raise ValueError("missing opening '{' at offset %d" % (offset,))
        offset += 1
        if prefix == 'U':
          # UUID
          m = UUID_re.match(s, offset)
          if not m:
            raise ValueError("expected a UUID")
          obj = UUID(m.group())
          offset = m.end()
        else:
          prefix_cls = cls.class_by_prefix.get(prefix)
          if prefix_cls is None:
            raise ValueError("prefix not registered")
          with Pfx("prefix_cls=%s", prefix_cls.__name__):
            obj, offset = prefix_cls.parse_inner(s, offset, '}', prefix)
            assert isinstance(
                obj, prefix_cls
            ), f'{prefix_cls}.parse_inner did not return the expected object type, got {type(obj)}'
          if offset > len(s):
            raise ValueError("parse_inner returns offset beyond text")
        if not s.startswith('}', offset):
          raise ValueError("missing closing '}' at offset %d" % (offset,))
        offset += 1
    else:
      raise ValueError(
          f'parse error at offset {offset}: {s[offset:offset+16]!r}'
      )
    if expected_cls is not None and not isinstance(obj, expected_cls):
      raise ValueError(
          f'unexpected object type at offset {offset}: expected {expected_cls} but got {r(obj)}'
      )
    return obj, offset

  @staticmethod
  def parse_qs(
      s: str,
      offset: int = 0,
      optional: Optional[bool] = False,
  ) -> [Union[str, None], int]:
    ''' Parse a quoted string from `s` at `offset`.
        Return the string value and the new offset.

        Parameters:
        * `s`: the source string
        * `offset`: optional string offset, default 0
        * `optional`: if true (default `False`), return `None` if there
          is no quoted string at offset instead of raising `ValueError`
    '''
    if s.startswith("'", offset) or s.startswith('"', offset):
      return get_qstr(s, offset=offset, q=s[offset])
    if optional:
      return None, offset
    raise ValueError("offset %d: expected quoted string" % (offset,))

  @classmethod
  def parse_mapping(
      cls, s, offset=0, stopchar=None, required=None, optional=None
  ):
    ''' Parse a mapping from the string `s`.
        Return the mapping and the new offset.

        A mapping is expressed as comma separated set of
        *name*`:`*transcribed_value* pairs, ended by `stopchar`.

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
      with Pfx("offset %d", offset):
        k, offset = get_identifier(s, offset)
        if not k:
          raise ValueError('expected identifier')
      with Pfx("offset %d", offset):
        if not s.startswith(':', offset):
          raise ValueError("expected ':'")
      offset += 1
      with Pfx("offset %d", offset):
        v, offset = cls.parse(s, offset, expected_cls=Any)
      d[k] = v
      with Pfx("offset %d", offset):
        if offset >= len(s):
          break
        c = s[offset]
        if c == stopchar:
          break
        if c != ',':
          raise ValueError(f"expected ',' or {stopchar!r} but found: {c!r}")
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

if __name__ == '__main__':
  from .transcribe_tests import selftest
  selftest()
