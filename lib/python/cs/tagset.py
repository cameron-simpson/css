#!/usr/bin/env python3

''' Tags and sets of tags.
'''

from collections import namedtuple
from datetime import date, datetime
from json import JSONEncoder, JSONDecoder
from time import strptime
from types import SimpleNamespace
from cs.lex import (
    cutsuffix, get_dotted_identifier, get_nonwhite, is_dotted_identifier,
    skipwhite, lc_, titleify_lc, FormatableMixin
)
from cs.logutils import info, warning
from cs.pfx import Pfx, pfx_method

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

class TagSet(dict, FormatableMixin):
  ''' A setlike class associating a set of tag names with values.
  '''

  def __init__(self):
    ''' Initialise the `TagSet`.
    '''
    super().__init__()
    self.modified = False

  def __str__(self):
    ''' The `TagSet` suitable for writing to a tag file.
    '''
    return ' '.join(sorted(str(T) for T in self.as_tags()))

  def __repr__(self):
    return "%s:%r" % (type(self).__name__, dict.__repr__(self))

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

  def __contains__(self, tag):
    if isinstance(tag, str):
      return super().__contains__(tag)
    for mytag in self.as_tags():
      if mytag.matches(tag):
        return True
    return False

  def as_tags(self):
    ''' Yield the tag data as `Tag`s.
    '''
    for tag_name, value in self.items():
      yield Tag(tag_name, value)

  def as_dict(self):
    ''' Return a `dict` mapping tag name to value.
    '''
    return dict(self)

  def __setitem__(self, tag_name, value):
    self.set(tag_name, value)

  def add(self, tag_name, value=None, *, verbose=False):
    ''' Add a `Tag` or a `tag_name,value` to this `TagSet`.
    '''
    tag = Tag.from_name_value(tag_name, value)
    self.set(tag.name, tag.value, verbose=verbose)

  def set(self, tag_name, value, *, verbose=False):
    ''' Set `self[tag_name]=value`.
        If `verbose`, emit an info message if this changes the previous value.
    '''
    if verbose:
      old_value = self.get(tag_name)
      if tag_name not in self or old_value is not value:
        self.modified = True
      if tag_name not in self or old_value != value:
        info("+ %s", Tag(tag_name, value))
    super().__setitem__(tag_name, value)

  def __delitem__(self, tag_name):
    if tag_name not in self:
      raise KeyError(tag_name)
    self.discard(tag_name)

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
      value = tag.value
      if value is None or self[tag_name] == value:
        old_value = self.pop(tag_name)
        self.modified = True
        old_tag = Tag(tag_name, old_value)
        if verbose:
          info("- %s", old_tag)
        return old_tag
    return None

  def update(self, *others, **kw):
    ''' Update this `TagSet` from `other`,
        a dict or an iterable of taggy things.
    '''
    for other in others:
      try:
        keys = other.keys
      except AttributeError:
        for k, v in other:
          self[k] = v
      else:
        for k in keys():
          self[k] = other[k]
    for k, v in kw.items():
      self[k] = v

  @pfx_method
  def ns(self):
    ''' Compute and return a presentation of this `TagSet` as a
        nested `ExtendedNamespace`.

        `ExtendedNamespaces` provide a number of convenience attibutes
        derived from the concrete attributes. They are also usable
        as mapping in `str.format_map` and the like as they implement
        the `keys` and `__getitem__` methods.

        Note that if the `TagSet` includes tags named `'a.b'` and
        also `'a.b.c'` then the `'a.b'` value will be reflected as
        `'a.b._'` in order to keep `'a.b.c'` available.

        Also note that multiple dots in `Tag` names are collapsed;
        for example `Tag`s named '`a.b'`, `'a..b'`, `'a.b.'` and
        `'..a.b'` will all map to the namespace entry `a.b`.

        `Tag`s are processed in reverse lexical order by name, which
        dictates which of the conflicting multidot names takes
        effect in the namespace - the first found is used.
    '''
    ns0 = ExtendedNamespace()
    for tag_name in sorted(self, reverse=True):
      with Pfx(tag_name):
        subnames = [subname for subname in tag_name.split('.') if subname]
        if not subnames:
          warning("skipping weirdly named tag")
          continue
        ns = ns0
        subpath = []
        while len(subnames) > 1:
          subname = subnames.pop(0)
          subpath.append(subname)
          with Pfx('.'.join(subpath)):
            try:
              subns = getattr(ns, subname)
            except AttributeError:
              subns = ExtendedNamespace()
              setattr(ns, subname, subns)
            ns = subns
        subname, = subnames
        subpath.append(subname)
        with Pfx('.'.join(subpath)):
          setattr(ns, '_' if hasattr(ns, subname) else subname, self[tag_name])
    return ns0

  format_kwargs = ns

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

  @classmethod
  def from_string(cls, s, offset=0):
    tag, post_offset=cls.parse(s, offset=offset)
    if post_offset < len(s):
      raise ValueError("unparsed text after Tag %s: %r" % (tag,s[post_offset:]))
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
    ''' Parse tag_name[=value], return `(Tag,offset)`.
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

class ExtendedNamespace(SimpleNamespace):
  ''' Subclass `SimpleNamespace` with inferred attributes.
      This also presents attributes as `[]` elements via `__getitem__`.
  '''

  def __getattr__(self, attr):
    ''' Look up an indirect attribute, whose value is inferred from another.
    '''
    if attr == 'keys':
      return self.__dict__.keys
    with Pfx("%s(%r)", type(self).__name__, attr):
      getns = self.__dict__.get
      # attr vs attr_lc
      title_attr = cutsuffix(attr, '_lc')
      if title_attr is not attr:
        value = getns(title_attr)
        if value is not None:
          return lc_(value)
      value = getns(attr + '_lc')
      if value is not None:
        return titleify_lc(value)
      # plural from singular
      for pl_suffix in 's', 'es':
        single_attr = cutsuffix(attr, pl_suffix)
        if single_attr is not attr:
          value = getns(single_attr)
          if value is not None:
            return [value]
      # singular from plural
      for pl_suffix in 's', 'es':
        plural_attr = attr + pl_suffix
        value = getns(plural_attr)
        if isinstance(value, list) and value:
          return value[0]
      raise AttributeError(attr)

  def __getitem__(self, attr):
    try:
      value = getattr(self, attr)
    except AttributeError as e:
      raise KeyError(attr) from e
    return value
