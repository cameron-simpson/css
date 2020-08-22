#!/usr/bin/env python3

''' Tags and sets of tags
    with __format__ support and optional ontology information.

    See `cs.fstags` for support for applying these to filesystem objects
    such as directories and files.

    See `cs.sqltags` for support for databases of entities with tags,
    not directly associated with filesystem objects.
    This is suited to both log entries (entities with no "name")
    and large collections of named entities;
    both accept `Tag`s and can be searched on that basis.

    All of the available complexity is optional:
    you can use `Tag`s without bothering with `TagSet`s
    or `TagsOntology`s.

    This module contains the following main classes:
    * `Tag`: an object with a `.name` and optional `.value` (default `None`)
      and also an optional reference `.ontology`
      for associating semantics with tag values.
      The `.value`, if not `None`, will often be a string,
      but may be any Python object.
      If you're using these via `cs.fstags`,
      the object will need to be JSON transcribeable.
    * `TagSet`: a `dict` subclass representing a set of `Tag`s
      to associate with something;
      it also has setlike `.add` and `.discard` methods.
      As such it only supports a single `Tag` for a given tag name,
      but that tag value can of course be a sequence or mapping
      for more elaborate tag values.
    * `TagsOntology`:
      a mapping of type names to `TagSet`s defining the type.
      This mapping also contains entries for the metadata
      for specific type values.

    Here's a simple example with some `Tag`s and a `TagSet`.

        >>> tags = TagSet()
        >>> # add a "bare" Tag named 'blue' with no value
        >>> tags.add('blue')
        >>> # add a "topic=tagging" Tag
        >>> tags.add('topic','tagging')
        >>> # make a "subtopic" Tag and add it
        >>> subtopic = Tag('subtopic', 'ontologies')
        >>> # Tags have nice repr() and str()
        >>> subtopic
        Tag(name='subtopic',value='ontologies',ontology=None)
        >>> print(subtopic)
        subtopic=ontologies
        >>> # you can add a Tag directly
        >>> tags.add(subtopic)
        >>> # TagSets also have nice repr() and str()
        >>> tags
        TagSet:{'blue': None, 'topic': 'tagging', 'subtopic': 'ontologies'}
        >>> print(tags)
        blue subtopic=ontologies topic=tagging
        >>> # because TagSets are dicts you can format strings with them
        >>> print('topic:{topic} subtopic:{subtopic}'.format_map(tags))
        topic:tagging subtopic:ontologies
        >>> # TagSets have convenient membership tests
        >>> # test for blueness
        >>> 'blue' in tags
        True
        >>> # test for redness
        >>> 'red' in tags
        False
        >>> # test for any "subtopic" tag
        >>> 'subtopic' in tags
        True
        >>> # test for subtopic=ontologies
        >>> subtopic in tags
        True
        >>> subtopic2 = Tag('subtopic', 'libraries')
        >>> # test for subtopic=libraries
        >>> subtopic2 in tags
        False
'''

from collections import namedtuple
from datetime import date, datetime
from json import JSONEncoder, JSONDecoder
import re
from types import SimpleNamespace
from icontract import require
from cs.dateutils import unixtime2datetime
from cs.edit import edit as edit_lines
from cs.lex import (
    cropped_repr, cutsuffix, get_dotted_identifier, get_nonwhite,
    is_dotted_identifier, skipwhite, lc_, titleify_lc, FormatableMixin
)
from cs.logutils import warning, ifverbose
from cs.obj import SingletonMixin
from cs.pfx import Pfx, pfx, pfx_method, XP
from cs.py3 import date_fromisoformat, datetime_fromisoformat

__version__ = '20200716-post'

DISTINFO = {
    'keywords': ["python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.dateutils',
        'cs.edit',
        'cs.lex',
        'cs.logutils',
        'cs.obj>=20200716',
        'cs.pfx',
        'cs.py3',
        'icontract',
    ],
}

class TagSet(dict, FormatableMixin):
  ''' A setlike class associating a set of tag names with values.

      This actually subclasses `dict`, so a `TagSet` is a direct
      mapping of tag names to values.

      *NOTE*: iteration yields `Tag`s, not dict keys.

      Also note that all the `Tags` from `TagSet`
      share its ontology.

      Subclasses should override the `set` and `discard` methods;
      the `dict` and mapping methods
      are defined in terms of these two basic operations.
  '''

  @pfx_method
  def __init__(self, *, ontology=None):
    ''' Initialise the `TagSet`.
    '''
    super().__init__()
    self.ontology = ontology
    self.modified = False

  def __str__(self):
    ''' The `TagSet` suitable for writing to a tag file.
    '''
    return ' '.join(map(str, sorted(self)))

  def __repr__(self):
    return "%s:%s" % (type(self).__name__, dict.__repr__(self))

  @classmethod
  def from_line(cls, line, offset=0, *, ontology=None, verbose=None):
    ''' Create a new `TagSet` from a line of text.
    '''
    tags = cls(ontology=ontology)
    offset = skipwhite(line, offset)
    while offset < len(line):
      tag, offset = Tag.parse(line, offset, ontology=ontology)
      tags.add(tag, verbose=verbose)
      offset = skipwhite(line, offset)
    return tags

##@classmethod
##def from_bytes(cls, bs, ontology=None):
##  ''' Create a new `TagSet` from the bytes `bs`,
##      a UTF-8 encoding of a `TagSet` line.
##  '''
##  line = bs.decode(errors='replace')
##  return cls.from_line(line, ontology=ontology)

  def __contains__(self, tag):
    if isinstance(tag, str):
      return super().__contains__(tag)
    for mytag in self:
      if mytag.matches(tag):
        return True
    return False

  def as_tags(self, prefix=None):
    ''' Yield the tag data as `Tag`s.
    '''
    for tag_name, value in self.items():
      yield Tag(
          prefix + '.' + tag_name if prefix else tag_name,
          value,
          ontology=self.ontology
      )

  __iter__ = as_tags

  def as_dict(self):
    ''' Return a `dict` mapping tag name to value.
    '''
    return dict(self)

  def __setitem__(self, tag_name, value):
    self.set(tag_name, value)

  def add(self, tag_name, value=None, *, verbose=None):
    ''' Add a `Tag` or a `tag_name,value` to this `TagSet`.
    '''
    tag = Tag(tag_name, value)
    self.set(tag.name, tag.value, verbose=verbose)

  def set(self, tag_name, value, *, verbose=None):
    ''' Set `self[tag_name]=value`.
        If `verbose`, emit an info message if this changes the previous value.
    '''
    old_value = self.get(tag_name)
    if tag_name not in self or old_value is not value:
      self.modified = True
      if tag_name not in self or old_value != value:
        ifverbose(
            verbose, "+ %s (was %s)",
            Tag(tag_name, value, ontology=self.ontology), old_value
        )
    super().__setitem__(tag_name, value)

  def __delitem__(self, tag_name):
    if tag_name not in self:
      raise KeyError(tag_name)
    self.discard(tag_name)

  def discard(self, tag_name, value=None, *, verbose=None):
    ''' Discard the tag matching `(tag_name,value)`.
        Return a `Tag` with the old value,
        or `None` if there was no matching tag.

        Note that if the tag value is `None`
        then the tag is unconditionally discarded.
        Otherwise the tag is only discarded
        if its value matches.
    '''
    tag = Tag(tag_name, value)
    tag_name = tag.name
    if tag_name in self:
      value = tag.value
      if value is None or self[tag_name] == value:
        old_value = self.pop(tag_name)
        self.modified = True
        old_tag = Tag(tag_name, old_value)
        ifverbose(verbose, "- %s", old_tag)
        return old_tag
    return None

  def set_from(self, other, verbose=None):
    ''' Completely replace the values in `self`
        with the values from `other`,
        a `TagSet` or any other `name`=>`value` dict.

        This has the feature of logging changes
        by calling `.set` and `.discard` to effect the changes.
    '''
    for name, value in sorted(other.items()):
      self.set(name, value, verbose=verbose)
    for name in list(self.keys()):
      if name not in other:
        self.discard(name, verbose=verbose)

  def update(self, other, *, prefix=None, verbose=None):
    ''' Update this `TagSet` from `other`,
        a dict of `{name:value}`
        or an iterable of `Tag`like or `(name,value)` things.
    '''
    try:
      # produce (name,value) from dict
      items_attr = other.items
    except AttributeError:
      items = other
    else:
      items = items_attr()
    for item in items:
      try:
        name, value = item
      except ValueError:
        name, value = item.name, item.value
      if prefix:
        name = prefix + '.' + name
      self.set(name, value, verbose=verbose)

  @pfx_method
  def ns(self):
    ''' Return a `TagSetNamespace` for this `TagSet`.

        This has many convenience facilities for use in format strings.
    '''
    return TagSetNamespace.from_tagset(self)

  format_kwargs = ns

  def edit(self, verbose=None):
    ''' Edit this `TagSet`.
    '''
    lines = (
        ["# Edit TagSet.", "# One tag per line."] +
        list(map(str, sorted(self)))
    )
    new_lines = edit_lines(lines)
    new_values = {}
    for lineno, line in enumerate(new_lines):
      with Pfx("%d: %r", lineno, line):
        line = line.strip()
        if not line or line.startswith('#'):
          continue
        tag = Tag.from_string(line)
        new_values[tag.name] = tag.value
        self.set_from(new_values, verbose=verbose)

class ValueMetadata(namedtuple('ValueMetadata', 'ontology ontkey value')):
  ''' Metadata information about a value.
        * `ontology`: the reference ontology
        * `ontkey`: the key within the ontology providing the metadata
        * `value`: the value
    '''

  @property
  def metadata(self):
    ''' The metadata, the `TagSet` from `ontology[ontkey]`.
      '''
    return self.ontology[self.ontkey]

  def ns(self):
    ''' Return a `ValueMetadataNamespace` for this `ValueMetadata`.
    '''
    return ValueMetadataNamespace.from_metadata(self)

class KeyValueMetadata(namedtuple('KeyValueMetadata',
                                  'key_metadata value_metadata')):
  ''' Metadata information about a `(key,value)` pair.
      * `ontology`: the reference ontology
      * `key_metadata`: the metadata for the `key`,
        the `TagSet` from `ontology[key_metadata.ontkey]`
      * `value`: the value
      * `value_metadata`: the metadata for the `value`,
        the `TagSet` from `ontology[value_metadata.ontkey]`
  '''

class Tag(namedtuple('Tag', 'name value ontology')):
  ''' A Tag has a `.name` (`str`) and a `.value`
      and an optional `.ontology`.

      The `name` must be a dotted identifier.

      Terminology:
      * A "bare" `Tag` has a `value` of `None`.
      * A "naive" `Tag` has an `ontology` of `None`.

      The constructor for a `Tag` is unusual:
      * both the `value` and `ontology` are optional,
        defaulting to `None`
      * if `name` is a `str` then we always construct a new `Tag`
        with the suppplied values
      * if `name` is not a `str`
        it should be a `Tag`like object to promote;
        it is an error if the `value` parameter is not `None`
        in this case

      The promotion process is as follows:
      * if `name` is a `Tag` subinstance
        then if the supplied `ontology` is not `None`
        and is not the ontology associated with `name`
        then a new `Tag` is made,
        otherwise `name` is returned unchanged
      * otherwise a new `Tag` is made from `name`
        using its `.value`
        and overriding its `.ontology`
        if the `ontology` parameter is not `None`
  '''

  def __new__(cls, name, value=None, *, ontology=None):
    # simple case: name is a str: make a new Tag
    if isinstance(name, str):
      # (name[,value[,ontology]]) => Tag
      return super().__new__(cls, name, value, ontology)
    # name should be taglike
    if value is not None:
      raise ValueError(
          "name(%s) is not a str, value must be None" % (type(name).__name__)
      )
    tag = name
    if not hasattr(tag, 'name'):
      raise ValueError("tag has no .name attribute")
    if not hasattr(tag, 'value'):
      raise ValueError("tag has no .value attribute")
    if isinstance(tag, Tag):
      # already a Tag subtype, see if the ontology needs updating
      if ontology is not None and tag.ontology is not ontology:
        # new Tag with supplied ontology
        tag = super().__new__(cls, tag.name, tag.value, ontology)
    else:
      # not a Tag subtype, construct a new instance,
      # overriding .ontology if the supplied ontology is not None
      tag = super().__new__(
          cls, tag.name, tag.value, (
              ontology
              if ontology is not None else getattr(tag, 'ontology', None)
          )
      )
    return tag

  # A JSON encoder used for tag values which lack a special encoding.
  # The default here is "compact": no whitespace in delimiters.
  JSON_ENCODER = JSONEncoder(separators=(',', ':'))

  # A JSON decoder.
  JSON_DECODER = JSONDecoder()

  EXTRA_TYPES = [
      (date, date_fromisoformat, date.isoformat),
      (datetime, datetime_fromisoformat, datetime.isoformat),
  ]

  @classmethod
  def with_prefix(cls, name, value, *, ontology=None, prefix):
    ''' Make a new `Tag` whose `name` is prefixed with `prefix+'.'`.
    '''
    if prefix:
      name = prefix + '.' + name
    return cls(name, value, ontology=ontology)

  def __eq__(self, other):
    return self.name == other.name and self.value == other.value

  def __lt__(self, other):
    if self.name < other.name:
      return True
    if self.name > other.name:
      return False
    return self.value < other.value

  def __repr__(self):
    return "%s(name=%r,value=%r,ontology=%r)" % (
        type(self).__name__, self.name, self.value, self.ontology
    )

  def __str__(self):
    ''' Encode `name` and `value`.
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
    if not prefix:
      return self
    return type(self)(
        '.'.join((prefix, self.name)), self.value, ontology=self.ontology
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
  def from_string(cls, s, offset=0, ontology=None):
    ''' Parse a `Tag` definition from `s` at `offset` (default `0`).
    '''
    with Pfx("%s.from_string(%r[%d:],...)", cls.__name__, s, offset):
      tag, post_offset = cls.parse(s, offset=offset, ontology=ontology)
      if post_offset < len(s):
        raise ValueError(
            "unparsed text after Tag %s: %r" % (tag, s[post_offset:])
        )
      return tag

  @staticmethod
  def is_valid_name(name):
    ''' Test whether a tag name is valid: a dotted identifier.
    '''
    return is_dotted_identifier(name)

  @staticmethod
  def parse_name(s, offset=0):
    ''' Parse a tag name from `s` at `offset`: a dotted identifier.
    '''
    return get_dotted_identifier(s, offset=offset)

  def matches(self, tag_name, value=None):
    ''' Test whether this `Tag` matches `(tag_name,value)`.
    '''
    other_tag = type(self)(tag_name, value)
    if self.name != other_tag.name:
      return False
    return other_tag.value is None or self.value == other_tag.value

  @classmethod
  def parse(cls, s, offset=0, *, ontology):
    ''' Parse tag_name[=value], return `(Tag,offset)`.
    '''
    with Pfx("%s.parse(%s)", cls.__name__, cropped_repr(s, offset=offset)):
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
      return cls(name, value, ontology=ontology), offset

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

  @property
  @pfx_method(use_str=True)
  def typedata(self):
    ''' The defining `TagSet` for this tag's name.

        This is how its type is defined,
        and is obtained from:
        `self.ontology.typedata_tagset(self.name)`
    '''
    ont = self.ontology
    if ont is None:
      warning("%s:%r: no ontology, returning None", type(self), self)
      return None
    return ont[self.name]

  @property
  @pfx_method(use_str=True)
  def key_typedata(self):
    ''' Return the typedata definition for this `Tag`'s keys.
    '''
    typedata = self.typedata
    if typedata is None:
      return None
    key_type = typedata.get('key_type')
    if key_type is None:
      return None
    ont = self.ontology
    return ont[key_type]

  @pfx_method(use_str=True)
  def key_metadata(self, key):
    ''' Return the metadata definition for `key`.
    '''
    typedata = self.typedata
    if typedata is None:
      return None
    key_type = typedata.get('key_type')
    if key_type is None:
      return None
    ont = self.ontology
    key_metadata_name = key_type + '.' + ont.value_to_tag_name(key)
    return ont[key_metadata_name]

  @property
  @pfx_method(use_str=True)
  def member_typedata(self):
    ''' Return the typedata definition for this `Tag`'s members.
    '''
    typedata = self.typedata
    if typedata is None:
      return None
    member_type = typedata.get('member_type')
    if member_type is None:
      return None
    ont = self.ontology
    return ont[member_type]

  @pfx_method(use_str=True)
  def member_metadata(self, member_key):
    ''' Return the metadata definition for self[member_key].
    '''
    typedata = self.typedata
    if typedata is None:
      return None
    member_type = typedata.get('member_type')
    if member_type is None:
      return None
    ont = self.ontology
    value = self.value[member_key]
    member_metadata_name = member_type + '.' + ont.value_to_tag_name(value)
    return ont[member_metadata_name]

  @property
  @pfx_method(use_str=True)
  def type(self):
    ''' The type name for this `Tag`.

        Unless the definition for `self.name` has a `type` tag,
        the type is `self.ontology.value_to_tag_name(self.name)`.

        For example, the tag `series="Avengers (Marvel)"`
        would look up the definition for `series`.
        If that had no `type=` tag, then the type
        would default to `series`
        which is what would be returned.

        The corresponding metadata `TagSet` for that tag
        would have the name `series.marvel.avengers`.

        By contrast, the tag `cast={"Scarlett Johasson":"Black Widow (Marvel)"}`
        would look up the definition for `cast`
        which might look like this:

            cast type=dict key_type=person member_type=character

        That says that the type name is `dict`,
        which is what would be returned.

        Because the type is `dict`
        the definition also has `key_type` and `member_type` tags
        identifying the type names for the keys and values
        of the `cast=` tag.
        As such, the corresponding metadata `TagSet`s
        in this example would be named
        `person.scarlett_johasson`
        and `character.marvel.black_widow` respectively.
    '''
    typedata = self.typedata
    if typedata is None:
      return None
    type_name = typedata.get('type')
    if type_name is None:
      type_name = self.ontology.value_to_tag_name(self.name)
    return type_name

  @property
  @pfx_method(use_str=True)
  def basetype(self):
    ''' The base type name for this tag.

        This calls `TagsOntology.basetype(self.type)`.
    '''
    ont = self.ontology
    if ont is None:
      warning("no ontology, returning None")
      return None
    return ont.basetype(self.type)

  @property
  @require(lambda self: isinstance(self.type, str))
  def metadata(self):
    ''' The metadataed information about this specific tag value,
        derived through the ontology from the tag name and value.

        For a scalar type this is a `ValueMetadata`
        with the following attributes:
        * `ontology`: the reference ontology
        * `ontkey`: the ontology key providing the metadata for the `value`
        * `value`: the value `self.value`
        * `metadata`: the metadata, a `TagSet`

        However, note that the types `'list'` and `'dict'` are special,
        indicating that the value is a sequence or mapping respectively.

        For `'list'` types
        this property is a list of `ValueMetadata` instances
        for each element of the sequence.

        For `'dict'` types
        this property is a list of `KeyValueMetadata` instances
        with the following attributes:
        * `ontology`: the reference ontology
        * `key`: the key
        * `key_metadata`: a `ValueMetadata` for the key
        * `value`: the value
        * `value_metadata`: a `ValueMetadata` for the value
    '''
    ont = self.ontology
    basetype = self.basetype
    if basetype == 'list':
      member_type = self.member_type
      return [ont.value_metadata(member_type, value) for value in self.value]
    if basetype == 'dict':
      key_type = self.key_type
      member_type = self.member_type
      return [
          KeyValueMetadata(
              ont.value_metadata(key_type, key),
              ont.value_metadata(member_type, value)
          ) for key, value in self.value.items()
      ]
    return ont.value_metadata(self.name, self.value)

  @property
  def key_type(self):
    ''' The type name for members of this tag.

        This is required if `.value` is a mapping.
    '''
    try:
      return self.typedata['key_type']
    except KeyError:
      raise AttributeError('key_type')

  @property
  @pfx_method
  def member_type(self):
    ''' The type name for members of this tag.

        This is required if `.value` is a sequence or mapping.
    '''
    try:
      return self.typedata['member_type']
    except KeyError:
      raise AttributeError('member_type')

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
    tag, offset = Tag.parse(s, offset=offset, ontology=None)
    return cls(s[offset0:offset], choice, tag), offset

  @classmethod
  @pfx_method
  def from_str(cls, s):
    ''' Prepare a `TagChoice` from the string `s`.
    '''
    tag_choice, offset = cls.parse(s)
    if offset != len(s):
      raise ValueError("unparsed TagChoice specification: %r" % (s[offset:],))
    return tag_choice

  @classmethod
  @pfx_method
  def from_any(cls, o):
    ''' Convert some suitable object `o` into a `TagChoice`.

        Various possibilities for `o` are:
        * `TagChoice`: returned unchanged
        * `str`: a string tests for the presence
          of a tag with that name and optional value;
        * an object with a `.choice` attribute;
          this is taken to be a `TagChoice` ducktype and returned unchanged
        * an object with `.name` and `.value` attributes;
          this is taken to be `Tag`-like and a positive test is constructed
        * `Tag`: an object with a `.name` and `.value`
          is equivalent to a positive `TagChoice`
        * `(name,value)`: a 2 element sequence
          is equivalent to a positive `TagChoice`
    '''
    if isinstance(o, cls):
      # already a TagChoice
      return o
    if isinstance(o, str):
      # parse choice form string
      return cls.from_str(o)
    try:
      name, value = o
    except (TypeError, ValueError):
      if hasattr(o, 'choice'):
        # assume TagChoice ducktype
        return o
      try:
        name = o.name
        value = o.value
      except AttributeError:
        pass
      else:
        return cls(None, True, Tag(name, value))
    else:
      # (name,value) => True choice
      return cls(None, True, Tag(name, value))
    raise TypeError("cannot infer %s from %s:%s" % (cls, type(o), o))

class ExtendedNamespace(SimpleNamespace):
  ''' Subclass `SimpleNamespace` with inferred attributes
      intended primarily for use in format strings.
      As such it also presents attributes as `[]` elements via `__getitem__`.

      Because [:alpha:]* attribute names
      are reserved for "public" keys/attributes,
      most methods commence with an underscore (`_`).
  '''

  def _public_keys(self):
    return (k for k in self.__dict__.keys() if k and k[0].isalpha())

  def _public_keys_str(self):
    return ','.join(sorted(self._public_keys()))

  def _public_items(self):
    return ((k, v) for k, v in self.__dict__.items() if k and k[0].isalpha())

  def __str__(self):
    return '{' + type(self).__name__ + ':' + ','.join(
        str(k) + '=' + repr(v) for k, v in sorted(self._public_items())
    ) + '}'

  def __len__(self):
    ''' The number of public keys.
    '''
    return len(self._public_keys())

  @pfx_method
  def __format__(self, spec):
    ''' The default formatted form of this node.
        The value to format is `'{`*type*':'*path*'['*public_keys*']'`.
    '''
    return (
        "{%s:%s%s}" %
        (type(self).__name__, self._path, sorted(self._public_keys()))
    ).__format__(spec)

  @property
  def _path(self):
    ''' The path to this node as a dotted string.
    '''
    pathnames = getattr(self, '_pathnames', ())
    return '.'.join(pathnames)

  def _subns(self, subname):
    ''' Create and attache a new subnamespace named `subname`
        of the same type as `self`.
        Return the new subnamespace.

        It is an error if `subname` is already present in `self.__dict__`.
    '''
    if subname in self.__dict__:
      raise ValueError(
          "%s: attribute %r already exists" % (self._path, subname)
      )
    subns = type(self)(_pathnames=self._pathnames + (subname,))
    setattr(self, subname, subns)
    return subns

  def __getattr__(self, attr):
    ''' Autogenerate stub subnamespacs for [:alpha:]* attributes
        containing a `Tag` for the attribute with a placeholder string.
    '''
    if attr and attr[0].isalpha():
      # no such attribute, create a placeholder `Tag`
      # for [:alpha:]* names
      format_placeholder = '{' + self._path + '.' + attr + '}'
      subns = self._subns(attr)
      overtag = self.__dict__.get('_tag')
      subns._tag = Tag(
          attr,
          format_placeholder,
          ontology=overtag.ontology if overtag else None
      )
      return subns
    raise AttributeError("%s: %s" % (self._path, attr))

  @pfx_method
  def __getitem__(self, attr):
    with Pfx("%s[%r]", self._path, attr):
      try:
        value = getattr(self, attr)
      except AttributeError:
        raise KeyError(attr)
      return value

class TagSetNamespace(ExtendedNamespace):
  ''' A formattable nested namespace for a `TagSet`,
      subclassing `ExtendedNamespace`.

      These are useful within format strings
      and `str.format` or `str.format_map`.

      This provides an assortment of special names derived from the `TagSet`.
      See the docstring for `__getattr__` for the special attributes provided
      beyond those already provided by `ExtendedNamespace.__getattr__`.
  '''

  @classmethod
  @pfx_method
  def from_tagset(cls, tags, pathnames=None):
    ''' Compute and return a presentation of this `TagSet` as a
        nested `ExtendedNamespace`.

        `ExtendedNamespace`s provide a number of convenience attibutes
        derived from the concrete attributes. They are also usable
        as mapping in `str.format_map` and the like as they implement
        the `keys` and `__getitem__` methods.

        Note that multiple dots in `Tag` names are collapsed;
        for example `Tag`s named '`a.b'`, `'a..b'`, `'a.b.'` and
        `'..a.b'` will all map to the namespace entry `a.b`.

        `Tag`s are processed in reverse lexical order by name, which
        dictates which of the conflicting multidot names takes
        effect in the namespace - the first found is used.
    '''
    if pathnames is None:
      pathnames = []
    ns0 = cls(
        _path='.'.join(pathnames) if pathnames else '.',
        _pathnames=tuple(pathnames)
    )
    if tags:
      ns0._ontology = tags.ontology
      for tag in sorted(tags, reverse=True):
        with Pfx(tag):
          tag_name = tag.name
          subnames = [subname for subname in tag_name.split('.') if subname]
          if not subnames:
            warning("skipping weirdly named tag")
            continue
          ns = ns0
          subpath = []
          while subnames:
            subname = subnames.pop(0)
            subpath.append(subname)
            dotted_subpath = '.'.join(subpath)
            with Pfx(dotted_subpath):
              subns = ns.__dict__.get(subname)
              if subns is None:
                subns = ns.__dict__[subname] = TagSetNamespace.from_tagset(
                    None, subpath
                )
              ns = subns
          ns._tag = tag
    return ns0

  @pfx_method
  def __format__(self, spec):
    ''' Format this node.
        If there's a `Tag` on the node, format its value.
        Otherwise use the superclass format.
    '''
    tag = self.__dict__.get('_tag')
    if tag is not None:
      return format(tag.value, spec)
    return super().__format__(spec)

  @pfx_method
  def __getitem__(self, key):
    tag = self.__dict__.get('_tag')
    if tag is not None:
      # This node in the hierarchy is associated with a Tag.
      # Dereference the Tag's value.
      value = tag.value
      try:
        element = value[key]
      except TypeError as e:
        warning("[%r]: %s", key, e)
        pass
      except KeyError:
        # Leave a visible indication of the unfulfilled dereference.
        return self._path + '[' + repr(key) + ']'
      else:
        # Look up this element in the ontology (if any).
        member_metadata = tag.member_metadata(key)
        if member_metadata is None:
          # No metadata? Return the element.
          return element
        # Return teh metadata for the element.
        return member_metadata.ns()
    return super().__getitem__(key)

  def _tag_value(self):
    ''' Fetch the value if this node's `Tag`, or `None`.
    '''
    tag = self.__dict__.get('_tag')
    if tag is None:
      warning("%s: no ._tag", self)
      return None
    return tag.value

  def _attr_tag_value(self, attr):
    ''' Fetch the value of the `Tag` at `attr` (a namespace with a `._tag`).
        Returns `None` if required attributes are not present.
    '''
    attr_value = self.__dict__.get(attr)
    if attr_value is None:
      ##warning("%s: no .%r", self, attr)
      return None
    return attr_value._tag_value()

  @pfx_method
  def __getattr__(self, attr):
    ''' Look up an indirect node attribute,
        whose value is inferred from another.

        The following attribute names and forms are supported:
        * `_keys`: the keys of the value
          for the `Tag` associated with this node;
          meaningful if `self._tag.value` has a `keys` method
        * `_meta`: a namespace containing the meta information
          for the `Tag` associated with this node
        * `_type`: a namespace containing the type definition
          for the `Tag` associated with this node
        * `_values`: the values within the `Tag.value`
          for the `Tag` associated with this node
        * *baseattr*`_lc`: lowercase and titled forms.
          If *baseattr* exists,
          return its value lowercased via `cs.lex.lc_()`.
          Conversely, if *baseattr* is required
          and does not directly exists
          but its *baseattr*`_lc` form does,
          return the value of *baseattr*`_lc`
          titlelified using `cs.lex.titleify_lc()`.
        * *baseattr*`s`, *baseattr*`es`: singular/plural.
          If *baseattr* exists
          return `[self.`*baseattr*`]`.
          Conversely,
          if *baseattr* does not exist but one of its plural attributes does,
          return the first element from the plural attribute.
    '''
    path = self.__dict__.get('_path')
    with Pfx("%s:%s.%s", type(self).__name__, path, attr):
      if attr == 'cover':
        raise RuntimeError("BANG")
      getns = self.__dict__.get
      if attr == '_type':
        return self._tag.typedata.ns()
      if attr == '_meta':
        return self._tag.metadata.ns()
      if attr == '_keys':
        tag = getns('_tag')
        if tag is not None:
          value = tag.value
          try:
            keys = value.keys
          except AttributeError:
            pass
          else:
            return list(keys())
      if attr == '_values':
        tag = getns('_tag')
        if tag is not None:
          value = tag.value
          try:
            values = value.values
          except AttributeError:
            pass
          else:
            return list(values())
      # end of private/special attributes
      if attr.startswith('_'):
        raise AttributeError(attr)
      # attr vs attr_lc
      title_attr = cutsuffix(attr, '_lc')
      if title_attr is not attr:
        title_value = self._attr_tag_value(title_attr)
        if title_value is not None:
          value_lc = lc_(title_value)
          return value_lc
      else:
        attr_lc_value = getns(attr + '_lc')
        if attr_lc_value is not None:
          return titleify_lc(value)
      # plural from singular
      for pl_suffix in 's', 'es':
        single_attr = cutsuffix(attr, pl_suffix)
        if single_attr is not attr:
          single_value = self._attr_tag_value(single_attr)
          if single_value is not None:
            return [single_value]
      # singular from plural
      for pl_suffix in 's', 'es':
        plural_attr = attr + pl_suffix
        plural_value = self._attr_tag_value(plural_attr)
        if plural_value is None:
          continue
        value0 = plural_value[0]
        return value0
      return super().__getattr__(attr)

  @property
  def ontology(self):
    ''' The reference ontology.
      '''
    return self.key_metadata.ontology

  @property
  def key(self):
    ''' The key.
      '''
    return self.key_metadata.value

  @property
  def value(self):
    ''' The value.
      '''
    return self.value_metadata.value

class ValueMetadataNamespace(TagSetNamespace):
  ''' A subclass of `TagSetNamespace` for a `Tag`'s metadata.

      The reference `TagSet` is the defining `TagSet`
      for the metadata of a particular `Tag` value
      as defined by a `ValueMetadata`
      (the return value of `Tag.metadata`).
  '''

  @classmethod
  @pfx_method
  def from_metadata(cls, meta, pathnames=None):
    ''' Construct a new `ValueMetadataNamespace` from `meta` (a `ValueMetadata`).
    '''
    ont = meta.ontology
    ontkey = meta.ontkey
    tags = ont[ontkey]
    ns0 = cls.from_tagset(tags, pathnames=pathnames)
    ns0._ontology = ont
    ns0._ontkey = ontkey
    ns0._value = meta.value
    return ns0

  @pfx_method
  def __format__(self, spec):
    ''' Format this node.
        If there's a `Tag` on the node, format its value.
        Otherwise use the superclass format.
    '''
    XP(
        "XNS%s.__FORMAT__(spec=%r): self=%s, %r", type(self), spec, self,
        self.__dict__
    )
    return (
        "{%s:%r[%s]}" % (self._ontkey, self._value, self._public_keys_str())
    ).__format__(spec)

class TagsOntology(SingletonMixin):
  ''' An ontology for tag names.

      This is based around a mapping of tag names
      to ontological information expressed as a `TagSet`.

      A `cs.fstags.FSTags` uses ontologies initialised from `TagFile`s
      containing ontology mappings.
  '''

  # A mapping of base type named to Python types.
  BASE_TYPES = {
      t.__name__: t
      for t in (int, float, str, list, dict, date, datetime)
  }

  @classmethod
  def _singleton_key(cls, tagset_mapping):
    return id(tagset_mapping)

  def __init__(self, tagset_mapping):
    if hasattr(self, 'tagsets'):
      return
    self.tagsets = tagset_mapping

  def __str__(self):
    return "%s(%s)" % (type(self).__name__, self.tagsets)

  __repr__ = __str__

  def __getitem__(self, index):
    assert isinstance(index, str)
    return self.tagsets[index]

  @staticmethod
  @pfx
  def value_to_tag_name(value):
    ''' Convert a tag value to a tagnamelike dotted identifierish string
        for use in ontology lookup.
        Returns `None` for unconvertable values.

        Nonnegative `int`s are converted to `str`.

        Strings are converted as follows:
        * a trailing `(.*)` is turned into a prefix with a dot,
          for example `"Captain America (Marvel)"`
          becomes `"Marvel.Captain America"`.
        * the string is split into words (nonwhitespace),
          lowercased and joined with underscores,
          for example `"Marvel.Captain America"`
          becomes `"marvel.captain_america"`.
    '''
    if isinstance(value, int) and value >= 0:
      return str(value)
    if isinstance(value, str):
      value = value.strip()
      m = re.match(r'(.*)\(([^()]*)\)\s*$', value)
      if m:
        value = m.group(2).strip() + '.' + m.group(1).strip()
      value = '_'.join(value.lower().split())
      return value
    raise ValueError(value)

  @pfx_method
  @require(lambda type_name: isinstance(type_name, str))
  def value_metadata(self, type_name, value):
    ''' Return a `ValueMetadata` for `type_name` and `value`.
        This provides the mapping between a type's value and its semantics.

        For example,
        if a `TagSet` had a list of characters such as:

            characters=["Captain America (Marvel)","Black Widow (Marvel)"]

        then these values could be converted to the dotted identifiers
        `character.marvel.captain_america`
        and `character.marvel.black_widow` respectively,
        ready for lookup in the ontology
        to obtain the "metadata" `TagSet` for each specific value.
    '''
    if isinstance(value, str):
      value_tag_name = self.value_to_tag_name(value)
      ontkey = type_name + '.' + '_'.join(value_tag_name.lower().split())
      return ValueMetadata(self, ontkey, value)
    return None

  def basetype(self, typename):
    ''' Infer the base type name from a type name.
        The default type is `'str'`,
        but any type which resolves to one in `self.BASE_TYPES`
        may be returned.
    '''
    typename0 = typename
    typeinfo = self[typename]
    seen = set((typename,))
    while 'type' in typeinfo:
      typename = typeinfo['type']
      if typename in seen:
        warning(
            "type %r: circular type definitions involving %r", typename0, seen
        )
        break
    if typename not in self.BASE_TYPES:
      typename = 'str'
    return typename

  def convert_tag(self, tag):
    ''' Convert a `Tag`'s value accord to the ontology.
        Return a new `Tag` with the converted value
        or the original `Tag` unchanged.

        This is primarily aimed at things like regexp based autotagging,
        where the matches are all strings
        but various fields have special types,
        commonly `int`s or `date`s.
    '''
    basetype = Tag(tag, ontology=self).basetype
    try:
      converter = {
          'date': date_fromisoformat,
          'datetime': datetime_fromisoformat,
      }[basetype]
    except KeyError:
      converter = self.BASE_TYPES.get(basetype)
    if converter:
      try:
        converted = converter(tag.value)
      except (ValueError, TypeError):
        pass
      else:
        tag = Tag(tag.name, converted)
    return tag

class TagsCommandMixin:
  ''' Utility methods for `cs.cmdutils.BaseCommand` classes working with tags.

      Optional subclass attributes:
      * `TAG_CHOICE_CLASS`: a `TagChoice` duck class.
        For example, `cs.sqltags` has a subclass
        with an `.extend_query` method for computing an SQL JOIN
        used in searching for tagged entities.
  '''

  @classmethod
  def parse_tag_choices(cls, argv, tag_choice_class=None):
    ''' Parse a list of tag specifications `argv` of the form:
        * `-`*tag_name*: a negative requirement for *tag_name*
        * *tag_name*[`=`*value*]: a positive requirement for a *tag_name*
          with optional *value*.
        Return a list of `TagChoice` instances for each `arg` in `argv`.

        The optional parameter `tag_choice_class` is a class
        with a `.from_str(str)` factory method
        returning a `TagChoice` duck instance.
        The default `tag_choice_class` is `cls.TAG_CHOICE_CLASS`
        or `TagChoice`.
    '''
    if tag_choice_class is None:
      tag_choice_class = getattr(cls, 'TAG_CHOICE_CLASS', TagChoice)
    choices = []
    for arg in argv:
      with Pfx(arg):
        choices.append(tag_choice_class.from_str(arg))
    return choices

class TaggedEntityMixin(FormatableMixin):
  ''' A mixin for classes like `TaggedEntity`.

      A `TaggedEnity`like instance has the following attributes:
      * `id`: a domain specific identifier;
        this may reasonably be `None` for entities
        not associated with database rows.
      * `name`: the entity's name;
        this is typically `None` for log entries.
      * `unixtime`: a UNIX timestamp,
        a `float` holding seconds since the UNIX epoch
        (midnight, 1 January 1970 UTC).
        This is typically the row creation time
        for entities associated with database rows.
      * `tags`: a `TagSet`, a mapping of names to values.

      This is a common representation of some tagged entity,
      and also is the intermediary form used by the `cs.fstags` and
      `cs.sqltags` import/export CSV format.

      The `id` column has domain specific use.
      For `cs.sqltags` the `id` attribute will be the database row id.
      For `cs.fstags` the `id` attribute will be `None`.
      It is available for other domains as an arbitrary identifier/key value,
      should that be useful.
  '''

  @classmethod
  def from_csvrow(cls, csvrow):
    ''' Construct a `TaggedEntity` from a CSV row like that from
        `TaggedEntity.csvrow`, being `unixtime,id,name,tags...`.
    '''
    with Pfx("%s.from_csvrow", cls.__name__):
      te_unixtime, te_id, te_name = csvrow[:3]
      tags = TagSet()
      for i, csv_value in enumerate(csvrow[3:], 3):
        with Pfx("field %d %r", i, csv_value):
          tag = Tag.from_string(csv_value)
          tags.add(tag)
      return cls(id=te_id, name=te_name, unixtime=te_unixtime, tags=tags)

  @property
  def csvrow(self):
    ''' This `TaggedEntity` as a list useful to a `csv.writer`.
        The inverse of `from_csvrow`.
    '''
    return [self.unixtime, self.id, self.name
            ] + [str(tag) for tag in self.tags]

  def format_tagset(self):
    ''' Compute a `TagSet` from the tags
        with additional derived tags.

        This can be converted into an `ExtendedNamespace`
        suitable for use with `str.format_map`
        via the `TagSet`'s `.format_kwargs()` method.

        In addition to the normal `TagSet.ns()` names
        the following additional names are available:
        * `entity.id`: the id of the entity database record
        * `entity.name`: the name of the entity database record, if not `None`
        * `entity.unixtime`: the UNIX timestamp of the entity database record
        * `entity.datetime`: the UNIX timestamp as a UTC `datetime`
    '''
    kwtags = TagSet()
    kwtags.update(self.tags)
    if self.id is not None:
      kwtags.add('entity.id', self.id)
    if self.name is not None:
      kwtags.add('entity.name', self.name)
    kwtags.add('entity.unixtime', self.unixtime)
    dt = unixtime2datetime(self.unixtime)
    kwtags.add('entity.datetime', dt)
    kwtags.add('entity.isotime', dt.isoformat())
    return kwtags

  def format_kwargs(self):
    ''' Format arguments suitable for `str.format_map`.

        This returns an `ExtendedNamespace` from `TagSet.ns()`
        for a computed `TagSet`.

        In addition to the normal `TagSet.ns()` names
        the following additional names are available:
        * `entity.id`: the id of the entity database record
        * `entity.name`: the name of the entity database record, if not `None`
        * `entity.unixtime`: the UNIX timestamp of the entity database record
        * `entity.datetime`: the UNIX timestamp as a UTC `datetime`
    '''
    kwtags = self.format_tagset()
    kwtags['tags'] = str(kwtags)
    # convert the TagSet to an ExtendedNamespace
    kwargs = kwtags.format_kwargs()
    return kwargs

class TaggedEntity(namedtuple('TaggedEntity', 'id name unixtime tags'),
                   TaggedEntityMixin):
  ''' A `namedtuple` entity record with its `Tag`s.

      This is a common representation of some tagged entity,
      and also is the intermediary form used by the `cs.fstags` and
      `cs.sqltags` import/export CSV format.

      The `id` column has domain specific use.
      For `cs.sqltags` the `id` attribute will be the database row id.
      For `cs.fstags` the `id` attribute will be `None`.
      It is available for other domains as an arbitrary identifier/key value,
      should that be useful.
  '''

  def set(self, tag_name, value, *, verbose=None):
    ''' Set a tag on `self.tags`.
    '''
    self.tags.set(tag_name, value, verbose=verbose)

  def discard(self, tag_name, value=None, *, verbose=None):
    ''' Discard a tag from `self.tags`.
    '''
    self.discard(tag_name, value, verbose=verbose)
