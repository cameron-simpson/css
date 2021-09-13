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
      a mapping of type names to `TagSet`s defining the type
      and also to entries for the metadata for specific per-type values.

    Here's a simple example with some `Tag`s and a `TagSet`.

        >>> tags = TagSet()
        >>> # add a "bare" Tag named 'blue' with no value
        >>> tags.add('blue')
        >>> # add a "topic=tagging" Tag
        >>> tags.set('topic', 'tagging')
        >>> # make a "subtopic" Tag and add it
        >>> subtopic = Tag('subtopic', 'ontologies')
        >>> tags.add(subtopic)
        >>> # Tags have nice repr() and str()
        >>> subtopic
        Tag(name='subtopic',value='ontologies',ontology=None)
        >>> print(subtopic)
        subtopic=ontologies
        >>> # a TagSet also has a nice repr() and str()
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
        >>> print(subtopic)
        subtopic=ontologies
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

Consider a record about a movie, with these tags (a `TagSet`):

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
    character.marvel.black_widow type=character names=["Natasha Romanov"]
    person.scarlett_johansson fullname="Scarlett Johansson" bio="Known for Black Widow in the Marvel stories."

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
The ontology entry for her is named `person.scarlett_johansson`
which is computed as:
* `person`: the type name
* `scarlett_johansson`: obtained by downcasing `"Scarlett Johansson"`
  and replacing whitespace with an underscore.
  The full conversion process is defined
  by the `TagsOntology.value_to_tag_name` function.

The key `"Black Widow (Marvel)"` is a `character`
(again, from the type definition of `cast`).
The ontology entry for her is named `character.marvel.black_widow`
which is computed as:
* `character`: the type name
* `marvel.black_widow`: obtained by downcasing `"Black Widow (Marvel)"`,
  replacing whitespace with an underscore,
  and moving a bracketed suffix to the front as an unbracketed prefix.
  The full conversion process is defined
  by the `TagsOntology.value_to_tag_name` function.

== Format Strings ==

You can just use `str.format_map` as shown above
for the direct values in a `TagSet`,
since it subclasses `dict`.

However, `TagSet`s also subclass `cs.lex.FormatableMixin`
and therefore have a richer `format_as` method which has an extended syntax
for the format component.
Command line tools like `fstags` use this for output format specifications.

An example:

    >>> # an ontology specifying the type for a colour
    >>> # and some information about the colour "blue"
    >>> ont = TagsOntology(
    ...   {
    ...       'type.colour':
    ...       TagSet(description="a colour, a hue", type="str"),
    ...       'colour.blue':
    ...       TagSet(
    ...           url='https://en.wikipedia.org/wiki/Blue',
    ...           wavelengths='450nm-495nm'
    ...       ),
    ...   }
    ... )
    >>> # tag set with a "blue" tag, using the ontology above
    >>> tags = TagSet(colour='blue', labels=['a', 'b', 'c'], size=9, _ontology=ont)
    >>> tags.format_as('The colour is {colour}.')
    'The colour is blue.'
    >>> # format a string about the tags showing some metadata about the colour
    >>> tags.format_as('Information about the colour may be found here: {colour:metadata.url}')
    'Information about the colour may be found here: https://en.wikipedia.org/wiki/Blue'


'''

from abc import ABC, abstractmethod
from collections import defaultdict, namedtuple
from collections.abc import MutableMapping
from contextlib import contextmanager
from datetime import date, datetime
import errno
from fnmatch import (fnmatch, fnmatchcase, translate as fn_translate)
from getopt import GetoptError
from json import JSONEncoder, JSONDecoder
from json.decoder import JSONDecodeError
import os
from os.path import dirname, isdir as isdirpath
import re
from threading import Lock
import time
from typing import Optional, Union
from uuid import UUID
from icontract import require
from typeguard import typechecked
from cs.cmdutils import BaseCommand
from cs.dateutils import UNIXTimeMixin
from cs.deco import decorator
from cs.edit import edit_strings, edit as edit_lines
from cs.fileutils import shortpath
from cs.lex import (
    cropped_repr, cutprefix, cutsuffix, get_dotted_identifier, get_nonwhite,
    is_dotted_identifier, is_identifier, skipwhite, FormatableMixin,
    has_format_attributes, format_attribute, FStr, typed_repr as r
)
from cs.logutils import setup_logging, warning, error, ifverbose
from cs.mappings import (
    AttrableMappingMixin, IndexedMapping, PrefixedMappingProxy,
    RemappedMappingProxy
)
from cs.obj import SingletonMixin
from cs.pfx import Pfx, pfx, pfx_method
from cs.py3 import date_fromisoformat, datetime_fromisoformat
from cs.resources import MultiOpenMixin
from cs.threads import locked_property

__version__ = '20210913'

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

    def accept_tag_or_tag_value(self, name, value=None, *a, **kw):  # pylint: disable=keyword-arg-before-vararg
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

@has_format_attributes
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
      provided that they do not conflict with instance attributes
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

  # Arrange to return None for missing mapping attributes
  # supporting tags.foo being None if there is no 'foo' tag.
  # Note: sometimes this has confusing effects.
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

  #################################################################
  # methods supporting FormattableMixin/ExtendedFormatter

  def get_arg_name(self, field_name):
    ''' Leading dotted identifiers represent tags or tag prefixes.
    '''
    return get_dotted_identifier(field_name)

  def get_value(self, arg_name, a, kw):
    assert isinstance(kw, TagSet)
    assert not a
    try:
      value = kw[arg_name]
    except KeyError:
      try:
        attribute = kw.get_format_attribute(arg_name)
      except AttributeError:
        if self.format_mode.strict:
          raise KeyError(
              "%s.get_value: unrecognised arg_name %r" %
              (type(self).__name__, arg_name)
          )
        value = f'{{{arg_name}}}'
      else:
        value = attribute() if callable(attribute) else attribute
    return value, arg_name

  ################################################################
  # The magic attributes.

  # pylint: disable=too-many-nested-blocks,too-many-return-statements
  def __getattr__(self, attr):
    ''' Support access to dotted name attributes.

        The following attribute access are supported:

        If `attr` is a key, return `self[attr]`.

        If `self.auto_infer(attr)` does not raise `ValueError`,
        return that value.

        If this `TagSet` has an ontology
        and `attr looks like *typename*`_`*fieldname*
        and *typename* is a key,
        look up the metadata for the `Tag` value
        and return the metadata's *fieldname* key.
        This also works for plural values.

        For example if a `TagSet` has the tag `artists=["fred","joe"]`
        and `attr` is `artist_names`
        then the metadata entries for `"fred"` and `"joe"` looked up
        and their `artist_name` tags are returned,
        perhaps resulting in the list
        `["Fred Thing","Joe Thang"]`.

        If there are keys commencing with `attr+'.'`
        then this returns a view of those keys
        so that a subsequent attribute access can access one of those keys.

        Otherwise, a superclass attribute access is performed.

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
            TagSetPrefixView:c.{'z': 9, 'x': 8}
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
      try:
        return self.auto_infer(attr)
      except ValueError as e:
        # no match
        ##warning("auto_infer(%r): %s", attr, e)
        pass
      # support for {type}_{field} and {type}_{field}s attributes
      # these dereference through the ontology if there is one
      ont = self.ontology
      if ont is not None:
        try:
          type_name, field_part = attr.split('_', 1)
        except ValueError:
          pass
        else:
          if type_name in self:
            value = self[type_name]
            md = ont.metadata(type_name, value)
            return md.get(field_part)
          type_name_s = type_name + 's'
          if type_name_s in self:
            # plural field
            values = self[type_name_s]
            if isinstance(values, (tuple, list)):
              # dereference lists and tuples
              field_name = cutsuffix(field_part, 's')
              md_field = type_name + '_' + field_name
              mds = [ont.metadata(type_name, value) for value in values]
              if field_name is field_part:
                # singular - take the first element
                if not mds:
                  return None
                md = mds[0]
                dereffed = md.get(md_field)
                return dereffed
              dereffed = [md.get(md_field) for md in mds]
              return dereffed
            # misfilled field - seems to be a scalar
            value = values
            md = ont.metadata(type_name, value)
            dereffed = md.get(field_part)
            return dereffed
      # magic dotted name access to attr.bar if there are keys
      # starting with "attr."
      if attr and attr[0].isalpha():
        attr_ = attr + '.'
        if any(map(lambda k: k.startswith(attr_) and k > attr_, self.keys())):
          return self.subtags(attr)
      try:
        super_getattr = super().__getattr__
      except AttributeError:
        raise AttributeError(type(self).__name__ + '.' + attr)  # pylint: disable=raise-missing-from
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
  def from_line(
      cls, line, offset=0, *, ontology=None, extra_types=None, verbose=None
  ):
    ''' Create a new `TagSet` from a line of text.
    '''
    tags = cls(_ontology=ontology)
    offset = skipwhite(line, offset)
    while offset < len(line):
      tag, offset = Tag.from_str2(
          line, offset, ontology=ontology, extra_types=extra_types
      )
      tags.add(tag, verbose=verbose)
      offset = skipwhite(line, offset)
    return tags

  def __contains__(self, tag):
    ''' Test for a tag being in this `TagSet`.

        If the supplied `tag` is a `str` then this test
        is for the presence of `tag` in the keys.

        Otherwise,
        for each tag `T` in the tagset
        test `T.matches(tag)` and return `True` on success.
        The default `Tag.matches` method compares the tag name
        and if the same,
        returns true if `tag.value` is `None` (basic "is the tag present" test)
        and otherwise true if `tag.value==T.value` (basic "tag value equality" test).

        Otherwise return `False`.
    '''
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
  # note: cannot just be add=set because it won't follow subclass overrides
  @tag_or_tag_value
  def add(self, tag_name, value, **kw):
    ''' Adding a `Tag` calls the class `set()` method.
    '''
    return self.set(tag_name, value, **kw)

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

  def subtags(self, prefix, as_tagset=False):
    ''' Return `TagSetPrefixView` of the tags commencing with `prefix+'.'`
        with the key prefixes stripped off.

        If `as_tagset` is true (default `False`)
        return a new standalone `TagSet` containing the prefixed keys.

        Example:

            >>> tags = TagSet({'a.b':1, 'a.d':2, 'c.e':3})
            >>> tags.subtags('a')
            TagSetPrefixView:a.{'b': 1, 'd': 2}
            >>> tags.subtags('a', as_tagset=True)
            TagSet:{'b': 1, 'd': 2}
    '''
    if as_tagset:
      # prepare a standalone TagSet
      prefix_ = prefix + '.'
      subdict = {
          cutprefix(k, prefix_): self[k]
          for k in self.keys()
          if k.startswith(prefix_)
      }
      return TagSet(subdict, _ontology=self.ontology)
    # prepare a view of this TagSet
    return TagSetPrefixView(self, prefix)

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

  #############################################################################
  # The '.auto' attribute space.

  class Auto:

    def __init__(self, tagset, prefix=None):
      self._tagset = tagset
      self._prefix = prefix

    def __bool__(self):
      ''' We return `False` so that an unresolved attribute,
          which returns a deeper `Auto` instance,
          looks false, enabling:

              title = tags.auto.title or "default title"
      '''
      return False

    def __getattr__(self, attr):
      fullattr = (
          attr if self._prefix is None else '.'.join((self._prefix, attr))
      )
      try:
        return self._tagset.auto_infer(fullattr)
      except ValueError:
        # auto view of deeper attributes
        return self._tagset.Auto(self._tagset, fullattr)

  @property
  def auto(self):
    return self.Auto(self)

  @pfx_method
  def auto_infer(self, attr):
    ''' The default inference implementation.

        This should return a value if `attr` is inferrable
        and raise `ValueError` if not.

        The default implementation returns the direct tag value for `attr`
        if present.
    '''
    if attr in self:
      warning("returning direct tag value for %r", attr)
      return self[attr]
    raise ValueError("cannot infer value for %r" % (attr,))

  #############################################################################
  # Edit tags.

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
    ''' Edit a collection of `TagSet`s.
        Return a list of `(old_name,new_name,TagSet)` for those which were modified.

        This function supports modifying both `name` and `Tag`s.
        The `Tag`s are updated directly.
        The changed names are returning in the `old_name,new_name` above.

        The collection `tes` may be either a mapping of name/key
        to `TagSet` or an iterable of `TagSets`. If the latter, a
        mapping is made based on `te.name or te.id` for each item
        `te` in the iterable.
    '''
    if editor is None:
      editor = EDITOR
    try:
      tes.items
    except AttributeError:
      te_map = {te.name or te.id: te for te in tes}
    else:
      te_map = tes
    assert all(isinstance(k, (str, int)) for k in te_map.keys()), \
        "not all entities have str or int keys: %r" % list(te_map.keys())
    lines = list(
        map(
            lambda te_item: ' '.join(
                [Tag.transcribe_value(te_item[0])] + [
                    str(te_item[1].tag(tag_name))
                    for tag_name in te_item[1].keys()
                    if tag_name != 'name'
                ]
            ), te_map.items()
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

@has_format_attributes
class Tag(namedtuple('Tag', 'name value ontology'), FormatableMixin):
  ''' A `Tag` has a `.name` (`str`) and a `.value`
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
      * an optional `prefix` may be supplied
        which is prepended to `name` with a dot (`'.'`) if not empty

      The promotion process is as follows:
      * if `name` is a `Tag` subinstance
        then if the supplied `ontology` is not `None`
        and is not the ontology associated with `name`
        then a new `Tag` is made,
        otherwise the original `Tag` is returned unchanged
      * otherwise a new `Tag` is made from `name`
        using its `.value`
        and overriding its `.ontology`
        if the `ontology` parameter is not `None`

      Examples:

          >>> ont = TagsOntology({'colour.blue': TagSet(wavelengths='450nm-495nm')})
          >>> tag0 = Tag('colour', 'blue')
          >>> tag0
          Tag(name='colour',value='blue',ontology=None)
          >>> tag = Tag(tag0)
          >>> tag
          Tag(name='colour',value='blue',ontology=None)
          >>> tag is tag0
          True
          >>> tag = Tag(tag0, ontology=ont)
          >>> tag # doctest: +ELLIPSIS
          Tag(name='colour',value='blue',ontology=...)
          >>> tag is tag0
          False
          >>> tag = Tag(tag0, prefix='surface')
          >>> tag
          Tag(name='surface.colour',value='blue',ontology=None)
          >>> tag is tag0
          False
  '''

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

  @typechecked
  def __new__(
      cls,
      name,
      value=None,
      *,
      ontology: Optional["TagsOntology"] = None,
      prefix: Optional[str] = None
  ):
    # simple case: name is a str: make a new Tag
    if isinstance(name, str):
      # (name[,value[,ontology][,prefix]]) => Tag
      if prefix:
        name = prefix + '.' + name
      return super().__new__(cls, name, value, ontology)
    # name should be taglike, value should not be present (None)
    tag = name
    try:
      name = tag.name
    except AttributeError:
      raise ValueError("tag has no .name attribute")  # pylint: disable=raise-missing-from
    else:
      name0 = name  # keep the preprefix name
      if prefix:
        name = prefix + '.' + name
    if value is not None:
      raise ValueError(
          "name(%s) is not a str, value must be None" % (r(name),)
      )
    try:
      value = tag.value
    except AttributeError:
      raise ValueError("tag has no .value attribute")  # pylint: disable=raise-missing-from
    if isinstance(tag, Tag):
      # already a Tag subtype, see if the ontology needs updating or the name was changed
      if name != name0 or (ontology is not None
                           and tag.ontology is not ontology):
        # new Tag with supplied ontology
        tag = super().__new__(cls, name, value, ontology)
    else:
      # not a Tag subtype, construct a new instance,
      # overriding .ontology if the supplied ontology is not None
      tag = super().__new__(
          cls, name, value, (
              ontology
              if ontology is not None else getattr(tag, 'ontology', None)
          )
      )
    return tag

  def __init__(self, *a, **kw):
    ''' Dummy `__init__` to avoid `FormatableMixin.__init__`
        because we subclass `namedtuple` which has no `__init__`.
    '''

  __hash__ = tuple.__hash__

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
  def transcribe_value(cls, value, extra_types=None):
    ''' Transcribe `value` for use in `Tag` transcription.

        The optional `extra_types` parameter may be an iterable of
        `(type,from_str,to_str)` tuples where `to_str` is a
        function which takes a string and returns a Python object
        (expected to be an instance of `type`).
        The default comes from `cls.EXTRA_TYPES`.

        If `value` is an instance of `type`
        then the `to_str` function is used to transcribe the value
        as a `str`, which should not include any whitespace
        (because of the implementation of `parse_value`).
        If there is no matching `to_str` function,
        `cls.JSON_ENCODER.encode` is used to transcribe `value`.

        This supports storage of nonJSONable values in text form.
    '''
    if extra_types is None:
      extra_types = cls.EXTRA_TYPES
    for type_, _, to_str in extra_types:
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
      tag, post_offset = cls.from_str2(s, offset=offset, ontology=ontology)
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
  def from_str2(cls, s, offset=0, *, ontology, extra_types=None):
    ''' Parse tag_name[=value], return `(Tag,offset)`.
    '''
    with Pfx("%s.from_str2(%s)", cls.__name__, cropped_repr(s[offset:])):
      name, offset = cls.parse_name(s, offset)
      with Pfx(name):
        if offset < len(s):
          sep = s[offset]
          if sep.isspace():
            value = None
          elif sep == '=':
            offset += 1
            value, offset = cls.parse_value(s, offset, extra_types=extra_types)
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
  def parse_value(cls, s, offset=0, extra_types=None):
    ''' Parse a value from `s` at `offset` (default `0`).
        Return the value, or `None` on no data.

        The optional `extra_types` parameter may be an iterable of
        `(type,from_str,to_str)` tuples where `from_str` is a
        function which takes a string and returns a Python object
        (expected to be an instance of `type`).
        The default comes from `cls.EXTRA_TYPES`.
        This supports storage of nonJSONable values in text form.

        The core syntax for values is JSON;
        value text commencing with any of `'"'`, `'['` or `'{'`
        is treated as JSON and decoded directly,
        leaving the offset at the end of the JSON parse.

        Otherwise all the nonwhitespace at this point is collected
        as the value text,
        leaving the offset at the next whitespace character
        or the end of the string.
        The text so collected is then tried against the `from_str`
        function of each `extra_types`;
        the first successful parse is accepted as the value.
        If no extra type match,
        the text is tried against `int()` and `float()`;
        if one of these parses the text and `str()` of the result round trips
        to the original text
        then that value is used.
        Otherwise the text itself is kept as the value.
    '''
    if extra_types is None:
      extra_types = cls.EXTRA_TYPES
    if offset >= len(s) or s[offset].isspace():
      warning("offset %d: missing value part", offset)
      value = None
    elif s[offset] in '"[{':
      # must be a JSON value - collect it
      value_part = s[offset:]
      try:
        value, suboffset = cls.JSON_DECODER.raw_decode(value_part)
      except JSONDecodeError as e:
        raise ValueError(
            "offset %d: raw_decode(%r): %s" % (offset, value_part, e)
        ) from e
      offset += suboffset
    else:
      # collect nonwhitespace, check for special forms
      nonwhite, offset = get_nonwhite(s, offset)
      value = None
      for _, from_str, _ in extra_types:
        try:
          value = from_str(nonwhite)
        except ValueError:
          pass
        else:
          break
      if value is None:
        # not one of the special formats
        # check for round trip int or float
        try:
          i = int(nonwhite)
        except ValueError:
          pass
        else:
          if str(i) == nonwhite:
            value = i
          else:
            try:
              f = float(nonwhite)
            except ValueError:
              pass
            else:
              if str(f) == nonwhite:
                value = f
      if value is None:
        # not a special value, preserve as a string
        value = nonwhite
    return value, offset

  @property
  @pfx_method(use_str=True)
  def typedef(self):
    ''' The defining `TagSet` for this tag's name.

        This is how its type is defined,
        and is obtained from:
        `self.ontology['type.'+self.name]`

        Basic `Tag`s often do not need a type definition;
        these are only needed for structured tag values
        (example: a mapping of cast members)
        or when a `Tag` name is an alias for another type
        (example: a cast member name might be an `actor`
        which in turn might be a `person`).

        For example, a `Tag` `colour=blue`
        gets its type information from the `type.colour` entry in an ontology;
        that entry is just a `TagSet` with relevant information.
    '''
    ont = self.ontology
    if ont is None:
      warning("%s:%r: no ontology, returning None", type(self), self)
      return None
    return ont.typedef(self.name)

  @property
  @pfx_method(use_str=True)
  def key_typedef(self):
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
    return ont.typedef(key_type)

  @pfx_method(use_str=True)
  def key_metadata(self, key):
    ''' Return the metadata definition for `key`.

        The metadata `TagSet` is obtained from the ontology entry
        *type*`.`*key_tag_name*
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
    key_metadata_name = key_type + '.' + ont.value_to_tag_name(key)
    return ont[key_metadata_name]

  @property
  @pfx_method(use_str=True)
  def member_typedef(self):
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
    return ont.typedef(member_type)

  @pfx_method(use_str=True)
  def member_metadata(self, member_key):
    ''' Return the metadata definition for self[member_key].

        The metadata `TagSet` is obtained from the ontology entry
        *type*`.`*member_tag_name*
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
    member_metadata_name = member_type + '.' + ont.value_to_tag_name(value)
    return ont[member_metadata_name]

  @property
  def basetype(self):
    ''' The base type name for this tag.
        Returns `None` if there is no ontology.

        This calls `self.onotology.basetype(self.name)`.
        The basetype is the endpoint of a cascade down the defined types.

        For example, this might tell us that a `Tag` `role="Fred"`
        has a basetype `"str"`
        by cascading through a hypothetical chain `role`->`character`->`str`:

            type.role type=character
            type.character type=str
    '''
    ont = self.ontology
    if ont is None:
      warning("no ontology, returning None")
      return None
    return ont.basetype(self.name)

  @format_attribute
  def metadata(self, *, ontology=None, convert=None):
    ''' Fetch the metadata information about this specific tag value,
        derived through the `ontology` from the tag name and value.
        The default `ontology` is `self.ontology`.

        For a scalar type (`int`, `float`, `str`) this is the ontology `TagSet`
        for `self.value`.

        For a sequence (`list`) this is a list of the metadata
        for each member.

        For a mapping (`dict`) this is mapping of `key->metadata`.
    '''
    ont = ontology or self.ontology
    assert ont, "ont is false: %r" % (ont,)
    return ont.metadata(self, convert=convert)

  @property
  def meta(self):
    ''' Shortcut property for the metadata `TagSet`.
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

# pylint: disable=too-many-ancestors
class TagSetPrefixView(FormatableMixin):
  ''' A view of a `TagSet` via a `prefix`.

      Access to a key `k` accesses the `TagSet`
      with the key `prefix+'.'+k`.

      This is a kind of funny hybrid of a `Tag` and a `TagSet`
      in that some things such as `__format__`
      will format the `Tag` named `prefix` if it exists
      in preference to the subtags.

      Example:

          >>> tags = TagSet(a=1, b=2)
          >>> tags
          TagSet:{'a': 1, 'b': 2}
          >>> tags['sub.x'] = 3
          >>> tags['sub.y'] = 4
          >>> tags
          TagSet:{'a': 1, 'b': 2, 'sub.x': 3, 'sub.y': 4}
          >>> sub = tags.sub
          >>> sub
          TagSetPrefixView:sub.{'x': 3, 'y': 4}
          >>> sub.z = 5
          >>> sub
          TagSetPrefixView:sub.{'x': 3, 'y': 4, 'z': 5}
          >>> tags
          TagSet:{'a': 1, 'b': 2, 'sub.x': 3, 'sub.y': 4, 'sub.z': 5}
  '''

  @typechecked
  @require(lambda prefix: len(prefix) > 0)
  def __init__(self, tags, prefix: str):
    self.__dict__.update(_tags=tags, _prefix=prefix, _prefix_=prefix + '.')

  def __str__(self):
    tag = self.tag
    if tag is None:
      return repr(
          dict(map(lambda k: (self._prefix_ + k, self._tags[k]), self.keys()))
      )
    return FStr(tag.value)

  def __repr__(self):
    return "%s:%s%r" % (type(self).__name__, self._prefix_, dict(self.items()))

  @property
  def ontology(self):
    ''' The ontology of the references `TagSet`.
    '''
    return self._tags.ontology

  @property
  def __proxied(self):
    ''' Return the object for which this view is a proxy.
        If there's a `Tag` at this node, return the `Tag`.
        Otherwise return a sub`TagSet` based on the prefix.
    '''
    tag = self.tag
    if tag is not None:
      return tag
    return self._tags.subtags(self._prefix, as_tagset=True)

  def get_format_attribute(self, attr):
    ''' Fetch a formatting attribute from the proxied object.
    '''
    return self.__proxied.get_format_attribute(attr)

  def keys(self):
    ''' The keys of the subtags.
    '''
    prefix_ = self._prefix_
    return map(
        lambda k: cutprefix(k, prefix_),
        filter(lambda k: k.startswith(prefix_), self._tags.keys())
    )

  def __contains__(self, k):
    return self._prefix_ + k in self._tags

  def __getitem__(self, k):
    return self._tags[self._prefix_ + k]

  def __setitem__(self, k, v):
    self._tags[self._prefix_ + k] = v

  def __deltitem__(self, k):
    del self._tags[self._prefix_ + k]

  def items(self):
    ''' Return an iterable of the items (`Tag` name, `Tag`).
    '''
    return map(lambda k: (k, self[k]), self.keys())

  def values(self):
    ''' Return an iterable of the values (`Tag`s).
    '''
    return map(lambda k: self[k], self.keys())

  def __getattr__(self, attr):
    ''' Proxy other attributes through to the `TagSet`.
    '''
    with Pfx("%s.__getattr__(%r)", type(self).__name__, attr):
      try:
        return self[attr]
      except (KeyError, TypeError):
        return getattr(self.__proxied, attr)

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

  def subtags(self, subprefix):
    ''' Return a deeper view of the `TagSet`.
    '''
    return type(self)(self._tags, self._prefix_ + subprefix)

  @property
  def tag(self):
    ''' The `Tag` for the prefix, or `None` if there is no such `Tag`.
    '''
    return self._tags.tag(self._prefix)

  @property
  def value(self):
    ''' Return the `Tag` value for the prefix, or `None` if there is no such `Tag`.
    '''
    return self._tags.get(self._prefix)

class BaseTagSets(MultiOpenMixin, MutableMapping, ABC):
  ''' Base class for collections of `TagSet` instances
      such as `cs.fstags.FSTags` and `cs.sqltags.SQLTags`.

      Examples of this include:
      * `cs.fstags.FSTags`: a mapping of filesystem paths to their associated `TagSet`
      * `cs.sqltags.SQLTags`: a mapping of names to `TagSet`s stored in an SQL database

      Subclasses must implement:
      * `get(name,default=None)`: return the `TagSet` associated
        with `name`, or `default`.
      * `__setitem__(name,tagset)`: associate a `TagSet`with the key `name`;
        this is called by the `__missing__` method with a newly created `TagSet`.
      * `keys(self)`: return an iterable of names

      Subclasses may reasonably want to override the following:
      * `startup_shutdown(self)`: context manager to allocate and release any 
        needed resources such as database connections

      Subclasses may implement:
      * `__len__(self)`: return the number of names

      The `TagSet` factory used to fetch or create a `TagSet` is
      named `TagSetClass`. The default implementation honours two
      class attributes:
      * `TAGSETCLASS_DEFAULT`: initially `TagSet`
      * `TAGSETCLASS_PREFIX_MAPPING`: a mapping of type names to `TagSet` subclasses

      The type name of a `TagSet` name is the first dotted component.
      For example, `artist.nick_cave` has the type name `artist`.
      A subclass of `BaseTagSets` could utiliise an `ArtistTagSet` subclass of `TagSet`
      and provide:

          TAGSETCLASS_PREFIX_MAPPING = {
            'artist': ArtistTagSet,
          }

      in its class definition. Accesses to `artist.`* entities would
      result in `ArtistTagSet` instances and access to other enitities
      would result in ordinary `TagSet` instances.
  '''

  _missing = object()

  # the default `TagSet` subclass
  TAGSETCLASS_DEFAULT = TagSet

  # a mapping of TagSet name prefixs to TagSet subclasses
  # used to automatically map certain tagsets to types
  TAGSETCLASS_PREFIX_MAPPING = {}

  @pfx_method
  def TagSetClass(self, *, name, **kw):
    ''' Factory to create a new `TagSet` from `name`.
    '''
    cls = self.TAGSETCLASS_DEFAULT
    if isinstance(name, str):
      try:
        type_name, _ = name.split('.', 1)
      except ValueError:
        pass
      else:
        cls = self.TAGSETCLASS_PREFIX_MAPPING.get(type_name, cls)
    tags = cls(name=name, **kw)
    return tags

  def __init__(self, *, ontology=None):
    ''' Initialise the collection.
    '''
    self.ontology = ontology

  def __str__(self):
    return "%s<%s>" % (type(self).__name__, id(self))

  __repr__ = __str__

  def default_factory(self, name: str):
    ''' Create a new `TagSet` named `name`.
    '''
    te = self.TagSetClass(name=name)
    te.ontology = self.ontology
    return te

  @pfx_method
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

  #################################################################
  # MutableMapping methods

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

  @abstractmethod
  # pylint: disable=arguments-differ
  def keys(self, *, prefix=None):
    ''' Return the keys starting with `prefix+'.'`
        or all keys if `prefix` is `None`.
    '''
    raise NotImplementedError("%s: no .keys() method" % (type(self).__name__,))

  def __iter__(self):
    ''' Iteration returns the keys.
    '''
    return self.keys()

  # pylint: disable=arguments-differ
  def values(self, *, prefix=None):
    ''' Generator yielding the mapping values (`TagSet`s),
        optionally constrained to keys starting with `prefix+'.'`.
    '''
    for k in self.keys(prefix=prefix):
      yield self.get(k)

  # pylint: disable=arguments-differ
  def items(self, *, prefix=None):
    ''' Generator yielding `(key,value)` pairs,
        optionally constrained to keys starting with `prefix+'.'`.
    '''
    for k in self.keys(prefix=prefix):
      yield k, self.get(k)

  def __contains__(self, name: str):
    ''' Test whether `name` is present in the underlying mapping.
    '''
    missing = object()
    return self.get(name, missing) is not missing

  def __len__(self):
    ''' Return the length of the underlying mapping.
    '''
    raise NotImplementedError(
        "%s: no .__len__() method" % (type(self).__name__,)
    )

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

  def subdomain(self, subname: str):
    ''' Return a proxy for this `BaseTagSets` for the `name`s
        starting with `subname+'.'`.
    '''
    return TagSetsSubdomain(self, subname)

  def edit(self, *, select_tagset=None, **kw):
    ''' Edit the `TagSet`s.

        Parameters:
        * `select_tagset`: optional callable accepting a `TagSet`
          which tests whether it should be included in the `TagSet`s
          to be edited
        Other keyword arguments are passed to `Tag.edit_many`.
    '''
    if select_tagset is None:
      tes = self
    else:
      tes = {name: te for name, te in self.items() if select_tagset(te)}
    changed_tes = TagSet.edit_many(tes, **kw)
    for old_name, new_name, te in changed_tes:
      if old_name != new_name:
        with Pfx("rename %r => %r", old_name, new_name):
          te.name = new_name

class TagSetsSubdomain(SingletonMixin, PrefixedMappingProxy):
  ''' A view into a `BaseTagSets` for keys commencing with a prefix.
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

class MappingTagSets(BaseTagSets):
  ''' A `BaseTagSets` subclass using an arbitrary mapping.

      If no mapping is supplied, a `dict` is created for the purpose.

      Example:

          >>> tagsets = MappingTagSets()
          >>> list(tagsets.keys())
          []
          >>> tagsets.get('foo')
          >>> tagsets['foo'] = TagSet(bah=1, zot=2)
          >>> list(tagsets.keys())
          ['foo']
          >>> tagsets.get('foo')
          TagSet:{'bah': 1, 'zot': 2}
          >>> list(tagsets.keys(prefix='foo'))
          ['foo']
          >>> list(tagsets.keys(prefix='bah'))
          []
  '''

  def __init__(self, mapping=None, ontology=None):
    if mapping is None:
      mapping = {}
    super().__init__(ontology=ontology)
    self.mapping = mapping

  def get(self, name: str, default=None):
    return self.mapping.get(name, default)

  def __setitem__(self, name, te):
    ''' Save `te` in the backend under the key `name`.
    '''
    self.mapping[name] = te

  def __delitem__(self, name):
    ''' Delete the `TagSet` named `name`.
    '''
    del self.mapping[name]

  @typechecked
  def keys(self, *, prefix: Optional[str] = None):
    ''' Return an iterable of the keys commencing with `prefix`
        or all keys if `prefix` is `None`.
    '''
    ks = self.mapping.keys()
    if prefix:
      ks = filter(lambda k: k.startswith(prefix), ks)
    return ks

class _TagsOntology_SubTagSets(RemappedMappingProxy, MultiOpenMixin):
  ''' A wrapper for a `TagSets` instance backing an ontology.

      Each instance has the following attributes:
      * `tagsets`: the `TagSets` instance containing ontology information
      * `match_func`: a function of the type name used in the main ontology,
        returning `None` if the type name is not supported
        by this particular `TagSets`;
        if this function is `None` that all type names are accepted unchanged.
      * `unmatch_func`: a function of the type name used within
        this particular `TagSets`,
        returning the type name used in the main ontology;
        it should be the reverse of `match_func`;
        if this function is `None` the subtype name is returned unchanged.
      * `type_map`: an `IndexedMapping` caching type_name<->subtype_name associations

      Example:

          >>> subTS = _TagsOntology_SubTagSets(MappingTagSets(), 'prefix.')
          >>> subkey = subTS.subkey('prefix.key')
          >>> subkey
          'key'
          >>> key = subTS.key(subkey)
          >>> key
          'prefix.key'
          >>> subTS['prefix.bah'].add('x', 1)
          >>> list(subTS.tagsets.keys())
          ['bah']
          >>> list(subTS.keys())
          ['prefix.bah']
  '''

  @typechecked
  def __init__(self, tagsets: BaseTagSets, match, unmatch=None):
    self.__match = match
    self.__unmatch = unmatch
    accepts_key = None
    if match is None:
      assert unmatch is None
      accepts_key = lambda _: True
      to_subkey = lambda key: key
      from_subkey = lambda subkey: subkey
    elif isinstance(match, str):
      assert unmatch is None
      if match.endswith(('.', '-', '_')):
        # prefixed based match and translation
        accepts_key = lambda key: key.startswith(match)
        to_subkey = lambda key: cutprefix(key, match)
        from_subkey = lambda subkey: match + subkey
      else:
        # prefixed based match, use keys unchanged
        match_ = match + '.'
        accepts_key = lambda key: key.startswith(match_)
        to_subkey = lambda key: key
        from_subkey = lambda subkey: subkey
    elif callable(match):
      accepts_key = lambda key: match(key) is not None
      to_subkey = match
      from_subkey = unmatch
    if accepts_key is None:
      raise ValueError("unsupported match=%r, unmatch=%r" % (match, unmatch))
    super().__init__(tagsets, to_subkey, from_subkey)
    self.tagsets = tagsets
    self.accepts_key = accepts_key

  def __repr__(self):
    return "%s(match=%r,unmatch=%r)" % (
        type(self).__name__, self.__match, self.__unmatch
    )

  @contextmanager
  def startup_shutdown(self):
    ''' Open/close the wrapped tagsets.
    '''
    with self.tagsets:
      yield

  def items(self):
    ''' Enumerate the `TagSet`s by name.
    '''
    return self.tagsets.items()

  def subtype_name(self, type_name):
    ''' Return the subkey used for `type_name`.
    '''
    subtype_name__ = self.subkey(type_name + '._')
    assert subtype_name__.endswith('._')
    return cutsuffix(subtype_name__, '._')

  def accepts_type(self, type_name):
    ''' Test whether this accepts the type `type_name`
        by probing `self.accepts_key(type_name+'._').
    '''
    return self.accepts_key(type_name + '._')

  def typedef(self, type_name):
    ''' Return the type definition `TagSet` for the type `type_name`.
    '''
    assert self.accepts_type(type_name)
    assert not type_name.startswith('type.')
    subtype_name = 'type.' + self.subkey(type_name)
    return self.tagsets[subtype_name]

  def type_names(self):
    return map(
        lambda subkey: self.key(cutprefix(subkey, 'type.')),
        self.tagsets.keys(prefix='type.')
    )

class TagsOntology(SingletonMixin, BaseTagSets):
  ''' An ontology for tag names.
      This is based around a mapping of names
      to ontological information expressed as a `TagSet`.

      Normally an object's tags are not a self contained repository of all the information;
      instead a tag just names some information.

      As a example, consider the tag `colour=blue`.
      Meta information about `blue` is obtained via the ontology,
      which has an entry for the colour `blue`.
      We adopt the convention that the type is just the tag name,
      so we obtain the metadata by calling `ontology.metadata(tag)`
      or alternatively `ontology.metadata(tag.name,tag.value)`
      being the type name and value respectively.

      The ontology itself is based around `TagSets` and effectively the call
      `ontology.metadata('colour','blue')`
      would look up the `TagSet` named `colour.blue` in the underlying `Tagsets`.

      For a self contained dataset this means that it can be its own ontology.
      For tags associated with arbitrary objects
      such as the filesystem tags maintained by `cs.fstags`
      the ontology would be a separate tags collection stored in a central place.

      There are two main categories of entries in an ontology:
      * metadata: other entries named *typename*`.`*value_key*
        contains a `TagSet` holding metadata for a value of type *typename*
        whose value is mapped to *value_key*
      * types: an optional entry named `type.`*typename* contains a `TagSet`
        describing the type named *typename*;
        really this is just more metadata where the "type name" is `type`

      Metadata are `TagSets` instances describing particular values of a type.
      For example, some metadata for the `Tag` `colour="blue"`:

          colour.blue url="https://en.wikipedia.org/wiki/Blue" wavelengths="450nm-495nm"

      Some metadata associated with the `Tag` `actor="Scarlett Johansson"`:

          actor.scarlett_johansson role=["Black Widow (Marvel)"]
          character.marvel.black_widow fullname=["Natasha Romanov"]

      The tag values are lists above because an actor might play many roles, etc.

      There's a convention for converting human descriptions
      such as the role string `"Black Widow (Marvel)"` to its metadata.
      * the value `"Black Widow (Marvel)"` if converted to a key
        by the ontology method `value_to_tag_name`;
        it moves a bracket suffix such as `(Marvel)` to the front as a prefix
        `marvel.` and downcases the rest of the string and turns spaces into underscores.
        This yields the value key `marvel.black_widow`.
      * the type is `role`, so the ontology entry for the metadata
        is `role.marvel.black_widow`

      this requires type information about a `role`.
      Here are some type definitions supporting the above metadata:

          type.person type=str description="A person."
          type.actor type=person description="An actor's stage name."
          type.character type=str description="A person in a story."
          type.role type_name=character description="A character role in a performance."
          type.cast type=dict key_type=actor member_type=role description="Cast members and their roles."

      The basic types have their Python names: `int`, `float`, `str`, `list`,
      `dict`, `date`, `datetime`.
      You can define subtypes of these for your own purposes
      as illustrated above.

      For example:

          type.colour type=str description="A hue."

      which subclasses `str`.

      Subtypes of `list` include a `member_type`
      specifying the type for members of a `Tag` value:

          type.scene type=list member_type=str description="A movie scene."

      Subtypes of `dict` include a `key_type` and a `member_type`
      specifying the type for keys and members of a `Tag` value:

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
  def _singleton_key(cls, tagsets=None, **_):
    return None if tagsets is None else id(tagsets)

  def __init__(
      self, tagsets: Union[BaseTagSets, dict, None] = None, **initial_tags
  ):
    if hasattr(self, 'tagsets'):
      return
    if tagsets is None or not isinstance(tagsets, BaseTagSets):
      tagsets = MappingTagSets(tagsets)
    self.__dict__.update(
        _subtagsetses=[],
        _type_name2subtype_name={},
        default_factory=getattr(
            tagsets, 'default_factory', lambda name: TagSet(_ontology=self)
        ),
    )
    self.add_tagsets(tagsets, None)
    for name, tagset in initial_tags.items():
      tagsets[name] = tagset

  def __str__(self):
    return str(self.as_dict())

  @contextmanager
  def startup_shutdown(self):
    ''' Open all the sub`TagSets` and close on exit.
    '''
    subs = list(self._subtagsetses)
    for subtagsets in subs:
      subtagsets.open()
    with super().startup_shutdown():
      yield
    for subtagsets in subs:
      subtagsets.close()

  @classmethod
  @pfx_method(with_args=True)
  def from_match(cls, tagsets, match, unmatch=None):
    ''' Initialise a `SubTagSets` from `tagsets`, `match` and optional `unmatch`.

        Parameters:
        * `tagsets`: a `TagSets` holding ontology information
        * `match`: a match function used to choose entries based on a type name
        * `unmatch`: an optional reverse for `match`, accepting a subtype
          name and returning its public name

        If `match` is `None`
        then `tagsets` will always be chosen if no prior entry matched.

        Otherwise, `match` is resolved to a function `match-func(type_name)`
        which returns a subtype name on a match and a false value on no match.

        If `match` is a callable it is used as `match_func` directly.

        if `match` is a list, tuple or set
        then this method calls itself with `(tagsets,submatch)`
        for each member `submatch` if `match`.

        If `match` is a `str`,
        if it ends in a dot '.', dash '-' or underscore '_'
        then it is considered a prefix of `type_name` and the returned
        subtype name is the text from `type_name` after the prefix
        othwerwise it is considered a full match for the `type_name`
        and the returns subtype name is `type_name` unchanged.
        The `match` string is a simplistic shell style glob
        supporting `*` but not `?` or `[`*seq*`]`.

        The value of `unmatch` is constrained by `match`.
        If `match` is `None`, `unmatch` must also be `None`;
        the type name is used unchanged.
        If `match` is callable`, `unmatch` must also be callable;
        it is expected to reverse `match`.

        Examples:

            >>> from cs.sqltags import SQLTags
            >>> from os.path import expanduser as u
            >>> # an initial empty ontology with a default in memory mapping
            >>> ont = TagsOntology()
            >>> # divert the types actor, role and series to my media ontology
            >>> ont.add_tagsets(
            ...     SQLTags(u('~/var/media-ontology.sqlite')),
            ...     ['actor', 'role', 'series'])
            >>> # divert type "musicbrainz.recording" to mbdb.sqlite
            >>> # mapping to the type "recording"
            >>> ont.add_tagsets(SQLTags(u('~/.cache/mbdb.sqlite')), 'musicbrainz.')
            >>> # divert type "tvdb.actor" to tvdb.sqlite
            >>> # mapping to the type "actor"
            >>> ont.add_tagsets(SQLTags(u('~/.cache/tvdb.sqlite')), 'tvdb.')
    '''
    if match is None:
      assert unmatch is None
      match_func = None
      unmatch_func = None
    elif callable(match):
      assert callable(unmatch)
      match_func = match
      unmatch_func = unmatch
    elif isinstance(match, str):
      if not match:
        raise ValueError("empty match string")
      if '?' in match or '[' in match:
        raise ValueError("match globs only support *, not ? or [seq]")
      if match.endswith(('.', '-', '_')):
        if len(match) == 1:
          raise ValueError("empty prefix")
        if '*' in match:
          match_re_s = fn_translate(match)
          assert match_re_s.endswith('\\Z')
          match_re_s = cutsuffix(match_re_s, '\\Z')
          match_re = re.compile(match_re_s)

          def match_func(type_name):
            ''' Glob based prefix match, return the suffix.
            '''
            m = match_re.match(type_name)
            if not m:
              return None
            subtype_name = type_name[m.end():]
            return subtype_name

          # TODO: define this function if there is exactly 1 asterisk
          unmatch_func = None

        else:

          def match_func(type_name):
            ''' Literal prefix match, return the suffix.
            '''
            subtype_name = cutprefix(type_name, match)
            if subtype_name is type_name:
              return None
            return subtype_name

          def unmatch_func(subtype_name):
            ''' Return the `subtype_name` with the prefix restored
            '''
            return match + subtype_name

      else:
        # not a prefix

        if '*' in match:

          def match_func(type_name):
            ''' Glob `type_name` match, return `type_name` unchanged.
            '''
            if fnmatch(type_name, match):
              return type_name
            return None

          # TODO: define unmatch_func is there is exactly 1 asterisk
          unmatch_func = None

        else:

          def match_func(type_name):
            ''' Fixed string exact `type_name` match, return `type_name` unchanged.
            '''
            if type_name == match:
              return type_name
            return None

          def unmatch_func(subtype_name):
            ''' Fixed string match
            '''
            assert subtype_name == match
            return subtype_name

    else:

      raise ValueError(
          "unhandled match value %s:%r" % (type(match).__name__, match)
      )

    return cls(
        tagsets=tagsets,
        match_func=match_func,
        unmatch_func=unmatch_func,
        type_map=IndexedMapping(pk='type_name')
    )

  def __bool__(self):
    ''' Support easy `ontology or some_default` tests,
        since ontologies are broadly optional.
    '''
    return True

  def as_dict(self):
    ''' Return a `dict` containing a mapping of entry names to their `TagSet`s.
    '''
    return dict(self.items())

  def items(self):
    ''' Yield `(entity_name,tags)` for all the items in each subtagsets.
    '''
    for subtagsets in self._subtagsetses:
      for entity_name, tags in subtagsets.items():
        yield subtagsets.key(entity_name), tags

  def keys(self):
    ''' Yield entity names for all the entities.
    '''
    for subtagsets in self._subtagsetses:
      for entity_name in subtagsets.keys():
        yield subtagsets.key(entity_name)

  def get(self, name, default=None):
    ''' Fetch the entity named `name` or `default`.
    '''
    subtagsets = self._subtagsets_for_key(name)
    return subtagsets.get(subtagsets.subkey(name), default)

  def __setitem__(self, name, tags):
    ''' Apply `tags` to the entity named `name`.
    '''
    subtagsets = self._subtagsets_for_key(name)
    subtags = subtagsets[subtagsets.subkey(name)]
    subtags.update(tags)

  def __delitem__(self, name):
    ''' Delete the entity named `name`.
    '''
    subtagsets = self._subtagsets_for_key(name)
    del subtagsets[subtagsets.subkey(name)]

  def subtype_name(self, type_name):
    ''' Return the type name for use within `self.tagsets` from `type_name`.
        Returns `None` if this is not a supported `type_name`.
    '''
    if self.match_func is None:
      return type_name
    try:
      return self.type_map.by_type_name[type_name]
    except KeyError:
      name = self.match_func(type_name)
      self.type_map.add = dict(type_name=type_name, subtype_name=name)
      return name

  def type_name(self, subtype_name):
    ''' Return the external type name from the internal `subtype_name`
        which is used within `self.tagsets`.
    '''
    if self.match_func is None:
      return subtype_name
    try:
      return self.type_map.by_subtype_name[subtype_name]
    except KeyError:
      name = self.unmatch_func(subtype_name)
      self.type_map.add = dict(type_name=name, subtype_name=subtype_name)
      return name

  @pfx_method(with_args=True)
  @typechecked
  def add_tagsets(self, tagsets: BaseTagSets, match, unmatch=None, index=0):
    ''' Insert a `_TagsOntology_SubTagSets` at `index`
        in the list of `_TagsOntology_SubTagSets`es.

        The new `_TagsOntology_SubTagSets` instance is initialised
        from the supplied `tagsets`, `match`, `unmatch` parameters.
    '''
    if isinstance(match, (list, tuple)):
      assert unmatch is None
      for match1 in match:
        self.add_tagsets(tagsets, match1, index=index)
    else:
      subtagsets = _TagsOntology_SubTagSets(tagsets, match, unmatch)
      self._subtagsetses.insert(index, subtagsets)

  @property
  def _default_tagsets(self):
    ''' The default `TagSets` instance
        i.e. the sets used for type names which are not specially diverted.
    '''
    return self._subtagsetses[-1].tagsets

  def _subtagsets_for_key(self, key):
    ''' Locate a `_TagsOntology_SubTagSets` for use with `key`,
        a tagset name.
        Returns the default subtagsets if no explicit match is found.
    '''
    for subtagsets in self._subtagsetses:
      if subtagsets.accepts_key(key):
        return subtagsets
    return self._default_tagsets()

  def _subtagsets_for_type(self, type_name):
    ''' Locate a `_TagsOntology_SubTagSets` for use with the type `type_name`.
        Returns the default subtagsets if no explicit match is found.
    '''
    for subtagsets in self._subtagsetses:
      if subtagsets.accepts_type(type_name):
        return subtagsets
    return self._default_tagsets()

  ##################################################################
  # Types.

  def typedef(self, type_name):
    ''' Return the `TagSet` defining the type named `type_name`.
    '''
    subtagsets = self._subtagsets_for_type(type_name)
    subtype_name = subtagsets.subkey(type_name)
    return subtagsets.typedef(subtype_name)

  def type_names(self):
    ''' Return defined type names i.e. all entries starting `type.`.
    '''
    return set(
        subtagsets.key(subtype_name)
        for subtagsets in self._subtagsetses
        for subtype_name in subtagsets.type_names()
    )

  def types(self):
    ''' Generator yielding defined type names and their defining `TagSet`.
    '''
    for type_name in self.type_names():
      yield type_name, self.typedef(type_name)

  def by_type(self, type_name, with_tagsets=False):
    ''' Yield keys or (key,tagset) of type `type_name`
        i.e. all keys commencing with *type_name*`.`.
    '''
    type_name_ = type_name + '.'
    subtagsets = self._subtagsets_for_type(type_name)
    subtype_name_ = subtagsets.subtype_name(type_name) + '.'
    tagsets = subtagsets.tagsets
    if with_tagsets:
      for subkey, tags in tagsets.items(prefix=subtype_name_):
        assert subkey.startswith(subtype_name_)
        key = subtagsets.key(subkey)
        assert key.startswith(type_name_)
        yield key, tags
    else:
      for subkey in tagsets.keys(prefix=subtype_name_):
        assert subkey.startswith(subtype_name_)
        key = subtagsets.key(subkey)
        assert key.startswith(type_name_)
        yield key

  ################################################################
  # Metadata.

  @staticmethod
  @pfx
  def value_to_tag_name(value):
    ''' Convert a tag value to a tagnamelike dotted identifierish string
        for use in ontology lookup.
        Raises `ValueError` for unconvertable values.

        We are allowing dashes in the result (UUIDs, MusicBrainz discids, etc).

        `int`s are converted to `str`.

        Strings are converted as follows:
        * a trailing `(.*)` is turned into a prefix with a dot,
          for example `"Captain America (Marvel)"`
          becomes `"Marvel.Captain America"`.
        * the string is split into words (nonwhitespace),
          lowercased and joined with underscores,
          for example `"Marvel.Captain America"`
          becomes `"marvel.captain_america"`.
    '''
    if isinstance(value, int):
      return str(value)
    if isinstance(value, str):
      value = value.strip()
      m = re.match(r'(.*)\(([^()]*)\)\s*$', value)
      if m:
        value = m.group(2).strip() + '.' + m.group(1).strip()
      value = '_'.join(value.lower().split())
      return value
    raise ValueError(value)

  @tag_or_tag_value
  @require(lambda type_name: isinstance(type_name, str))
  def metadata(self, type_name, value, *, convert=None):
    ''' Return the metadata `TagSet` for `type_name` and `value`.
        This implements the mapping between a type's value and its semantics.

        The optional parameter `convert`
        may specify a function to use to convert `value` to a tag name component
        to be used in place of `self.value_to_tag_name` (the default).

        For example, if a `TagSet` had a list of characters such as:

            character=["Captain America (Marvel)","Black Widow (Marvel)"]

        then these values could be converted to the dotted identifiers
        `character.marvel.captain_america`
        and `character.marvel.black_widow` respectively,
        ready for lookup in the ontology
        to obtain the "metadata" `TagSet` for each specific value.
    '''
    md = None
    typedef = (
        TagSet() if type_name == 'type' else self.metadata('type', type_name)
    )
    primary_type_name = typedef.type_name
    if primary_type_name:
      type_name = primary_type_name
    if not isinstance(value, str):
      # strs look a lot like other sequences, sidestep the probes
      try:
        items = value.items
      except AttributeError:
        # not a mapping
        try:
          it = iter(value)
        except TypeError:
          # not iterable
          pass
        else:
          md = [self.metadata(type_name, item, convert=convert) for item in it]
      else:
        # a mapping
        # split the type_name on underscore to derive key and member type names
        # otherwise fall back to {type_name}_key, {type_name}_member
        try:
          key_type_name, member_type_name = type_name.split('_')
        except ValueError:
          key_type_name = type_name + '_key'
          member_type_name = type_name + '_member'
        md = {
            k: (
                self.metadata(key_type_name, k, convert=convert),
                self.metadata(member_type_name, v, convert=convert),
            )
            for k, v in items
        }
    if md is None:
      # neither mapping nor iterable
      # fetch the metadata TagSet
      subtagsets = self._subtagsets_for_type(type_name)
      if value is None:
        value_key = '_'
      else:
        if convert is None:
          convert = self.value_to_tag_name
        value_key = convert(value)
        assert isinstance(value_key, str) and value_key
      key = type_name + '.' + value_key
      md = subtagsets[value_key]
    return md

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

class TagFile(SingletonMixin, BaseTagSets):
  ''' A reference to a specific file containing tags.

      This manages a mapping of `name` => `TagSet`,
      itself a mapping of tag name => tag value.
  '''

  @classmethod
  def _singleton_key(cls, filepath, **_):
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

  # Mapping methods, proxying through to .tagsets.
  def keys(self, *, prefix=None):
    ''' `tagsets.keys`

        If the options `prefix` is supplied,
        yield only those keys starting with `prefix`.
    '''
    ks = self.tagsets.keys()
    if prefix:
      ks = filter(lambda k: k.startswith(prefix), ks)
    return ks

  def __delitem__(self, name):
    del self.tagsets[name]

  def _loadsave_signature(self):
    ''' Compute a signature of the existing names and `TagSet` id values.
        We use this to check for added/removed `TagSet`s at save time.
    '''
    return set((name, id(tags)) for name, tags in self.items() if tags)

  def is_modified(self):
    ''' Test whether this `TagSet` has been modified.
    '''
    tagsets = self._tagsets
    if tagsets is None:
      return False
    sig = self._loadsave_signature()
    if self._loaded_signature != sig:
      return True
    return any(map(lambda tagset: tagset.modified, tagsets.values()))

  @locked_property
  @pfx_method
  def tagsets(self):
    ''' The tag map from the tag file,
        a mapping of name=>`TagSet`.

        This is loaded on demand.
    '''
    ts = {}
    loaded_tagsets, unparsed = self.load_tagsets(self.filepath, self.ontology)
    self.unparsed = unparsed
    ont = self.ontology
    for name, tags in loaded_tagsets.items():
      te = ts[name] = self.default_factory(name)
      te.ontology = ont
      te.update(tags)
      te.modified = False
    self._tagsets = ts
    self._loaded_signature = self._loadsave_signature()
    return ts

  @property
  def names(self):
    ''' The names from this `FSTagsTagFile` as a list.
    '''
    return list(self.tagsets.keys())

  @classmethod
  @pfx_method
  def parse_tags_line(
      cls, line, ontology=None, verbose=None, extra_types=None
  ):
    ''' Parse a "name tags..." line as from a `.fstags` file,
        return `(name,TagSet)`.
    '''
    if extra_types is None:
      extra_types = getattr(cls, 'EXTRA_TYPES', None)
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
    tags = TagSet.from_line(
        line,
        offset,
        extra_types=extra_types,
        ontology=ontology,
        verbose=verbose
    )
    return name, tags

  @classmethod
  def load_tagsets(cls, filepath, ontology, extra_types=None):
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
                name, tags = cls.parse_tags_line(
                    line, extra_types=extra_types, ontology=ontology
                )
              except ValueError as e:
                warning("parse error: %s", e)
                unparsed.append((lineno, line0))
              else:
                tags.modified = False
                if 'name' in tags:
                  warning("discard explicit tag name=%s", tags.name)
                  tags.discard('name')
                tagsets[name] = tags
      except OSError as e:
        if e.errno != errno.ENOENT:
          raise
      return tagsets, unparsed

  @classmethod
  def tags_line(cls, name, tags, extra_types=None):
    ''' Transcribe a `name` and its `tags` for use as a `.fstags` file line.
    '''
    if extra_types is None:
      extra_types = getattr(cls, 'EXTRA_TYPES', None)
    fields = [Tag.transcribe_value(name, extra_types=extra_types)]
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
  def save_tagsets(cls, filepath, tagsets, unparsed, extra_types=None):
    ''' Save `tagsets` and `unparsed` to `filepath`.

        This method will create the required intermediate directories
        if missing.

        This method *does not* clear the `.modified` attribute of the `TagSet`s
        because it does not know it is saving to the `Tagset`'s primary location.
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
            f.write(cls.tags_line(name, tags, extra_types=extra_types))
            f.write('\n')
      except OSError as e:
        error("save(%r) fails: %s", filepath, e)

  def save(self, extra_types=None):
    ''' Save the tag map to the tag file if modified.
    '''
    tagsets = self._tagsets
    if tagsets is None:
      # never loaded - no need to save
      return
    with self._lock:
      if self.is_modified():
        # there are modified TagSets
        self.save_tagsets(
            self.filepath, tagsets, self.unparsed, extra_types=extra_types
        )
        self._loaded_signature = self._loadsave_signature()
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

  @contextmanager
  def run_context(self):
    with self.options.ontology:
      yield

  def cmd_edit(self, argv):
    ''' Usage: {cmd} [{{/name-regexp | entity-name}}]
          Edit entities.
          With no arguments, edit all the entities.
          With an argument starting with a slash, edit the entities
          whose names match the regexp.
          Otherwise the argument is expected to be an entity name;
          edit the tags of that entity.
    '''
    options = self.options
    ont = options.ontology
    if not argv:
      ont.edit()
    else:
      arg = argv.pop(0)
      if argv:
        raise GetoptError("extra arguments after argument: %r" % (argv,))
      if arg.startswith('/'):
        # select entities and edit them
        regexp = re.compile(arg[1:])
        ont.edit(select_tagset=lambda te: regexp.match(te.name))
      else:
        # edit a single entity's tags
        entity_name = arg
        tags = ont[entity_name]
        tags.edit()

  def cmd_meta(self, argv):
    ''' Usage: {cmd} tag=value
    '''
    options = self.options
    ont = options.ontology
    if not argv:
      raise GetoptError("missing tag=value")
    tag_s = argv.pop(0)
    tag = Tag.from_str(tag_s, ontology=ont)
    print(tag)
    md = tag.metadata()
    for md_tag in md:
      print(" ", md_tag)

  # pylint: disable=too-many-locals,too-many-branches
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
          {cmd} type_name ls
            List the metadata names for this type and their tags.
          {cmd} type_name + entity_name [tags...]
            Create type_name.entity_name and apply the tags.
    '''
    options = self.options
    ont = options.ontology
    if not argv:
      # list defined types
      print("Types:")
      for type_name, tags in ont.types():
        print(type_name, tags)
      return 0
    type_name = argv.pop(0)
    with Pfx(type_name):
      tags = ont.typedef(type_name)
      if not argv:
        print("Tags for type", type_name, "=", tags)
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
            # obtain the collection
            type_name_ = type_name + '.'
            tagset_map = {}
            for key, tagset in ont.by_type(type_name, with_tagsets=True):
              assert key.startswith(type_name_)
              print("%r vs %r" % (key, argv))
              if any(map(lambda ptn: fnmatchcase(key, ptn), argv)):
                print("matched")
                subkey = cutprefix(key, type_name_)
                assert subkey not in tagset_map
                tagset_map[subkey] = tagset
            for old_subkey, new_subkey, new_tags in TagSet.edit_many(
                tagset_map, verbose=True):
              tags = tagset_map[old_subkey]
              if old_subkey != new_subkey:
                warning(
                    "rename not implemented, skipping %r => %r", old_subkey,
                    new_subkey
                )
              tags.set_from(new_tags, verbose=True)
          return 0
        if subcmd in ('list', 'ls'):
          if argv:
            raise GetoptError("extra arguments: %r" % (argv,))
          for key, tags in sorted(ont.by_type(type_name, with_tagsets=True)):
            print(key, tags)
          return 0
        if subcmd == '+':
          if not argv:
            raise GetoptError("missing entity_name")
          entity_name = argv.pop(0)
          print("entity_name =", entity_name)
          etags = ont.metadata(type_name, entity_name)
          print("entity tags =", etags)
          for arg in argv:
            with Pfx("%s", arg):
              tag = Tag.from_str(arg)
              etags.add(tag)
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

def selftest(argv):
  ''' Run some ad hoc self tests.
  '''
  from pprint import pprint  # pylint: disable=import-outside-toplevel
  setup_logging(argv.pop(0))
  ont = TagsOntology(
      {
          'type.colour':
          TagSet(description="a colour, a hue", type="str"),
          'colour.blue':
          TagSet(
              url='https://en.wikipedia.org/wiki/Blue',
              wavelengths='450nm-495nm'
          ),
      }
  )
  print(ont)
  tags = TagSet(colour='blue', labels=['a', 'b', 'c'], size=9, _ontology=ont)
  print("tags =", tags)
  print(tags['colour'])
  colour = Tag('colour', 'blue')
  print(colour)
  print(colour.metadata(ontology=ont))
  print("================")
  tags['aa.bb'] = 'aabb'
  tags['aa'] = 'aa'
  pprint(tags.as_dict())
  for format_str in argv:
    print("FORMAT_STR =", repr(format_str))
    ##formatted = format(tags, format_str)
    formatted = tags.format_as(format_str)
    print("tag.format_as(%r) => %s" % (format_str, formatted))

if __name__ == '__main__':
  import sys
  sys.exit(selftest(sys.argv))
