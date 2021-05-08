#!/usr/bin/env python3
#
# pylint: disable=too-many-lines

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
        >>> tags.add(subtopic)
        >>> # Tags have nice repr() and str()
        >>> subtopic
        Tag(name='subtopic',value='ontologies',ontology=None)
        >>> print(subtopic)
        subtopic=ontologies
        >>> # TagSets also have nice repr() and str()
        >>> tags
        TagSet:{'blue': None, 'topic': 'tagging', 'subtopic': 'ontologies'}
        >>> print(tags)
        blue subtopic=ontologies topic=tagging
        >>> tags2 = TagSet({'a': 1}, b=3, c=[1,2,3], d='dee')
        >>> tags2
        TagSet:{'a': 1, 'b': 3, 'c': [1, 2, 3], 'd': 'dee'}
        >>> print(tags2)
        a=1 b=3 c=[1,2,3] d=dee
        >>> # since you can print a TagSet to a file as a line of text
        >>> # you can get it back from a line of text
        >>> TagSet.from_line('a=1 b=3 c=[1,2,3] d=dee')
        TagSet:{'a': 1, 'b': 3, 'c': [1, 2, 3], 'd': 'dee'}
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
        >>> # test for subtopic=libraries
        >>> subtopic2 = Tag('subtopic', 'libraries')
        >>> subtopic2 in tags
        False

== Ontologies ==

`Tag`s and `TagSet`s suffice to apply simple annotations to things.
However, an ontology brings meaning to those annotations.

See the `TagsOntology` class for implementation details,
access methods and more examples.

Consider a record about a movie, with this `TagSet`:

    title="Avengers Assemble"
    series="Avengers (Marvel)"
    cast={"Scarlett Johansson":"Black Widow (Marvel)"}

where we have the movie title,
a name for the series in which it resides,
and a cast as an association of actors with roles.

An ontology lets us associate implied types and metadata with these values.

Here's an example ontology supporting the above `TagSet`:

    type.cast type=dict key_type=person member_type=character description="members of a production"
    type.character description="an identified member of a story"
    type.series type=str
    meta.character.marvel.black_widow type=character names=["Natasha Romanov"]
    meta.person.scarlett_johansson fullname="Scarlett Johansson" bio="Known for Black Widow in the Marvel stories."

The type information for a `cast`
is defined by the ontology entry named `type.cast`,
which tells us that a `cast` `Tag` is a `dict`,
whose keys are of type `person`
and whose values are of type `character`.
(The default type is `str`.)

To find out the underlying type for a `character`
we look that up in the ontology in turn;
because it does not have a specified `type` `Tag`, it it taken to be a `str`.

Having the types for a `cast`,
it is now possible to look up the metadata for the described cast members.

The key `"Scarlett Johansson"` is a `person`
(from the type definition of `cast`).
The ontology entry for her is named `meta.person.scarlett_johansson`
which is computed as:
* `meta`: the name prefix for metadata entries
* `person`: the type name
* `scarlett_johansson`: obtained by downcasing `"Scarlett Johansson"`
  and replacing whitespace with an underscore.
  The full conversion process is defined
  by the `TagsOntology.value_to_tag_name` function.

The key `"Black Widow (Marvel)"` is a `character`
(again, from the type definition of `cast`).
The ontology entry for her is named `meta.character.marvel.black_widow`
which is computed as:
* `meta`: the name prefix for metadata entries
* `character`: the type name
* `marvel.black_widow`: obtained by downcasing `"Black Widow (Marvel)"`,
  replacing whitespace with an underscore,
  and moving a bracketed suffix to the front as an unbracketed prefix.
  The full conversion process is defined
  by the `TagsOntology.value_to_tag_name` function.

== Format Strings ==

While you can just use `str.format_map` as shown above
for the directvalues in a `TagSet`
(and some command line tools like `fstags` use this in output format specifications
you can also use `TagSet`s in format strings.

There is a `TagSet.ns()` method which constructs
an enhanced type of `SimpleNamespace`
from the tags in the set
which allows convenient dot notation use in format strings,
for example:

      tags = TagSet(colour='blue', labels=['a','b','c'], size=9, _ontology=ont)
      ns = tags.ns()
      print(f'colour={ns.colour}, info URL={ns.colour._meta.url}')
      colour=blue, info URL=https://en.wikipedia.org/wiki/Blue

There is a detailed run down of this in the `TagSetNamespace` docstring below.
'''

from abc import ABC, abstractmethod
from collections import defaultdict, namedtuple
from datetime import date, datetime
import errno
import fnmatch
from fnmatch import fnmatchcase
from getopt import GetoptError
from json import JSONEncoder, JSONDecoder
from json.decoder import JSONDecodeError
import os
from os.path import dirname, isdir as isdirpath
import re
from threading import Lock
import time
from types import SimpleNamespace
from uuid import UUID
from icontract import ensure, require
from typeguard import typechecked
from cs.cmdutils import BaseCommand
from cs.dateutils import UNIXTimeMixin
from cs.deco import decorator
from cs.edit import edit_strings, edit as edit_lines
from cs.fileutils import shortpath
from cs.lex import (
    cropped_repr, cutprefix, cutsuffix, get_dotted_identifier, get_nonwhite,
    is_dotted_identifier, is_identifier, skipwhite, lc_, titleify_lc,
    FormatableMixin
)
from cs.logutils import warning, error, ifverbose
from cs.mappings import AttrableMappingMixin, PrefixedMappingProxy
from cs.obj import SingletonMixin
from cs.pfx import Pfx, pfx, pfx_method, XP
from cs.py3 import date_fromisoformat, datetime_fromisoformat
from cs.resources import MultiOpenMixin
from cs.threads import locked_property

__version__ = '20210428-post'

DISTINFO = {
    'keywords': ["python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.cmdutils>=20210404',
        'cs.dateutils',
        'cs.deco',
        'cs.edit',
        'cs.fileutils',
        'cs.lex',
        'cs.logutils',
        'cs.mappings',
        'cs.obj>=20200716',
        'cs.pfx',
        'cs.py3',
        'cs.resources',
        'cs.threads',
        'icontract',
        'typeguard',
    ],
}

EDITOR = os.environ.get('TAGSET_EDITOR') or os.environ.get('EDITOR')

@decorator
def tag_or_tag_value(func, no_self=False):
  ''' A decorator for functions or methods which may be called as:

          func(name, [value])

      or as:

          func(Tag, [None])

      The optional decorator argument `no_self` (default `False`)
      should be supplied for plain functions
      as they have no leading `self` parameter to accomodate.

      Example:

          @tag_or_tag_value
          def add(self, tag_name, value, *, verbose=None):

      This defines a `.add()` method
      which can be called with `name` and `value`
      or with single `Tag`like object
      (something with `.name` and `.value` attributes),
      for example:

          tags = TagSet()
          ....
          tags.add('colour', 'blue')
          ....
          tag = Tag('size', 9)
          tags.add(tag)
  '''

  if no_self:

    # pylint: disable=keyword-arg-before-vararg
    def accept_tag_or_tag_value(name, value=None, *a, **kw):
      ''' Plain function flavour of `tag_or_tag_value`,
          accepting `(name,value=None,...)`.
      '''
      if not isinstance(name, str):
        if value is not None:
          raise ValueError(
              "name is not a str (%s) and value is not None (%s)" %
              (type(name), type(value))
          )
        name, value = name.name, name.value
      return func(name, value, *a, **kw)
  else:

    # pylint: disable=keyword-arg-before-vararg
    def accept_tag_or_tag_value(self, name, value=None, *a, **kw):
      ''' Method flavour of `tag_or_tag_value`,
          accepting `(self,name,value=None,...)`.
      '''
      if not isinstance(name, str):
        if value is not None:
          raise ValueError(
              "name is not a str (%s) and value is not None (%s)" %
              (type(name), type(value))
          )
        name, value = name.name, name.value
      return func(self, name, value, *a, **kw)

  accept_tag_or_tag_value.__name__ = "@accept_tag_or_tag_value(%s)" % (
      func.__name__,
  )
  accept_tag_or_tag_value.__doc__ = func.__doc__
  return accept_tag_or_tag_value

@pfx
def as_unixtime(tag_value):
  ''' Convert a tag value to a UNIX timestamp.

      This accepts `int`, `float` (already a timestamp)
      and `date` or `datetime`
      (use `datetime.timestamp() for a nonnaive `datetime`,
      otherwise `time.mktime(tag_value.time_tuple())`,
      which assumes the local time zone).
  '''
  if isinstance(tag_value, (date, datetime)):
    if isinstance(tag_value, datetime) and tag_value.tzinfo is not None:
      # nonnaive datetime
      return tag_value.timestamp()
      # plain date or naive datetime: pretend it is localtime
    return time.mktime(tag_value.timetuple())
  if isinstance(tag_value, (int, float)):
    return float(tag_value)
  raise ValueError(
      "requires an int, float, date or datetime, got %s:%r" %
      (type(tag_value), tag_value)
  )

class TagSetFormatter(Formatter):
  ''' A `string.Formatter` subclass for format strings based on a `TagSet`.
  '''

  @pfx_method
  def get_field(self, field_name, a, tags):
    ''' Get the object referenced by the field text `field_name`.
    '''
    assert not a
    with Pfx("field_name=%r: tags=%s", field_name, tags):
      tag_name, offset = get_dotted_identifier(field_name)
      if not tag_name:
        # not a tag_name, pass to the superclass
        return super().get_field(field_name, a, tags)
      # obtain a view of the tags at tag_name
      subtags = TagSetPrefixView(tags, tag_name)
      if offset == len(field_name):
        # no further dereferencing - return the subtags
        return subtags, field_name
      with Pfx("tag_name %r", tag_name):
        # need to dereference the named Tag's value
        tag_value = tags[tag_name]
        return self.get_subfield(tag_value, field_name[offset:])

  @pfx_method(with_args=[0])
  def get_subfield(self, value, subfield_text: str):
    ''' Resolve `value` against `subfield_text`,
        the remaining field text after the term which resolved to `value`.

        For example, a format `{name.blah[0]}`
        has the field text `name.blah[0]`.
        A `get_field` implementation might initially
        resolve `name` to some value,
        leaving `.blah[0]` as the `subfield_text`.
        This method supports taking that value
        and resolving it against the remaining text `.blah[0]`.

        For generality, if `subfield_text` is the empty string
        `value` is returned unchanged.
    '''
    if subfield_text == '':
      return value
    subfield_fmt = f'{{value{subfield_text}}}'
    subfield_map = {'value': value}
    with Pfx("%r.format_map(%r)", subfield_fmt, subfield_map):
      return subfield_fmt.format_map(subfield_map)

  @pfx_method
  def get_value(self, key, args, kwargs):
    ''' Get the object with index `key`.
    '''
    with Pfx("key=%r", key):
      assert not args
      return super().get_value(key, args, kwargs)

  @pfx_method
  def convert_field(self, value, conversion):
    ''' Convert a value.
    '''
    with Pfx("conversion=%r", conversion):
      return super().convert_field(value, conversion)

  @pfx_method
  def format_field(self, value, format_spec):
    with Pfx("format_spec=%r", format_spec):
      return super().format_field(value, format_spec)

class TagSet(dict, UNIXTimeMixin, FormatableMixin, AttrableMappingMixin):
  ''' A setlike class associating a set of tag names with values.

      This actually subclasses `dict`, so a `TagSet` is a direct
      mapping of tag names to values.
      It accepts attribute access to simple tag values when they
      do not conflict with the class methods;
      the reliable method is normal item access.

      *NOTE*: iteration yields `Tag`s, not dict keys.

      Also note that all the `Tags` from `TagSet`
      share its ontology.

      Subclasses should override the `set` and `discard` methods;
      the `dict` and mapping methods
      are defined in terms of these two basic operations.

      `TagSet`s have a few special properties:
      * `id`: a domain specific identifier;
        this may reasonably be `None` for entities
        not associated with database rows;
        the `cs.sqltags.SQLTags` class associates this
        with the database row id.
      * `name`: the entity's name;
        a read only alias for the `'name'` `Tag`.
        The `cs.sqltags.SQLTags` class defines "log entries"
        as `TagSet`s with no `name`.
      * `unixtime`: a UNIX timestamp,
        a `float` holding seconds since the UNIX epoch
        (midnight, 1 January 1970 UTC).
        This is typically the row creation time
        for entities associated with database rows.

      Because ` TagSet` subclasses `cs.mappings.AttrableMappingMixin`
      you can also access tag values as attributes
      provided that they do conflict with instance attributes
      or class methods or properties.
      The `TagSet` class defines the class attribute `ATTRABLE_MAPPING_DEFAULT`
      as `None` which causes attribute access to return `None`
      for missing tag names.
      This supports code like:

          if tags.title:
              # use the title in something
          else:
              # handle a missing title tag
  '''

  # arrange to return None for missing mapping attributes
  # supporting tags.foo being None if there is no 'foo' tag
  ATTRABLE_MAPPING_DEFAULT = None

  @pfx_method
  @require(
      lambda _ontology: _ontology is None or
      isinstance(_ontology, TagsOntology)
  )
  def __init__(self, *a, _id=None, _ontology=None, **kw):
    ''' Initialise the `TagSet`.

        Parameters:
        * positional parameters initialise the `dict`
          and are passed to `dict.__init__`
        * `_id`: optional identity value for databaselike implementations
        * `_ontology`: optional `TagsOntology to use for this `TagSet`
        * other alphabetic keyword parameters are also used to initialise the
          `dict` and are passed to `dict.__init__`
    '''
    dict_kw = {}
    okw = {}
    for k, v in kw.items():
      if k and k[0].isalpha() and is_identifier(k):
        dict_kw[k] = v
      else:
        okw[k] = v
    if okw:
      raise ValueError("unrecognised keywords: %r" % (okw,))
    super().__init__(*a, **dict_kw)
    self.__dict__.update(id=_id, ontology=_ontology, modified=False)

  def __str__(self):
    ''' The `TagSet` suitable for writing to a tag file.
    '''
    return ' '.join(map(str, sorted(self)))

  def __repr__(self):
    return "%s:%s" % (type(self).__name__, dict.__repr__(self))

  def __getattr__(self, attr):
    ''' Support access to dotted name attributes
        if `attr` is not found via the superclass `__getattr__`.

        This is done by returning a subtags of those tags
        commencing with `attr+'.'`.

        Example:

            >>> tags=TagSet(a=1,b=2)
            >>> tags.a
            1
            >>> tags.c
            >>> tags['c.z']=9
            >>> tags['c.x']=8
            >>> tags
            TagSet:{'a': 1, 'b': 2, 'c.z': 9, 'c.x': 8}
            >>> tags.c
            TagSet:{'z': 9, 'x': 8}
            >>> tags.c.z
            9

        However, this is not supported when there is a tag named `'c'`
        because `tags.c` has to return the `'c'` tag value:

            >>> tags=TagSet(a=1,b=2,c=3)
            >>> tags.a
            1
            >>> tags.c
            3
            >>> tags['c.z']=9
            >>> tags.c.z
            Traceback (most recent call last):
              File "<stdin>", line 1, in <module>
            AttributeError: 'int' object has no attribute 'z'

    '''
    try:
      return self[attr]
    except KeyError:
      # magic dotted name access to foo.bar if there are keys
      # starting with "attr."
      if attr and attr[0].isalpha():
        attr_ = attr + '.'
        if any(map(lambda k: k.startswith(attr_) and k > attr_, self.keys())):
          return self.subtags(attr)
      try:
        super_getattr = super().__getattr__
      except AttributeError:
        raise AttributeError(type(self).__name__ + '.' + attr)
      return super_getattr(attr)

  def __setattr__(self, attr, value):
    ''' Attribute based `Tag` access.

        If `attr` is in `self.__dict__` then that is updated,
        supporting "normal" attributes set on the instance.
        Otherwise the `Tag` named `attr` is set to `value`.

        The `__init__` methods of subclasses should do something like this
        (from `TagSet.__init__`)
        to set up the ordinary instance attributes
        which are not to be treated as `Tag`s:

            self.__dict__.update(id=_id, ontology=_ontology, modified=False)
    '''
    if attr in self.__dict__:
      self.__dict__[attr] = value
    else:
      self[attr] = value

  @classmethod
  def from_line(cls, line, offset=0, *, ontology=None, verbose=None):
    ''' Create a new `TagSet` from a line of text.
    '''
    tags = cls(_ontology=ontology)
    offset = skipwhite(line, offset)
    while offset < len(line):
      tag, offset = Tag.parse(line, offset, ontology=ontology)
      tags.add(tag, verbose=verbose)
      offset = skipwhite(line, offset)
    return tags

  def __contains__(self, tag):
    if isinstance(tag, str):
      return super().__contains__(tag)
    for mytag in self:
      if mytag.matches(tag):
        return True
    return False

  def tag(self, tag_name, prefix=None, ontology=None):
    ''' Return a `Tag` for `tag_name`, or `None` if missing.
    '''
    try:
      value = self[tag_name]
    except KeyError:
      return None
    return Tag(
        prefix + '.' + tag_name if prefix else tag_name,
        value,
        ontology=ontology or self.ontology,
    )

  def tag_metadata(self, tag_name, prefix=None, ontology=None, convert=None):
    ''' Return a list of the metadata for the `Tag` named `tag_name`,
        or an empty list if the `Tag` is missing.
    '''
    tag = self.tag(tag_name, prefix=prefix, ontology=ontology)
    return tag.metadata(
        ontology=ontology, convert=convert
    ) if tag is not None else []

  def as_tags(self, prefix=None, ontology=None):
    ''' Yield the tag data as `Tag`s.
    '''
    if ontology is None:
      ontology = self.ontology
    for tag_name in self.keys():
      yield self.tag(tag_name, prefix=prefix, ontology=ontology)

  __iter__ = as_tags

  def as_dict(self):
    ''' Return a `dict` mapping tag name to value.
    '''
    return dict(self)

  def __setitem__(self, tag_name, value):
    self.set(tag_name, value)

  @tag_or_tag_value
  def set(self, tag_name, value, *, verbose=None):
    ''' Set `self[tag_name]=value`.
        If `verbose`, emit an info message if this changes the previous value.
    '''
    self.modified = True
    if verbose is None or verbose:
      old_value = self.get(tag_name)
      if old_value is not value and old_value != value:
        # report different values
        tag = Tag(tag_name, value, ontology=self.ontology)
        msg = (
            "+ %s" % (tag,) if old_value is None else "+ %s (was %s)" %
            (tag, old_value)
        )
        ifverbose(verbose, msg)
    super().__setitem__(tag_name, value)

  # "set" mode
  add = set

  def __delitem__(self, tag_name):
    if tag_name not in self:
      raise KeyError(tag_name)
    self.discard(tag_name)

  @tag_or_tag_value
  def discard(self, tag_name, value, *, verbose=None):
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

  def update(self, other=None, *, prefix=None, verbose=None, **kw):
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
    if items is not None:
      for item in items:
        try:
          name, value = item
        except ValueError:
          name, value = item.name, item.value
        if prefix:
          name = prefix + '.' + name
        self.set(name, value, verbose=verbose)
    for name, value in kw.items():
      if prefix:
        name = prefix + '.' + name
      self.set(name, value, verbose=verbose)

  def subtags(self, prefix):
    ''' Return a new `TagSet` containing tags commencing with `prefix+'.'`
        with the key prefixes stripped off.

        Example:

            >>> tags = TagSet({'a.b':1, 'a.d':2, 'c.e':3})
            >>> tags.subtags('a')
            TagSet:{'b': 1, 'd': 2}
    '''
    prefix_ = prefix + '.'
    return TagSet(
        {
            cutprefix(k, prefix_): v
            for k, v in self.items()
            if k.startswith(prefix_)
        }
    )

  @property
  def name(self):
    ''' Read only `name` property, `None` if there is no `'name'` tag.
    '''
    return self.get('name')

  @property
  def unixtime(self):
    ''' `unixtime` property, autosets to `time.time()` if accessed.
    '''
    ts = self.get('unixtime')
    if ts is None:
      self.unixtime = ts = time.time()
    return ts

  @unixtime.setter
  @typechecked
  def unixtime(self, new_unixtime: float):
    ''' Set the `unixtime`.
    '''
    self['unixtime'] = new_unixtime

  @pfx_method
  def ns(self):
    ''' Return a `TagSetNamespace` for this `TagSet`.

        This has many convenience facilities for use in format strings.
    '''
    return TagSetNamespace.from_tagset(self)

  format_kwargs = ns

  def edit(self, editor=None, verbose=None):
    ''' Edit this `TagSet`.
    '''
    if editor is None:
      editor = EDITOR
    lines = (
        ["# Edit TagSet.", "# One tag per line."] +
        list(map(str, sorted(self)))
    )
    new_lines = edit_lines(lines, editor=editor)
    new_values = {}
    for lineno, line in enumerate(new_lines):
      with Pfx("%d: %r", lineno, line):
        line = line.strip()
        if not line or line.startswith('#'):
          continue
        tag = Tag.from_str(line)
        new_values[tag.name] = tag.value
    self.set_from(new_values, verbose=verbose)

  @classmethod
  @pfx_method
  def _from_named_tags_line(cls, line, ontology=None):
    ''' Parse a "name-or-id tags..." line as used by `edit_many()`,
        return the `TagSet`.
    '''
    name, offset = Tag.parse_value(line)
    if offset < len(line) and not line[offset].isspace():
      _, offset2 = get_nonwhite(line, offset)
      name = line[:offset2]
      warning(
          "offset %d: expected whitespace, adjusted name to %r", offset, name
      )
      offset = offset2
    if offset < len(line) and not line[offset].isspace():
      warning("offset %d: expected whitespace", offset)
    tags = TagSet.from_line(line, offset, ontology=ontology)
    if 'name' in tags:
      warning("discard explicit tag name=%r", tags.name)
      tags.discard('name')
    return name, tags

  @classmethod
  @pfx_method
  def edit_many(cls, tes, editor=None, verbose=True):
    ''' Edit an iterable of `TagSet`s.
        Return a list of `(old_name,new_name,TagSet)` for those which were modified.

        This function supports modifying both `name` and `Tag`s.
    '''
    if editor is None:
      editor = EDITOR
    te_map = {te.name or te.id: te for te in tes}
    assert all(isinstance(k, (str, int)) for k in te_map.keys()), \
        "not all entities have str or int keys: %r" % list(te_map.keys())
    lines = list(
        map(
            lambda te: ' '.join(
                [Tag.transcribe_value(te.name or te.id)] + [
                    str(te.tag(tag_name))
                    for tag_name in te.keys()
                    if tag_name != 'name'
                ]
            ), te_map.values()
        )
    )
    changes = edit_strings(lines, editor=editor)
    changed_tes = []
    for old_line, new_line in changes:
      old_name, _ = cls._from_named_tags_line(old_line)
      assert isinstance(old_name, (str, int))
      with Pfx("%r", old_name):
        te = te_map[old_name]
        new_name, new_tags = cls._from_named_tags_line(new_line)
        te.set_from(new_tags, verbose=verbose)
        changed_tes.append((old_name, new_name, te))
    return changed_tes

  @classmethod
  def from_csvrow(cls, csvrow):
    ''' Construct a `TagSet` from a CSV row like that from
        `TagSet.csvrow`, being `unixtime,id,name,tags...`.
    '''
    with Pfx("%s.from_csvrow", cls.__name__):
      te_unixtime, te_id, te_name = csvrow[:3]
      tags = TagSet()
      for i, csv_value in enumerate(csvrow[3:], 3):
        with Pfx("field %d %r", i, csv_value):
          tag = Tag.from_str(csv_value)
          tags.add(tag)
      return cls(id=te_id, name=te_name, unixtime=te_unixtime, tags=tags)

  @property
  def csvrow(self):
    ''' This `TagSet` as a list useful to a `csv.writer`.
        The inverse of `from_csvrow`.
    '''
    return [self.unixtime, self.id, self.name
            ] + [str(tag) for tag in self if tag.name != 'name']

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

  @require(
      lambda ontology: ontology is None or isinstance(ontology, TagsOntology)
  )
  def __new__(cls, name, value=None, *, ontology=None):
    # simple case: name is a str: make a new Tag
    if isinstance(name, str):
      # (name[,value[,ontology]]) => Tag
      return super().__new__(cls, name, value, ontology)
    # name should be taglike
    if value is not None:
      raise ValueError(
          "name(%s) is not a str, value must be None" % (type(name).__name__,)
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
      (UUID, UUID, str),
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
    try:
      value_s = self.transcribe_value(value)
    except TypeError:
      value_s = str(value)
    return name + '=' + value_s

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
  def from_str(cls, s, offset=0, ontology=None):
    ''' Parse a `Tag` definition from `s` at `offset` (default `0`).
    '''
    with Pfx("%s.from_str(%r[%d:],...)", cls.__name__, s, offset):
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

  @tag_or_tag_value
  def matches(self, tag_name, value):
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
    with Pfx("%s.parse(%s)", cls.__name__, cropped_repr(s[offset:])):
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

  # pylint: disable=too-many-branches
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
          else:
            break
        if nw_value is not None:
          # special format found
          value = nw_value
          offset = nw_offset
        else:
          # decode as plain JSON data
          value_part = s[offset:]
          try:
            value, suboffset = cls.JSON_DECODER.raw_decode(value_part)
          except JSONDecodeError as e:
            raise ValueError(
                "offset %d: raw_decode(%r): %s" % (offset, value_part, e)
            ) from e
          offset += suboffset
    return value, offset

  @property
  @pfx_method(use_str=True)
  def typedata(self):
    ''' The defining `TagSet` for this tag's name.

        This is how its type is defined,
        and is obtained from:
        `self.ontology['type.'+self.name]`

        For example, a `Tag` `colour=blue`
        gets its type information from the `type.colour` entry in an ontology.
    '''
    ont = self.ontology
    if ont is None:
      warning("%s:%r: no ontology, returning None", type(self), self)
      return None
    return ont.type(self.name)

  @property
  @pfx_method(use_str=True)
  def key_typedata(self):
    ''' The typedata definition for this `Tag`'s keys.

        This is for `Tag`s which store mappings,
        for example a movie cast, mapping actors to roles.

        The name of the member type comes from
        the `key_type` entry from `self.typedata`.
        That name is then looked up in the ontology's types.
    '''
    typedata = self.typedata
    if typedata is None:
      return None
    key_type = typedata.get('key_type')
    if key_type is None:
      return None
    ont = self.ontology
    return ont.type(key_type)

  @pfx_method(use_str=True)
  def key_metadata(self, key):
    ''' Return the metadata definition for `key`.

        The metadata `TagSet` is obtained from the ontology entry
        'meta.`*type*`.`*key_tag_name*
        where *type* is the `Tag`'s `key_type`
        and *key_tag_name* is the key converted
        into a dotted identifier by `TagsOntology.value_to_tag_name`.
    '''
    typedata = self.typedata
    if typedata is None:
      return None
    key_type = typedata.get('key_type')
    if key_type is None:
      return None
    ont = self.ontology
    key_metadata_name = 'meta.' + key_type + '.' + ont.value_to_tag_name(key)
    return ont[key_metadata_name]

  @property
  @pfx_method(use_str=True)
  def member_typedata(self):
    ''' The typedata definition for this `Tag`'s members.

        This is for `Tag`s which store mappings or sequences,
        for example a movie cast, mapping actors to roles,
        or a list of scenes.

        The name of the member type comes from
        the `member_type` entry from `self.typedata`.
        That name is then looked up in the ontology's types.
    '''
    typedata = self.typedata
    if typedata is None:
      return None
    member_type = typedata.get('member_type')
    if member_type is None:
      return None
    ont = self.ontology
    return ont.type(member_type)

  @pfx_method(use_str=True)
  def member_metadata(self, member_key):
    ''' Return the metadata definition for self[member_key].

        The metadata `TagSet` is obtained from the ontology entry
        'meta.`*type*`.`*member_tag_name*
        where *type* is the `Tag`'s `member_type`
        and *member_tag_name* is the member value converted
        into a dotted identifier by `TagsOntology.value_to_tag_name`.
    '''
    typedata = self.typedata
    if typedata is None:
      return None
    member_type = typedata.get('member_type')
    if member_type is None:
      return None
    ont = self.ontology
    value = self.value[member_key]
    member_metadata_name = 'meta.' + member_type + '.' + ont.value_to_tag_name(
        value
    )
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

        By contrast, the tag `cast={"Scarlett Johansson":"Black Widow (Marvel)"}`
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
        `person.scarlett_johansson`
        and `character.marvel.black_widow` respectively.
    '''
    typedata = self.typedata
    if typedata is None:
      return None
    type_name = typedata.type
    if type_name is None:
      type_name = self.ontology.value_to_tag_name(self.name)
    return type_name

  @property
  @pfx_method(use_str=True)
  def basetype(self):
    ''' The base type name for this tag.
        Returns `None` if there is no ontology.

        This calls `TagsOntology.basetype(self.ontology,self.type)`.
    '''
    ont = self.ontology
    if ont is None:
      warning("no ontology, returning None")
      return None
    return ont.basetype(self.type)

  def metadata(self, ontology=None, convert=None):
    ''' Fetch the metadata information about this specific tag value,
        derived through the `ontology` from the tag name and value.
        The default `ontology` is `self.onotology`.

        For a scalar type (`int`, `float`, `str`) this is the ontology `TagSet`
        for `self.value`.

        For a sequence (`list`) this is a list of the metadata
        for each member.

        For a mapping (`dict`) this is mapping of `key->value_metadata`.
    '''
    ont = ontology or self.ontology
    assert ont, "ont is false: %r" % (ont,)
    basetype = self.basetype
    if basetype == 'list':
      member_type = self.member_type
      return [
          ont.value_metadata(member_type, value, convert=convert)
          for value in self.value
      ]
    if basetype == 'dict':
      member_type = self.member_type
      return {
          key: ont.value_metadata(member_type, value)
          for key, value in self.value.items()
      }
    return ont.value_metadata(self.name, self.value)

  @property
  def meta(self):
    ''' The `Tag` metadata derived from the `Tag`'s ontology.
    '''
    return self.metadata()

  @property
  def key_type(self):
    ''' The type name for members of this tag.

        This is required if `.value` is a mapping.
    '''
    try:
      return self.typedata['key_type']
    except KeyError:
      raise AttributeError('key_type')  # pylint: disable=raise-missing-from

  @property
  @pfx_method
  def member_type(self):
    ''' The type name for members of this tag.

        This is required if `.value` is a sequence or mapping.
    '''
    try:
      return self.typedata['member_type']
    except KeyError:
      raise AttributeError('member_type')  # pylint: disable=raise-missing-from

class TagSetCriterion(ABC):
  ''' A testable criterion for a `TagSet`.
  '''

  # list of TagSetCriterion classes
  # whose .parse methods are used by .parse
  CRITERION_PARSE_CLASSES = []

  @abstractmethod
  @typechecked
  def match_tagged_entity(self, te: "TagSet") -> bool:
    ''' Apply this `TagSetCriterion` to a `TagSet`.
    '''
    raise NotImplementedError("match")

  @classmethod
  @pfx_method
  def from_str(cls, s):
    ''' Prepare a `TagSetCriterion` from the string `s`.
    '''
    criterion, offset = cls.from_str2(s)
    if offset != len(s):
      raise ValueError("unparsed specification: %r" % (s[offset:],))
    return criterion

  @classmethod
  def from_str2(cls, s, offset=0, delim=None):
    ''' Parse a criterion from `s` at `offset` and return `(TagSetCriterion,offset)`.

        This method recognises an optional leading `'!'` or `'-'`
        indicating negation of the test,
        followed by a criterion recognised by the `.parse` method
        of one of the classes in `cls.CRITERION_PARSE_CLASSES`.
    '''
    offset0 = offset
    if s.startswith('!', offset) or s.startswith('-', offset):
      choice = False
      offset += 1
    else:
      choice = True
    criterion = None
    for crit_cls in cls.CRITERION_PARSE_CLASSES:
      parse_method = crit_cls.parse
      with Pfx("%s.from_str2(%r,offset=%d)", crit_cls.__name__, s, offset):
        try:
          params, offset = parse_method(s, offset, delim)
        except ValueError:
          pass
        else:
          criterion = crit_cls(s[offset0:offset], choice, **params)
          break
    if criterion is None:
      raise ValueError("no criterion parsed at offset %d" % (offset0,))
    return criterion, offset

  @classmethod
  @pfx_method
  def from_any(cls, o):
    ''' Convert some suitable object `o` into a `TagSetCriterion`.

        Various possibilities for `o` are:
        * `TagSetCriterion`: returned unchanged
        * `str`: a string tests for the presence
          of a tag with that name and optional value;
        * an object with a `.choice` attribute;
          this is taken to be a `TagSetCriterion` ducktype and returned unchanged
        * an object with `.name` and `.value` attributes;
          this is taken to be `Tag`-like and a positive test is constructed
        * `Tag`: an object with a `.name` and `.value`
          is equivalent to a positive equality `TagBasedTest`
        * `(name,value)`: a 2 element sequence
          is equivalent to a positive equality `TagBasedTest`
    '''
    tag_based_test_class = getattr(cls, 'TAG_BASED_TEST_CLASS', TagBasedTest)
    if isinstance(o, (cls, TagSetCriterion)):
      # already suitable
      return o
    if isinstance(o, str):
      # parse choice form string
      return cls.from_str(o)
    try:
      name, value = o
    except (TypeError, ValueError):
      if hasattr(o, 'choice'):
        # assume TagBasedTest ducktype
        return o
      try:
        name = o.name
        value = o.value
      except AttributeError:
        pass
      else:
        return tag_based_test_class(
            repr(o), True, tag=Tag(name, value), comparison='='
        )
    else:
      # (name,value) => True TagBasedTest
      return tag_based_test_class(
          repr((name, value)), True, tag=Tag(name, value), comparison='='
      )
    raise TypeError("cannot infer %s from %s:%s" % (cls, type(o), o))

class TagBasedTest(namedtuple('TagBasedTest', 'spec choice tag comparison'),
                   TagSetCriterion):
  ''' A test based on a `Tag`.

      Attributes:
      * `spec`: the source text from which this choice was parsed,
        possibly `None`
      * `choice`: the apply/reject flag
      * `tag`: the `Tag` representing the criterion
      * `comparison`: an indication of the test comparison

      The following comparison values are recognised:
      * `None`: test for the presence of the `Tag`
      * `'='`: test that the tag value equals `tag.value`
      * `'<'`: test that the tag value is less than `tag.value`
      * `'<='`: test that the tag value is less than or equal to `tag.value`
      * `'>'`: test that the tag value is greater than `tag.value`
      * `'>='`: test that the tag value is greater than or equal to `tag.value`
      * `'~/'`: test if the tag value as a regexp is present in `tag.value`
      * '~': test if a matching tag value is present in `tag.value`
  '''

  COMPARISON_FUNCS = {
      '=':
      lambda tag_value, cmp_value: cmp_value is None or tag_value == cmp_value,
      '<=':
      lambda tag_value, cmp_value: tag_value <= cmp_value,
      '<':
      lambda tag_value, cmp_value: tag_value < cmp_value,
      '>=':
      lambda tag_value, cmp_value: tag_value >= cmp_value,
      '>':
      lambda tag_value, cmp_value: tag_value > cmp_value,
      '~/':
      lambda tag_value, cmp_value: re.match(cmp_value, tag_value),
      '~':
      lambda tag_value, cmp_value: (
          fnmatchcase(tag_value, cmp_value) if isinstance(tag_value, str) else
          any(map(lambda value: fnmatchcase(value, cmp_value), tag_value))
      ),
  }

  # These are ordered so that longer operators
  # come before shorter operators which are prefixes
  # so as to recognise '>=' ahead of '>' etc.
  COMPARISON_OPS = sorted(COMPARISON_FUNCS.keys(), key=len, reverse=True)

  def __str__(self):
    return ('' if self.choice else '!') + (
        self.tag.name if self.comparison is None else (
            self.tag.name + self.comparison +
            self.tag.transcribe_value(self.tag.value)
        )
    )

  @classmethod
  @tag_or_tag_value
  def by_tag_value(cls, tag_name, tag_value, *, choice=True, comparison='='):
    ''' Return a `TagBasedTest` based on a `Tag` or `tag_name,tag_value`.
    '''
    tag = Tag(tag_name, tag_value)
    return cls(str(tag), choice, tag, comparison)

  @classmethod
  def parse(cls, s, offset=0, delim=None):
    ''' Parse *tag_name*[{`<`|`<=`|'='|'>='|`>`|'~'}*value*]
        and return `(dict,offset)`
        where the `dict` contains the following keys and values:
        * `tag`: a `Tag` embodying the tag name and value
        * `comparison`: an indication of the test comparison
    '''
    tag_name, offset = get_dotted_identifier(s, offset)
    if not tag_name:
      raise ValueError("no tag_name")
    # end of text?
    if offset == len(s) or s[offset].isspace() or (delim
                                                   and s[offset] in delim):
      # just tag_name present
      return dict(tag=Tag(tag_name), comparison='='), offset

    comparison = None
    for cmp_op in cls.COMPARISON_OPS:
      if s.startswith(cmp_op, offset):
        comparison = cmp_op
        break
    if comparison is None:
      raise ValueError("expected one of %r" % (cls.COMPARISON_OPS,))
    # tag_name present with specific value
    offset += len(comparison)
    if comparison == '~':
      value = s[offset:]
      offset = len(s)
    else:
      value, offset = Tag.parse_value(s, offset)
    return dict(tag=Tag(tag_name, value), comparison=comparison), offset

  @typechecked
  def match_tagged_entity(self, te: "TagSet") -> bool:
    ''' Test against the `Tag`s in `tags`.

        *Note*: comparisons when `self.tag.name` is not in `tags`
        always return `False` (possibly inverted by `self.choice`).
    '''
    tag_name = self.tag.name
    comparison = self.comparison
    if comparison is None:
      result = tag_name in te
    else:
      try:
        tag_value = te[tag_name]
      except KeyError:
        # tag not present, base test fails
        result = False
      else:
        assert tag_value is not None
        cmp_value = self.tag.value
        comparison_test = self.COMPARISON_FUNCS[comparison]
        try:
          result = comparison_test(tag_value, cmp_value)
        except TypeError as e:
          warning(
              "compare tag_value=%r %r cmp_value=%r: %s", tag_value,
              comparison, cmp_value, e
          )
          result = False
    return result if self.choice else not result

TagSetCriterion.CRITERION_PARSE_CLASSES.append(TagBasedTest)
TagSetCriterion.TAG_BASED_TEST_CLASS = TagBasedTest

class ExtendedNamespace(SimpleNamespace):
  ''' Subclass `SimpleNamespace` with inferred attributes
      intended primarily for use in format strings.
      As such it also presents attributes as `[]` elements via `__getitem__`.

      Because [:alpha:]* attribute names
      are reserved for "public" keys/attributes,
      most methods commence with an underscore (`_`).
  '''

  def _public_keys(self):
    return (k for k in self.__dict__ if k and k[0].isalpha())

  def _public_keys_str(self):
    return ','.join(sorted(self._public_keys()))

  def _public_items(self):
    return ((k, v) for k, v in self.__dict__.items() if k and k[0].isalpha())

  def __str__(self):
    ''' Return a visible placeholder, supporting exposing this object
        in a format string so that the user knows there wasn't a value
        at this point in the dotted path.
    '''
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
    ''' Just a stub so that (a) subclasses can call `super().__getattr__`
        and (b) a pathbased `AttributeError` gets raised for better context.
    '''
    raise AttributeError("%s:%s.%s" % (self._path, type(self).__name__, attr))

  @pfx_method
  def __getitem__(self, attr):
    with Pfx("%s[%r]", self._path, attr):
      try:
        value = getattr(self, attr)
      except AttributeError:
        raise KeyError(attr)  # pylint: disable=raise-missing-from
      return value

class TagSetNamespace(ExtendedNamespace):
  ''' A formattable nested namespace for a `TagSet`,
      subclassing `ExtendedNamespace`,
      providing attribute based access to tag data.

      `TagSet`s have a `.ns()` method which returns a `TagSetNamespace`
      derived from that `TagSet`.

      This class exists particularly to help with format strings
      because tools like fstags and sqltags use these for their output formats.
      As such, I wanted to be able to put some expressive stuff
      in the format strings.

      However, this also gets you attribute style access to various
      related values without mucking with format strings.
      For example for some `TagSet` `tags` with a `colour=blue` `Tag`,
      if I set `ns=tags.ns()`:
      * `ns.colour` is itself a namespace based on the `colour `Tag`
      * `ns.colour_s` is the string `'blue'`
      * `ns.colour._tag` is the `colour` `Tag` itself
      If the `TagSet` had an ontology:
      * `ns.colour._meta` is a namespace based on the metadata
        for the `colour` `Tag`

      This provides an assortment of special names derived from the `TagSet`.
      See the docstring for `__getattr__` for the special attributes provided
      beyond those already provided by `ExtendedNamespace.__getattr__`.

      Example with a simple `TagSet`:

          >>> tags = TagSet(colour='blue', labels=['a','b','c'], size=9)
          >>> 'The colour is {colour}.'.format_map(tags)
          'The colour is blue.'
          >>> # the natural way to obtain a TagSetNamespace from a TagSet
          >>> ns = tags.ns()  # returns TagSetNamespace.from_tagset(tags)
          >>> # the ns object has additional computed attributes
          >>> 'The colour tag is {colour._tag}.'.format_map(ns)
          'The colour tag is colour=blue.'
          >>> # also, the direct name for any Tag can be used
          >>> # which returns its value
          >>> 'The colour is {colour}.'.format_map(ns)
          'The colour is blue.'
          >>> 'The colours are {colours}. The labels are {labels}.'.format_map(ns)
          "The colours are ['blue']. The labels are ['a', 'b', 'c']."
          >>> 'The first label is {label}.'.format_map(ns)
          'The first label is a.'

      The same `TagSet` with an ontology:

          >>> ont = TagsOntology({
          ...   'type.colour': TagSet(description="a colour, a hue", type="str"),
          ...   'meta.colour.blue': TagSet(
          ...     url='https://en.wikipedia.org/wiki/Blue',
          ...     wavelengths='450nm-495nm'),
          ... })
          >>> tags = TagSet(colour='blue', labels=['a','b','c'], size=9, _ontology=ont)
          >>> # the colour Tag
          >>> tags.tag('colour')  # doctest: +ELLIPSIS
          Tag(name='colour',value='blue',ontology=TagsOntology<...>)
          >>> # type information about a colour
          >>> tags.tag('colour').type
          'str'
          >>> tags.tag('colour').typedata
          TagSet:{'description': 'a colour, a hue', 'type': 'str'}
          >>> # metadata about this particular colour value
          >>> tags.tag('colour').meta
          TagSet:{'url': 'https://en.wikipedia.org/wiki/Blue', 'wavelengths': '450nm-495nm'}

      Using a namespace view of the Tag, useful for format strings:

          >>> # the TagSet as a namespace for use in format strings
          >>> ns = tags.ns()
          >>> # The namespace .colour node, which has the Tag attached.
          >>> # When there is a Tag attached, the repr is that of the Tag value.
          >>> ns.colour         # doctest: +ELLIPSIS
          'blue'
          >>> # The underlying colour Tag itself.
          >>> ns.colour._tag    # doctest: +ELLIPSIS
          Tag(name='colour',value='blue',ontology=TagsOntology<...>)
          >>> # The str() of a namespace with a ._tag is the Tag value
          >>> # making for easy use in a format string.
          >>> f'{ns.colour}'
          'blue'
          >>> # the type information about the colour Tag
          >>> ns.colour._tag.typedata
          TagSet:{'description': 'a colour, a hue', 'type': 'str'}
          >>> # The metadata: a TagSetNamespace for the metadata TagSet
          >>> ns.colour._meta   # doctest: +ELLIPSIS
          TagSetNamespace(_path='.', _pathnames=(), _ontology=None, wavelengths='450nm-495nm', url='https://en.wikipedia.org/wiki/Blue')
          >>> # the _meta.url is itself a namespace with a ._tag for the URL
          >>> ns.colour._meta.url   # doctest: +ELLIPSIS
          'https://en.wikipedia.org/wiki/Blue'
          >>> # but it formats nicely because it has a ._tag
          >>> f'colour={ns.colour}, info URL={ns.colour._meta.url}'
          'colour=blue, info URL=https://en.wikipedia.org/wiki/Blue'
  '''

  @classmethod
  @pfx_method
  def from_tagset(cls, tags, pathnames=None):
    ''' Compute and return a presentation of this `TagSet` as a
        nested `TagSetNamespace`.

        Note that multiple dots in `Tag` names are collapsed;
        for example `Tag`s named '`a.b'`, `'a..b'`, `'a.b.'` and
        `'..a.b'` will all map to the namespace entry `a.b`.
        `Tag`s are processed in reverse lexical order by name, which
        dictates which of the conflicting multidot names takes
        effect in the namespace - the first found is used.
    '''
    assert tags is not None
    if pathnames is None:
      pathnames = []
    ns0 = cls(
        _path='.'.join(pathnames) if pathnames else '.',
        _pathnames=tuple(pathnames),
        _tagset=tags,
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
                    tags.subtags(dotted_subpath), subpath
                )
              ns = subns
          ns._tag = tag
    return ns0

  def __str__(self):
    ''' A `TagSetNamespace` with a `._tag` renders `str(_tag.value)`,
        otherwise `ExtendedNamespace.__str__` is used.
    '''
    tag = self.__dict__.get('_tag')
    if tag is not None:
      return str(tag.value)
    return super().__str__()

  def __repr__(self):
    ''' Return `repr(self._tag.value)` if defined, otherwise use the superclass `__repr__`.
    '''
    tag = self.__dict__.get('_tag')
    if tag is not None:
      return repr(tag.value)
    return super().__repr__()

  def __bool__(self):
    ''' Truthiness: true if the `._tagset` is not empty.
    '''
    return bool(self._tagset)

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

  def _subns(self, attr):
    ''' Create a subnamespace for `attr` as for `ExtendedNamespace`
        and adorn it with `._tag` and `._tagset`.
    '''
    subns = super()._subns(attr)
    overtag = self.__dict__.get('_tag')
    format_placeholder = '{' + self._path + '.' + attr + '}'
    subns._tag = Tag(
        attr,
        format_placeholder,
        ontology=overtag.ontology if overtag else self._tagset.ontology
    )
    subns._tagset = self._tagset.subtags(attr)
    return subns

  @pfx_method
  def __getitem__(self, key):
    ''' If this node has a `._tag` then dereference its `.value`,
        otherwise fall through to the superclass `__getitem__`.
    '''
    if isinstance(key, str):
      try:
        item = getattr(self, key)
      except (AttributeError, TypeError) as e:
        warning(
            "[%s:%r]: %s, fall back to super().__getitem__",
            type(key).__name_, key, e
        )
      else:
        return item
    super_item = super().__getitem__(key)
    return super_item

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

  # pylint: disable=too-many-locals
  # pylint: disable=too-many-return-statements
  # pylint: disable=too-many-branches
  # pylint: disable=too-many-statements
  @pfx_method
  def __getattr__(self, attr):
    ''' Look up an indirect node attribute,
        whose value is inferred from another.

        The following attribute names and forms are supported:
        * `_keys`: the keys of the value
          for the `Tag` associated with this node;
          meaningful if `self._tag.value` has a `keys` method
        * `_meta`: a namespace containing the meta information
          for the `Tag` associated with this node:
          `self._tag.meta.ns()`
        * `_type`: a namespace containing the type definition
          for the `Tag` associated with this node:
          `self._tag.typedata.ns()`
        * `_values`: the values within the `Tag.value`
          for the `Tag` associated with this node
        * *baseattr*`_lc`: lowercase and titled forms.
          If *baseattr* exists,
          return its value lowercased via `cs.lex.lc_()`.
          Conversely, if *baseattr* is required
          and does not directly exist
          but its *baseattr*`_lc` form *does*,
          return the value of *baseattr*`_lc`
          titlelified using `cs.lex.titleify_lc()`.
        * *baseattr*`s`, *baseattr*`es`: singular/plural.
          If *baseattr* exists
          return `[self.`*baseattr*`]`.
          Conversely,
          if *baseattr* does not exist but one of its plural attributes does,
          return the first element from the plural attribute.
        * `[:alpha:]*`:
          an identifierish name binds to a stub subnamespace
          so the `{a.b.c.d}` in a format string
          can be replaced with itself to present the undefined name in full.
    '''
    getns = self.__dict__.get
    path = getns('_path')
    tagset = getns('_tagset')
    tag = getns('_tag')
    with Pfx("%s:%s.%s", type(self).__name__, path, attr):
      if tag is not None:
        # local Tag? probe it
        # first, special _* attributes
        if attr == '_type':
          return self._tag.typedata.ns()
        if attr == '_meta':
          return self._tag.meta.ns()
        if attr == '_keys':
          if tag is not None:
            value = tag.value
            try:
              keys = value.keys
            except AttributeError:
              # not found, fall through
              pass
            else:
              return list(keys())
        if attr == '_value':
          if tag is not None:
            return tag.value
        if attr == '_values':
          if tag is not None:
            value = tag.value
            try:
              values = value.values
            except AttributeError:
              # not found, fall through
              pass
            else:
              return list(values())
        # otherwise probe the Tag directly
        try:
          return getattr(tag, attr)
        except AttributeError:
          # not found, fall through
          pass
      # probe local TagSet
      try:
        tagset_value = getattr(tagset, attr)
      except AttributeError:
        pass
      else:
        # TagSet attribute access returns None for missing attributes (Tags)
        if tagset_value is not None:
          return tagset_value
      # end of Tag, Tagset and private/special attributes
      if attr.startswith('_'):
        raise AttributeError(
            "%s: unsupported special attribute .%r" % (type(self), attr)
        )
      # try builtin conversions
      # these are all reductive, so there should be no unbound regress
      for conv_suffix, conv in {
          '_i': int,
          '_s': str,
          '_f': float,
          '_lc': lc_,
          's': lambda value: [value],
          'es': lambda value: [value],
      }.items():
        ur_attr = cutsuffix(attr, '_' + conv_suffix)
        if ur_attr is not attr:
          try:
            ur_value = getattr(self, ur_attr)
          except AttributeError:
            pass
          else:
            with Pfx("%s(.%s=%r)", conv, ur_attr, ur_value):
              ur_value = conv(ur_value)
            return ur_value
      # see if there's a real "attr_lc" attribute to titleify
      attr_lc = attr + '_lc'
      attr_lc_value = getns(attr_lc)
      if attr_lc_value is not None:
        return titleify_lc(attr_lc_value)
      # singular from plural
      for pl_suffix in 's', 'es':
        plural_attr = attr + pl_suffix
        plural_value = self._attr_tag_value(plural_attr)
        if plural_value is not None:
          value0 = plural_value[0]
          return value0
      try:
        # see if the superclass knows this attribute
        return super().__getattr__(attr)
      except AttributeError as e:
        if attr and attr[0].isalpha():
          # "public" attribute name
          # no such attribute, create a placeholder `Tag`
          # for [:alpha:]* names
          # and return a namespace containing it
          subns = self._subns(attr)
          return subns
        raise

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
    return (
        "{%s:%r[%s]}" % (self._ontkey, self._value, self._public_keys_str())
    ).__format__(spec)

class TagSets(MultiOpenMixin, ABC):
  ''' Base class for collections of `TagSet` instances
      such as `cs.fstags.FSTags` and `cs.sqltags.SQLTags`.

      Examples of this include:
      * `cs.fstags.FSTags`: a mapping of filesystem paths to their associated `TagSets`
      * `cs.sqltags.SQLTags`: a mapping of names to `TagSet`s stored in an SQL database

      Subclasses must implement:
      * `default_factory(self,name,**kw)`: as with `defaultdict` this is called as
        from `__missing__` for missing names,
        and also from `add`.
        If set to `None` then `__getitem__` will raise `KeyError`
        for missing names.
        _Unlike_ `defaultdict`, the factory is called with the key `name`
        and any additional keyword parameters.
      * `get(name,default=None)`: return the `TagSet` associated
        with `name`, or `default`.
      * `__setitem__(name,tagset)`: associate a `TagSet`with the key `name`;
        this is called by the `__missing__` method with a newly created `TagSet`.

      Subclasses may reasonably want to define the following:
      * `startup(self)`: allocate any needed resources
        such as database connections
      * `shutdown(self)`: write pending changes to a backing store,
        release resources acquired during `startup`
      * `keys(self)`: return an iterable of names
      * `__len__(self)`: return the number of names
  '''

  _missing = object()

  TagSetClass = TagSet

  def __init__(self, *, ontology=None):
    ''' Initialise the collection.
    '''
    self.ontology = ontology

  def __str__(self):
    return "%s<%s>" % (type(self).__name__, id(self))

  __repr__ = __str__

  def startup(self):
    ''' Allocate any needed resources such as database connections.
    '''

  def shutdown(self):
    ''' Write any pending changes to a backing store,
        release resources allocated during `startup`.
    '''

  def default_factory(self, name: str):
    ''' Create a new `TagSet` named `name`.
    '''
    te = self.TagSetClass(name=name)
    te.ontology = self.ontology
    return te

  @pfx_method(use_str=True)
  def __missing__(self, name: str, **kw):
    ''' Like `dict`, the `__missing__` method may autocreate a new `TagSet`.

        This is called from `__getitem__` if `name` is missing
        and uses the factory `cls.default_factory`.
        If that is `None` raise `KeyError`,
        otherwise call `self.default_factory(name,**kw)`.
        If that returns `None` raise `KeyError`,
        otherwise save the entity under `name` and return the entity.
    '''
    te_factory = self.default_factory
    if te_factory is None:
      raise KeyError(name)
    te = te_factory(name, **kw)
    if te is None:
      raise KeyError(name)
    self[name] = te
    return te

  def add(self, name: str, **kw):
    ''' Return a new `TagSet` associated with `name`,
        which should not already be in use.
    '''
    te = self.get(name, default=self._missing)
    if te is not self._missing:
      raise ValueError("%r: name already present" % (name,))
    return self.default_factory(name, **kw)

  @abstractmethod
  def get(self, name: str, default=None):
    ''' Return the `TagSet` associated with `name`,
        or `default` if there is no such entity.
    '''
    raise NotImplementedError(
        "%s: no .get(name,default=None) method" % (type(self).__name__,)
    )

  def __getitem__(self, name: str):
    ''' Obtain the `TagSet` associated with `name`.

        If `name` is not presently mapped,
        return `self.__missing__(name)`.
    '''
    te = self.get(name, default=self._missing)
    if te is self._missing:
      te = self.__missing__(name)
    return te

  @abstractmethod
  def __setitem__(self, name, te):
    ''' Save `te` in the backend under the key `name`.
    '''
    raise NotImplementedError(
        "%s: no .__setitem__(name,tagset) method" % (type(self).__name__,)
    )

  def __contains__(self, name: str):
    ''' Test whether `name` is present in `self.te_mapping`.
    '''
    missing = object()
    return self.get(name) is not missing

  def __len__(self):
    ''' Return the length of `self.te_mapping`.
    '''
    raise NotImplementedError(
        "%s: no .__len__() method" % (type(self).__name__,)
    )

  def subdomain(self, subname):
    ''' Return a proxy for this `TagSets` for the `name`s
        starting with `subname+'.'`.
    '''
    return TagSetsSubdomain(self, subname)

class TagSetsSubdomain(SingletonMixin, PrefixedMappingProxy):
  ''' A view into a `TagSets` for keys commencing with a prefix.
  '''

  @classmethod
  def _singleton_key(cls, tes, subdomain: str):
    return id(tes), subdomain

  def __init__(self, tes, subdomain: str):
    PrefixedMappingProxy.__init__(self, tes, subdomain + '.')
    self.tes = tes

  @property
  def TAGGED_ENTITY_FACTORY(self):
    ''' The entity factory comes from the parent collection.
    '''
    return self.tes.TAGGED_ENTITY_FACTORY

class TagsOntology(SingletonMixin, TagSets):
  ''' An ontology for tag names.

      This is based around a mapping of names
      to ontological information expressed as a `TagSet`.

      A `cs.fstags.FSTags` uses ontologies initialised from `TagFile`s
      containing ontology mappings.

      There are two main categories of entries in an ontology:
      * types: an entry named `type.{typename}` contains a `TagSet`
        defining the type named `typename`
      * metadata: an entry named `meta.{typename}.{value_key}`
        contains a `TagSet` holding metadata for a value of type {typename}

      Types:

      The type of a `Tag` is nothing more than its `name`.

      The basic types have their Python names: `int`, `float`, `str`, `list`,
      `dict`, `date`, `datetime`.
      You can define subtypes of these for your own purposes,
      for example:

          type.colour type=str description="A hue."

      which subclasses `str`.

      Subtypes of `list` include a `member_type`
      specifying the type for members of a `Tag` value:

          type.scene type=list member_type=str description="A movie scene."

      Subtypes of `dict` include a `key_type` and a `member_type`
      specifying the type for keys and members of a `Tag` value:

          type.cast type=dict key_type=actor member_type=role description="Cast members and their roles."
          type.actor type=person description="An actor's stage name."
          type.person type=str description="A person."
          type.role type=character description="A character role in a performance."
          type.character type=str description="A person in a story."

      Metadata:

      Metadata are `Tag`s describing particular values of a type.
      For example, the metadata for the `Tag` `colour=blue`:

          meta.colour.blue url="https://en.wikipedia.org/wiki/Blue" wavelengths="450nm-495nm"
          meta.actor.scarlett_johansson
          meta.character.marvel.black_widow type=character names=["Natasha Romanov"]

      Accessing type data and metadata:

      A `TagSet` may have a reference to a `TagsOntology` as `.ontology`
      and so also do any of its `Tag`s.
  '''

  # A mapping of base type named to Python types.
  BASE_TYPES = {
      t.__name__: t
      for t in (int, float, str, list, dict, date, datetime)
  }

  @classmethod
  def _singleton_key(cls, te_mapping):
    return id(te_mapping)

  def __init__(self, te_mapping):
    if hasattr(self, 'te_mapping'):
      return
    self.__dict__.update(
        te_mapping=te_mapping,
        default_factory=getattr(
            te_mapping, 'default_factory', lambda name: TagSet(_ontology=self)
        ),
    )

  def __bool__(self):
    ''' Support easy `ontology or some_default` tests,
        since ontologies are broadly optional.
    '''
    return True

  def get(self, name, default=None):
    ''' Proxy `.get` through to `self.te_mapping`.
    '''
    return self.te_mapping.get(name, default)

  def __setitem__(self, name, te):
    ''' Save `te` against the key `name`.
    '''
    self.te_mapping[name] = te

  def type(self, type_name):
    ''' Return the `TagSet` defining the type named `type_name`.
    '''
    return self[self.type_index(type_name)]

  @staticmethod
  @require(lambda type_name: Tag.is_valid_name(type_name))  # pylint: disable=unnecessary-lambda
  def type_index(type_name):
    ''' Return the entry index for the type `type_name`.
    '''
    return 'type.' + type_name

  def types(self):
    ''' Generator yielding defined type names and their defining `TagSet`.
    '''
    for key, tags in self.tagsets.items():
      type_name = cutprefix(key, 'type.')
      if type_name is not key:
        yield type_name, tags

  def type_names(self):
    ''' Generator yielding defined type names.
    '''
    for key in self.tagsets.keys():
      type_name = cutprefix(key, 'type.')
      if type_name is not key:
        yield type_name

  def meta(self, type_name, value):
    ''' Return the metadata `TagSet` for `(type_name,value)`.
    '''
    return self[self.meta_index(type_name, value)]

  @classmethod
  def meta_index(cls, type_name=None, value=None):
    ''' Return the entry index for the metadata for `(type_name,value)`.
    '''
    index = 'meta'
    if type_name is None:
      assert value is None
    else:
      index += '.' + type_name
      if value:
        index += '.' + cls.value_to_tag_name(value)
    return index

  def meta_names(self, type_name=None):
    ''' Generator yielding defined metadata names.

        If `type_name` is specified, yield only the value_names
        for that `type_name`.

        For example, `meta_names('character')`
        on an ontology with a `meta.character.marvel.black_widow`
        would yield `'marvel.black_widow'`
        i.e. only the suffix part for `character` metadata.
    '''
    prefix = self.meta_index(type_name=type_name) + '.'
    for key in self.tagsets.keys(prefix=prefix):
      suffix = cutprefix(key, prefix)
      assert suffix is not key
      yield suffix

  @staticmethod
  @pfx
  @ensure(lambda result: Tag.is_valid_name(result))  # pylint: disable=unnecessary-lambda
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
  def value_metadata(self, type_name, value, convert=None):
    ''' Return a `ValueMetadata` for `type_name` and `value`.
        This provides the mapping between a type's value and its semantics.

        For example,
        if a `TagSet` had a list of characters such as:

            characters=["Captain America (Marvel)","Black Widow (Marvel)"]

        then these values could be converted to the dotted identifiers
        `characters.marvel.captain_america`
        and `characters.marvel.black_widow` respectively,
        ready for lookup in the ontology
        to obtain the "metadata" `TagSet` for each specific value.
    '''
    if convert:
      value_tag_name = convert(value)
      assert isinstance(value_tag_name, str) and value_tag_name
    else:
      value_tag_name = self.value_to_tag_name(str(value))
    ontkey = 'meta.' + type_name + '.' + '_'.join(
        value_tag_name.lower().split()
    )
    return self[ontkey]

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

  @pfx_method
  def edit_indices(self, indices, prefix=None):
    ''' Edit the entries specified by indices.
        Return `TagSet`s for the entries which were changed.
    '''
    tes = []
    te_old_names = {}
    for index in indices:
      if prefix:
        name = cutprefix(index, prefix)
        assert name is not index
      else:
        name = index
      te = self.entity(index, name=name)
      tes.append(te)
      te_old_names[id(te)] = name
    # modify tagsets
    changed_tes = TagSet.edit_entities(tes)
    # rename entries
    for te in changed_tes:
      old_name = te_old_names[id(te)]
      new_name = te.name
      if old_name == new_name:
        continue
      with Pfx("name %r => %r", old_name, new_name):
        new_index = prefix + new_name if prefix else new_name
        if new_index in self:
          warning("new name already exists, not renaming")
          continue
        old_index = prefix + old_name if prefix else old_name
        self[new_index] = te
        del self[old_index]
    return changed_tes

class TagFile(SingletonMixin, TagSets):
  ''' A reference to a specific file containing tags.

      This manages a mapping of `name` => `TagSet`,
      itself a mapping of tag name => tag value.
  '''

  @classmethod
  def _singleton_key(cls, filepath, **kw):
    return filepath

  @typechecked
  def __init__(self, filepath: str, *, ontology=None):
    if hasattr(self, 'filepath'):
      return
    super().__init__(ontology=ontology)
    self.filepath = filepath
    self._tagsets = None
    self._lock = Lock()

  def __str__(self):
    return "%s(%r)" % (type(self).__name__, shortpath(self.filepath))

  def __repr__(self):
    return "%s(%r)" % (type(self).__name__, self.filepath)

  def startup(self):
    ''' No special startup.
    '''

  def shutdown(self):
    ''' Save the tagsets if modified.
    '''
    self.save()

  def get(self, name, default=None):
    ''' Get from the tagsets.
    '''
    return self.tagsets.get(name, default)

  def __setitem__(self, name, te):
    ''' Set item `name` to `te`.
    '''
    self.tagsets[name] = te

  # Mapping mathods, proxying through to .tagsets.
  def keys(self, prefix=None):
    ''' `tagsets.keys`

        If the options `prefix` is supplied,
        yield only those keys starting with `prefix`.
    '''
    ks = self.tagsets.keys()
    if prefix:
      ks = filter(lambda k: k.startswith(prefix), ks)
    return ks

  def values(self, prefix=None):
    ''' `tagsets.values`

        If the optional `prefix` is supplied,
        yield only those values whose keys start with `prefix`.
    '''
    if not prefix:
      # use native values, faster
      return self.tagsets.values()
    return map(lambda kv: kv[1], self.items(prefix=prefix))

  def items(self, prefix=None):
    ''' `tagsets.items`

        If the optional `prefix` is supplied,
        yield only those items whose keys start with `prefix`.
    '''
    if not prefix:
      # use native items, faster
      return self.tagsets.items()
    return filter(lambda kv: kv[0].startswith(prefix), self.tagsets.items())

  def __delitem__(self, name):
    del self.tagsets[name]

  @locked_property
  @pfx_method
  def tagsets(self):
    ''' The tag map from the tag file,
        a mapping of name=>`TagSet`.

        This is loaded on demand.
    '''
    ts = self._tagsets = {}
    loaded_tagsets, unparsed = self.load_tagsets(self.filepath, self.ontology)
    self.unparsed = unparsed
    ont = self.ontology
    for name, tags in loaded_tagsets.items():
      te = ts[name] = self.default_factory(name)
      te.ontology = ont
      te.update(tags)
    return ts

  @property
  def names(self):
    ''' The names from this `FSTagsTagFile` as a list.
    '''
    return list(self.tagsets.keys())

  @classmethod
  @pfx_method
  def parse_tags_line(cls, line, ontology=None, verbose=None):
    ''' Parse a "name tags..." line as from a `.fstags` file,
        return `(name,TagSet)`.
    '''
    name, offset = Tag.parse_value(line)
    if offset < len(line) and not line[offset].isspace():
      _, offset2 = get_nonwhite(line, offset)
      name = line[:offset2]
      # This is normal.
      ##warning(
      ##    "offset %d: expected whitespace, adjusted name to %r", offset, name
      ##)
      offset = offset2
    if offset < len(line) and not line[offset].isspace():
      warning("offset %d: expected whitespace", offset)
    tags = TagSet.from_line(line, offset, ontology=ontology, verbose=verbose)
    return name, tags

  @classmethod
  def load_tagsets(cls, filepath, ontology):
    ''' Load `filepath` and return `(tagsets,unparsed)`.

        The returned `tagsets` are a mapping of `name`=>`tag_name`=>`value`.
        The returned `unparsed` is a list of `(lineno,line)`
        for lines which failed the parse (excluding the trailing newline).
    '''
    with Pfx("%r", filepath):
      tagsets = defaultdict(lambda: TagSet(_ontology=ontology))
      unparsed = []
      try:
        with open(filepath) as f:
          for lineno, line in enumerate(f, 1):
            with Pfx(lineno):
              line0 = cutsuffix(line, '\n')
              line = line0.strip()
              if not line:
                continue
              if line.startswith('#'):
                unparsed.append((lineno, line0))
                continue
              try:
                name, tags = cls.parse_tags_line(line, ontology=ontology)
              except ValueError as e:
                warning("parse error: %s", e)
                unparsed.append((lineno, line0))
              else:
                if 'name' in tags:
                  warning("discard explicit tag name=%s", tags.name)
                  tags.discard('name')
                tagsets[name] = tags
      except OSError as e:
        if e.errno != errno.ENOENT:
          raise
      return tagsets, unparsed

  @classmethod
  def tags_line(cls, name, tags):
    ''' Transcribe a `name` and its `tags` for use as a `.fstags` file line.
    '''
    fields = [Tag.transcribe_value(name)]
    for tag in tags:
      if tag.name == 'name':
        # we don't write this one out, but we do expect it to match
        # the `name` parameter
        if tag.value != name:
          warning(
              "%s.tags_line(name=%r,tags=%s): tags['name']:%r != name)",
              cls.__name__, name, tags, tag.value
          )
        continue
      fields.append(str(tag))
    return ' '.join(fields)

  @classmethod
  @pfx_method
  def save_tagsets(cls, filepath, tagsets, unparsed):
    ''' Save `tagsets` and `unparsed` to `filepath`.

        This method will create the required intermediate directories
        if missing.
    '''
    with Pfx(filepath):
      dirpath = dirname(filepath)
      if dirpath and not isdirpath(dirpath):
        ifverbose("makedirs(%r)", dirpath)
        with Pfx("os.makedirs(%r)", dirpath):
          os.makedirs(dirpath)
      name_tags = sorted(tagsets.items())
      try:
        with open(filepath, 'w') as f:
          for _, line in unparsed:
            if not line.startswith('#'):
              f.write('##  ')
            f.write(line)
            f.write('\n')
          for name, tags in name_tags:
            if not tags:
              continue
            f.write(cls.tags_line(name, tags))
            f.write('\n')
      except OSError as e:
        error("save(%r) fails: %s", filepath, e)
      else:
        for _, tags in name_tags:
          tags.modified = False

  def save(self):
    ''' Save the tag map to the tag file.
    '''
    tagsets = self._tagsets
    if tagsets is None:
      # TagSets never loaded
      return
    with self._lock:
      if any(map(lambda tagset: tagset.modified, tagsets.values())):
        # there are modified TagSets
        self.save_tagsets(self.filepath, tagsets, self.unparsed)
        for tagset in tagsets.values():
          tagset.modified = False

  def update(self, name, tags, *, prefix=None, verbose=None):
    ''' Update the tags for `name` from the supplied `tags`
        as for `Tagset.update`.
    '''
    return self[name].update(tags, prefix=prefix, verbose=verbose)

class TagsOntologyCommand(BaseCommand):
  ''' A command line for working with ontology types.
  '''

  def cmd_type(self, argv):
    ''' Usage:
          {cmd}
            With no arguments, list the defined types.
          {cmd} type_name
            With a type name, print its `Tag`s.
          {cmd} type_name edit
            Edit the tags defining a type.
          {cmd} type_name edit meta_names_pattern...
            Edit the tags for the metadata names matching the
            meta_names_patterns.
          {cmd} type_name list
            Listt the metadata names for this type and their tags.
    '''
    options = self.options
    ont = options.ontology
    if not argv:
      # list defined types
      for type_name, tags in ont.types():
        print(type_name, tags)
      return 0
    type_name = argv.pop(0)
    with Pfx(type_name):
      tags = ont.type(type_name)
      if not argv:
        for tag in sorted(tags):
          print(tag)
        return 0
      subcmd = argv.pop(0)
      with Pfx(subcmd):
        if subcmd == 'edit':
          if not argv:
            # edit the type specification
            tags.edit()
          else:
            # edit the metadata of this type
            meta_names = ont.meta_names(type_name=type_name)
            if not meta_names:
              error("no metadata of type %r", type_name)
              return 1
            selected = set()
            for ptn in argv:
              selected.update(fnmatch.filter(meta_names, ptn))
            indices = [
                ont.meta_index(type_name, value) for value in sorted(selected)
            ]
            ont.edit_indices(indices, prefix=ont.meta_index(type_name) + '.')
          return 0
        if subcmd == 'list':
          if argv:
            raise GetoptError("extra arguments: %r" % (argv,))
          for meta_name in sorted(ont.meta_names(type_name=type_name)):
            print(meta_name, ont.meta(type_name, meta_name))
          return 0
        raise GetoptError("unrecognised subcommand")

class TagsCommandMixin:
  ''' Utility methods for `cs.cmdutils.BaseCommand` classes working with tags.

      Optional subclass attributes:
      * `TAGSET_CRITERION_CLASS`: a `TagSetCriterion` duck class,
        default `TagSetCriterion`.
        For example, `cs.sqltags` has a subclass
        with an `.extend_query` method for computing an SQL JOIN
        used in searching for tagged entities.
  '''

  @classmethod
  def parse_tagset_criterion(cls, arg, tag_based_test_class=None):
    ''' Parse `arg` as a tag specification
        and return a `tag_based_test_class` instance
        via its `.from_str` factory method.
        Raises `ValueError` in a misparse.
        The default `tag_based_test_class`
        comes from `cls.TAGSET_CRITERION_CLASS`,
        which itself defaults to class `TagSetCriterion`.

        The default `TagSetCriterion.from_str` recognises:
        * `-`*tag_name*: a negative requirement for *tag_name*
        * *tag_name*[`=`*value*]: a positive requirement for a *tag_name*
          with optional *value*.
    '''
    if tag_based_test_class is None:
      tag_based_test_class = getattr(
          cls, 'TAGSET_CRITERION_CLASS', TagSetCriterion
      )
    return tag_based_test_class.from_str(arg)

  @classmethod
  def parse_tagset_criteria(cls, argv, tag_based_test_class=None):
    ''' Parse tag specifications from `argv` until an unparseable item is found.
        Return `(criteria,argv)`
        where `criteria` is a list of the parsed criteria
        and `argv` is the remaining unparsed items.

        Each item is parsed via
        `cls.parse_tagset_criterion(item,tag_based_test_class)`.
    '''
    argv = list(argv)
    criteria = []
    while argv:
      try:
        criterion = cls.parse_tagset_criterion(
            argv[0], tag_based_test_class=tag_based_test_class
        )
      except ValueError as e:
        warning("parse_tagset_criteria(%r): %s", argv[0], e)
        break
      criteria.append(criterion)
      argv.pop(0)
    return criteria, argv

  @staticmethod
  def parse_tag_choices(argv):
    ''' Parse `argv` as an iterable of [`!`]*tag_name*[`=`*tag_value`] `Tag`
        additions/deletions.
    '''
    tag_choices = []
    for arg in argv:
      with Pfx(arg):
        try:
          tag_choice = TagSetCriterion.from_str(arg)
        except ValueError as e:
          raise ValueError("bad tag specifications: %s" % (e,)) from e
        if tag_choice.comparison != '=':
          raise ValueError("only tag_name or tag_name=value accepted")
        tag_choices.append(tag_choice)
    return tag_choices

class RegexpTagRule:
  ''' A regular expression based `Tag` rule.

      This applies a regular expression to a string
      and returns inferred `Tag`s.
  '''

  def __init__(self, regexp):
    self.regexp_src = regexp
    self.regexp = re.compile(regexp)

  def __str__(self):
    return "%s(%r)" % (type(self).__name__, self.regexp_src)

  @pfx_method
  def infer_tags(self, s):
    ''' Apply the rule to the string `s`, return a list of `Tag`s.
    '''
    # TODO: honour the JSON decode strings
    tags = []
    m = self.regexp.search(s)
    if m:
      tag_value_queue = list(m.groupdict().items())
      while tag_value_queue:
        tag_name, value = tag_value_queue.pop(0)
        with Pfx(tag_name):
          if value is None:
            # unused branch of the regexp?
            warning("value=None, skipped")
            continue
          # special case prefix_strpdate_strptimeformat
          try:
            prefix, strptime_format_tplt = tag_name.split('_strpdate_', 1)
          except ValueError:
            pass
          else:
            tag_name = prefix + '_date'
            strptime_format = ' '.join(
                '%' + letter for letter in strptime_format_tplt.split('_')
            )
            value = datetime.strptime(value, strptime_format)
            tag_value_queue.insert(0, (tag_name, value))
            continue
          # special case prefix_strptime_strptimeformat
          try:
            prefix, strptime_format_tplt = tag_name.split('_strpdatetime_', 1)
          except ValueError:
            pass
          else:
            tag_name = prefix + '_datetime'
            strptime_format = ' '.join(
                '%' + letter for letter in strptime_format_tplt.split('_')
            )
            value = datetime.strptime(value, strptime_format)
            tag_value_queue.insert(0, (tag_name, value))
            continue
          # special case *_n
          tag_name_prefix = cutsuffix(tag_name, '_n')
          if tag_name is not tag_name_prefix:
            # numeric rule
            try:
              value = int(value)
            except ValueError:
              pass
            else:
              tag_name = tag_name_prefix
          tag = Tag(tag_name, value)
          tags.append(tag)
    return tags

def main(_):
  ''' Test code.
  '''
  # pylint: disable=import-outside-toplevel
  from cs.logutils import setup_logging
  setup_logging()
  ont = TagsOntology(
      {
          'type.colour':
          TagSet(description="a colour, a hue", type="str"),
          'meta.colour.blue':
          TagSet(
              url='https://en.wikipedia.org/wiki/Blue',
              wavelengths='450nm-495nm'
          ),
      }
  )
  tags = TagSet(colour='blue', labels=['a', 'b', 'c'], size=9, _ontology=ont)
  print("tags.colour =", tags.colour)
  colour = tags.tag('colour')
  print("colour =", colour)
  print("type =", colour.typedata)
  print("meta =", colour.metadata)
  print("meta.url =", colour.meta.url)
  print("meta.ns =", colour.meta.ns())
  print("meta.url =", colour.meta.ns().url_s)
  ns = tags.ns()
  print("colour =", ns.colour._tag)
  print("colour.metadata =", ns.colour._meta)
  print("colour.metadata.url =", ns.colour._meta.url)
  ##print("colour.metadata.ns() =", ns.colour._tag.metadata.ns())
  ##print(repr(list(ns.colour._tag.metadata.ns().__dict__.items())))

if __name__ == '__main__':
  import sys  # pylint: disable=import-outside-toplevel
  sys.exit(main(sys.argv))
