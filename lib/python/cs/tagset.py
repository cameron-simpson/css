#!/usr/bin/env python3

''' Tags and sets of tags.
'''

from collections import namedtuple
from datetime import date, datetime
from json import JSONEncoder, JSONDecoder
from time import strptime
from types import SimpleNamespace as NS
from cs.lex import (
    cutsuffix, get_dotted_identifier, get_nonwhite, is_dotted_identifier,
    skipwhite, lc_, titleify_lc
)
from cs.logutils import info, warning
from cs.pfx import Pfx

try:
  date_fromisoformat = date.fromisoformat
except AttributeError:

  def date_fromisoformat(datestr):
    ''' Placeholder for `date.fromisoformat`.
    '''
    parsed = strptime(datestr, '%Y-%m-%d')
    return date(parsed.tm_year, parsed.tm_mon, parsed.tm_mday)

try:
  datetime_fromisoformat = datetime.fromisoformat
except AttributeError:

  def datetime_fromisoformat(datestr):
    ''' Placeholder for `datetime.fromisoformat`.
    '''
    parsed = strptime(datestr, '%Y-%m-%dT%H:%M:%S')
    return datetime(
        parsed.tm_year, parsed.tm_mon, parsed.tm_mday, parsed.tm_hour,
        parsed.tm_min, parsed.tm_sec
    )

__version__ = '20200229.1'

DISTINFO = {
    'keywords': ["python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.lex',
        'cs.logutils',
        'cs.pfx',
    ],
}

class TagSet:
  ''' A setlike class associating a set of tag names with values.
  '''

  def __init__(self):
    ''' Initialise the `TagSet`.
    '''
    self.tagmap = {}
    self.modified = False

  def __str__(self):
    ''' The `TagSet` suitable for writing to a tag file.
    '''
    return ' '.join(sorted(str(T) for T in self.as_tags()))

  def __repr__(self):
    return "%s:%r" % (type(self).__name__, self.tagmap)

  @classmethod
  def from_line(cls, line, offset=0):
    ''' Create a new `TagSet` from a line of text.
    '''
    tags = cls()
    offset = skipwhite(line, offset)
    while offset < len(line):
      tag, offset = Tag.parse(line, offset)
      tags.add(tag)
      offset = skipwhite(line, offset)
    return tags

  @classmethod
  def from_bytes(cls, bs):
    ''' Create a new `TagSet` from the bytes `bs`,
        a UTF-8 encoding of a `TagSet` line.
    '''
    line = bs.decode(errors='replace')
    return cls.from_line(line)

  def __len__(self):
    return len(self.tagmap)

  def __contains__(self, tag):
    tagmap = self.tagmap
    if isinstance(tag, str):
      return tag in tagmap
    for mytag in self.as_tags():
      if mytag.matches(tag):
        return True
    return False

  def __getitem__(self, tag_name):
    ''' Fetch tag value by `tag_name`.
        Raises `KeyError` for missing `tag_name`.
    '''
    return self.tagmap[tag_name]

  def get(self, tag_name, default=None):
    ''' Fetch tag value by `tag_name`, or `default`.
    '''
    try:
      value = self[tag_name]
    except KeyError:
      value = default
    return value

  def as_tags(self):
    ''' Yield the tag data as `Tag`s.
    '''
    for tag_name, value in self.tagmap.items():
      yield Tag(tag_name, value)

  __iter__ = as_tags

  def as_dict(self):
    ''' Return a `dict` mapping tag name to value.
    '''
    return dict(self.tagmap)

  def add(self, tag_name, value=None, *, verbose=False):
    ''' Add a tag to these tags.
    '''
    tag = Tag.from_name_value(tag_name, value)
    tag_name = tag.name
    tagmap = self.tagmap
    value = tag.value
    if tag_name not in tagmap or tagmap[tag_name] != value:
      if verbose:
        info("+ %s", tag)
      tagmap[tag_name] = value
      self.modified = True

  def discard(self, tag_name, value=None, *, verbose=False):
    ''' Discard the tag matching `(tag_name,value)`.
        Return a `Tag` with the old value,
        or `None` if there was no matching tag.

        Note that if the tag value is `None`
        then the tag is unconditionally discarded.
        Otherwise the tag is only discarded
        if its value matches.
    '''
    tag = Tag.from_name_value(tag_name, value)
    tag_name = tag.name
    if tag_name in self:
      tagmap = self.tagmap
      value = tag.value
      if value is None or tagmap[tag_name] == value:
        old_value = tagmap.pop(tag_name)
        self.modified = True
        old_tag = Tag(tag_name, old_value)
        if verbose:
          info("- %s", old_tag)
        return old_tag
    return None

  def update(self, other, *, prefix=None, verbose=False):
    ''' Update this `TagSet` from `other`.

        Parameters:
        * `other`: a mapping of name->value
          or an iterable of `Tag`like things
        * `prefix`: an optional prefix for the update names;
          if a nonempty string, the update names will be
          `prefix+'.'+name`.
        * `verbose`: verbosity flag (default `False`)
          passed to `self.add`.
    '''
    try:
      other_kvs = other.items()
    except AttributeError:
      # not a mapping, presume an iterable
      for tag in other:
        self.add(tag.prefix_name(prefix), verbose=verbose)
    else:
      # a mapping, convert to iterable and recurse
      self.update(
          (Tag.from_name_value(k, v) for k, v in other_kvs),
          prefix=prefix,
          verbose=verbose
      )

  def format_kwargs(self):
    kwargs = {}
    # initial kwargs: all tags directly
    for tag_name, value in self.as_dict().items():
      kwargs[tag_name] = value
    # fill out computed/impled tags
    for tag_name, value in self.as_dict().items():
      # provide _lc versions of strings unless called _lc, in which
      # case the reverse
      if isinstance(value, str):
        tag_name_prefix = cutsuffix(tag_name, '_lc')
        if tag_name_prefix is tag_name:
          # not a _lc tag_name
          tag_name_lc = tag_name + '_lc'
          if tag_name_lc not in kwargs:
            kwargs[tag_name_lc] = lc_(value)
        else:
          # tag_name is foo_lc, compute title version if missing
          if tag_name_prefix not in kwargs:
            kwargs[tag_name_prefix] = titleify_lc(value)
    # convert dashes and dots
    for k, v in sorted(kwargs.items()):
      # a-b synonym as a_b
      if '-' in k:
        k_ = k.replace('-', '_')
        if k_ not in kwargs:
          kwargs[k_] = v
      # a.b.c as nested SimpleNamespace
      if '.' in k:
        # TODO: utility function
        parts = list(filter(None, k.split('.')))
        if not parts:
          continue
        kbase = parts.pop(0)
        try:
          ns0 = kwargs[kbase]
        except KeyError:
          ns0 = NS()
        ns = ns0
        while parts:
          attr = parts.pop(0)
          if parts:
            # more substructure, descend into it
            try:
              subns = getattr(ns, attr)
            except AttributeError:
              # make subns
              subns = NS()
              setattr(ns, attr, subns)
            ns = subns
        # no more parts, assign value
        try:
          setattr(ns, attr, v)
        except AttributeError as e:
          warning("could not set k: %s", e)
        else:
          # attach the namespace if not originally present
          if kbase not in kwargs:
            kwargs[kbase] = ns0
    return kwargs

class Tag(namedtuple('Tag', 'name value')):
  ''' A Tag has a `.name` (`str`) and a `.value`.

      The `name` must be a dotted identifier.

      A "bare" `Tag` has a `value` of `None`.
  '''

  # A JSON encoder used for tag values which lack a special encoding.
  # The default here is "compact": no whitespace in delimiters.
  JSON_ENCODER = JSONEncoder(separators=(',', ':'))

  # A JSON decoder.
  JSON_DECODER = JSONDecoder()

  EXTRA_TYPES = [
      (date, date_fromisoformat, date.isoformat),
      (datetime, datetime_fromisoformat, datetime.isoformat),
  ]

  def __eq__(self, other):
    return self.name == other.name and self.value == other.value

  def __lt__(self, other):
    if self.name < other.name:
      return True
    if self.name > other.name:
      return False
    return self.value < other.value

  def __repr__(self):
    return "%s(name=%r,value=%r)" % (
        type(self).__name__, self.name, self.value
    )

  def __str__(self):
    ''' Encode `tag_name` and `value`.
    '''
    name = self.name
    value = self.value
    if value is None:
      return name
    return name + '=' + self.transcribe_value(value)

  def prefix_name(self, prefix):
    ''' Return a `Tag` whose `.name` has an additional prefix.

        If `prefix` is `None` or empty, return this `Tag`.
        Otherwise return a new `Tag` whose name is `prefix+'.'+self.name`.
    '''
    return (
        self.from_name_value('.'.join((prefix, self.name)), self.value)
        if prefix else self
    )

  @classmethod
  def transcribe_value(cls, value):
    ''' Transcribe `value` for use in `Tag` transcription.
    '''
    for type_, _, to_str in cls.EXTRA_TYPES:
      if isinstance(value, type_):
        value_s = to_str(value)
        # should be nonwhitespace
        if get_nonwhite(value_s)[0] != value_s:
          raise ValueError(
              "to_str(%r) => %r: contains whitespace" % (value, value_s)
          )
        return value_s
    # "bare" dotted identifiers
    if isinstance(value, str) and is_dotted_identifier(value):
      return value
    # convert some values to a suitable type
    if isinstance(value, (tuple, set)):
      value = list(value)
    # fall back to JSON encoded form of value
    return cls.JSON_ENCODER.encode(value)

  @classmethod
  def from_name_value(cls, name, value):
    ''' Support method for functions accepting either a tag or a name and value.

        If `name` is a str make a new Tag from `name` and `value`.
        Otherwise check that `value is `None`
        and that `name` has a `.name` and `.value`
        and return it as a tag ducktype.

        This supports functions of the form:

            def f(x, y, tag_name, value=None):
              tag = Tag.from_name_value(tag_name, value)

        so that that may accept a `Tag` or a tag name or a tag name and value.

        Exanples:

            >>> Tag.from_name_value('a', 3)
            Tag(name='a',value=3)
            >>> T = Tag('b', None)
            >>> Tag.from_name_value(T, None)
            Tag(name='b',value=None)
    '''
    with Pfx("%s.from_name_value(name=%r,value=%r)", cls.__name__, name,
             value):
      if isinstance(name, str):
        # (name,value) => Tag
        return cls(name, value)
      if value is not None:
        raise ValueError("name is not a str, value must be None")
      tag = name
      if not hasattr(tag, 'name'):
        raise ValueError("tag has no .name attribute")
      if not hasattr(tag, 'value'):
        raise ValueError("tag has no .value attribute")
      # Tag ducktype
      return tag

  @staticmethod
  def is_valid_name(name):
    ''' Test whether a tag name is valid: a dotted identifier including dash.
    '''
    return is_dotted_identifier(name, extras='_-')

  @staticmethod
  def parse_name(s, offset=0):
    ''' Parse a tag name from `s` at `offset`: a dotted identifier including dash.
    '''
    return get_dotted_identifier(s, offset=offset, extras='_-')

  def matches(self, tag_name, value=None):
    ''' Test whether this `Tag` matches `(tag_name,value)`.
    '''
    other_tag = self.from_name_value(tag_name, value)
    if self.name != other_tag.name:
      return False
    return other_tag.value is None or self.value == other_tag.value

  @classmethod
  def parse(cls, s, offset=0):
    ''' Parse tag_name[=value], return `(tag,offset)`.
    '''
    with Pfx("%s.parse(%r)", cls.__name__, s[offset:]):
      name, offset = cls.parse_name(s, offset)
      with Pfx(name):
        if offset < len(s):
          sep = s[offset]
          if sep.isspace():
            value = None
          elif sep == '=':
            offset += 1
            value, offset = cls.parse_value(s, offset)
          else:
            name_end, offset = get_nonwhite(s, offset)
            name += name_end
            value = None
            ##warning("bad separator %r, adjusting tag to %r" % (sep, name))
        else:
          value = None
      return cls(name, value), offset

  @classmethod
  def parse_value(cls, s, offset=0):
    ''' Parse a value from `s` at `offset` (default `0`).
        Return the value, or `None` on no data.
    '''
    if offset >= len(s) or s[offset].isspace():
      warning("offset %d: missing value part", offset)
      value = None
    else:
      try:
        value, offset2 = cls.parse_name(s, offset)
      except ValueError:
        value = None
      else:
        if offset == offset2:
          value = None
      if value is not None:
        offset = offset2
      else:
        # check for special "nonwhitespace" transcription
        nonwhite, nw_offset = get_nonwhite(s, offset)
        nw_value = None
        for _, from_str, _ in cls.EXTRA_TYPES:
          try:
            nw_value = from_str(nonwhite)
          except ValueError:
            pass
        if nw_value is not None:
          # special format found
          value = nw_value
          offset = nw_offset
        else:
          # decode as plain JSON data
          value_part = s[offset:]
          value, suboffset = cls.JSON_DECODER.raw_decode(value_part)
          offset += suboffset
    return value, offset

class TagChoice(namedtuple('TagChoice', 'spec choice tag')):
  ''' A "tag choice", an apply/reject flag and a `Tag`,
      used to apply changes to a `TagSet`
      or as a criterion for a tag search.

      Attributes:
      * `spec`: the source text from which this choice was parsed,
        possibly `None`
      * `choice`: the apply/reject flag
      * `tag`: the `Tag` representing the criterion
  '''

  @classmethod
  def parse(cls, s, offset=0):
    ''' Parse a tag choice from `s` at `offset` (default `0`).
        Return the `TagChoice` and new offset.
    '''
    offset0 = offset
    if s.startswith('-', offset):
      choice = False
      offset += 1
    else:
      choice = True
    tag, offset = Tag.parse(s, offset=offset)
    return cls(s[offset0:offset], choice, tag), offset
