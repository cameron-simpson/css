#!/usr/bin/env python3

''' Tagged information entities, built on `TagSet`s for representation
    and typically an `SQLTags` for storage.
    I use these to persist and mediate knowledge, including my interactions
    with web sites, APIs, and third party databases.
'''

DISTINFO = {
    'keywords': ["python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.deco',
        'cs.lex',
        'cs.obj',
        'cs.tagset',
    ],
    'python_requires':
    '>=3.9',  # for subscripting builtin types
}

from collections import defaultdict
from collections.abc import Sequence
from contextlib import contextmanager
from functools import cached_property
from types import GenericAlias
from typing import Self, Union
from uuid import UUID
from weakref import WeakValueDictionary

from icontract import require
from typeguard import typechecked

from cs.cmdutils import vprint
from cs.context import stackkeys
from cs.deco import default_params, Promotable, uses_verbose
from cs.lex import format_attribute, FormatMapping, FormatableMixin, printt, r
from cs.logutils import warning, vwarning, vvwarning
from cs.obj import public_subclasses, Refreshable, NoAttrs
from cs.pfx import pfx_call, pfx_method
from cs.progress import progressbar
from cs.tagset import TagSet, ZonedTypes
from cs.trace import Trace


class Entity(ZonedTypes, Refreshable, Promotable, FormatableMixin, NoAttrs):
  ''' A base class for classes which have a `.tags:TagSet` attribute
      and a `.tags_db:BaseTagSets` containing other `Tagset`s.

      Usually these are considered part of a "zone" - a group of
      entities in a particular applicaiton domain.

      The subclass may itself define its `.tags` instance attribute
      or rely on the default cached property `.tags`, which will return
      `self.tags_db[self.tags_entity_key]`.
      (`self.tags_entity_key` is `self.tags.name` by default.)

      Note that this mixin brings its own `__new__` method which
      can choose a subclass based on the subclass' `.TYPE_SUBNAME`
      attribute. See the `__new__` docstring.

      This also provides some behaviour based around updating
      entities based on some kind of API call; the direct values
      from the API call land on attributes named `{zone}.{key}` -
      the `.type_zone_update(mapping)` provides a convenient call
      for this.

      `Entity` instances are designed as representing entities in
      some "zone", a set of entities in some domain or organised
      grouping; typical examples include entities describes by some
      API like MusicBrainzNG or objects presented by some website.
      As such, they subclass `ZonedTypes`, which expects the entity's
      `.name` to be of the form *zone*`.`*subname*`.`*key*; the
      *zone* partitions entities off into their own domain, the
      *subname* is in effect the entity's type within that domain
      and the *key* is the entity id within that type.

      On this basis, entities updated with data from the zone,
      for example from an API call or by scraping a web page,
      normally update tag keys named *zone*`.`*field* where the *field*
      is the top level field from the data.

      The `ZonedTypes.__getattr__(attr)` method looks first for a
      direct tag named `attr` but falls back to a tag named
      *zone*`.`*attr*. This allows entities to be tagged with the
      data from an API, but to be overridden by the direct tag if
      the API data are considered incorrect or unsuitable.

      The `ScanData.apply()` method follows this principle,
      applying the scanned data to tags named *zone*`.`*field*.

      We relate entities using attributes named *field*`_id`,
      which may be a single key for another entity or a list of keys.

      Various derived attributes are also provided, see the
      `__getattr__` docstring for details:
      - *field*`_ent`: the related `Entity` named *zone*`.`*field*`.`*key*
        where *key* comes from the `.`*field*`_id` attribute
      - *field*`_ents`: multiple related `Entity` named
        *zone*`.`*field*`.`*key* where *key* comes from the `.`*field*`_id` attribute
  '''

  def __new__(cls, tags: TagSet, *_, **__):
    ''' Scan the subclasses of `cls` to see if one has a `.TYPE_SUBNAME`
        attribute matching `tags.type_subname`. If so, return an instance
        of the subclass, otherwise return an instance of `cls`.

        Note that because of the mechanics of `__new__` the new
        instance's `__init__` method will be called twice, and
        therefore it should be both cheap and safe to run twice.
        Because this mixin is oriented around instances which keep
        most or all of their state in the `.tags` attribute, usually
        the initialiser just sets `self.tags` and some other
        attributes.
    '''
    type_subname = tags.type_subname
    for subclass in public_subclasses(cls):
      if getattr(subclass, 'TYPE_SUBNAME', None) == type_subname:
        return super().__new__(subclass)
    strict = getattr(cls, 'STRICT_SUBTYPES', True)
    if strict:
      raise ValueError(
          f'no subclass of {cls.__name__} found for {type_subname=}'
          f' and {cls.__name__}.STRICT_SUBTYPES={strict}'
          f'; public subclasses are {", ".join(sorted(map(lambda subcls: subcls.__name__, public_subclasses(cls))))}'
      )
    # return the generic version
    return super().__new__(cls)

  def __init__(self, tags: TagSet, tags_db: "Entities" = None):
    super().__init__()
    if tags_db is None:
      # TODO: I should use Entity.default(zone) but only if it remembers
      # so leaving this breaking if the Entities subclass isn't defined
      tags_db = Entities.by_type_zone[self.TYPE_ZONE]
    self.tags = tags
    self.tags_db = tags_db

  def __str__(self):
    return f'{self.__class__.__name__}:{self.tags.name}'

  def __repr__(self):
    return f'{self.__class__.__name__}:{self.tags}'

  def __hash__(self):
    return hash((hash(self.tags['name']), id(self.tags_db)))

  def __eq__(self, other):
    return (
        self.tags['name'] == other.tags['name']
        and self.tags_db is other.tags_db
    )

  @classmethod
  def NOTYET__class_getitem__(cls, index):
    if isinstance(index, type):
      return GenericAlias(cls, (index,))
    zone = cls.TYPE_ZONE
    entities = cls.default(zone)
    return entities[index]

  def __getattr__(self, attr):
    ''' Try `ZonedTypes.__getattr__` (which lokks up `[attr]` then `[f'{zone}.{attr}']`)
        then fall back to suffix based synthetic attributes where
        an attribute ending in `_`*suffix* is implemented by the
        `suffix_`*suffix*`(attr)` method if it exists.

        The following synthetic attibutes are implemented:
        - *attr0*`_or_none`: return `.attr0` or `None` if that does not exist
        - *subtype*`_ent`: the entity with name
          *type_zone*`.`*subtype*`.`*id* or `None` where `id` comes
          from the `.`*attr*`_id` value;
          see the `suffix_ent` method.
        - *subtype*`_ents`: the entities with name
          *type_zone*`.`*subtype*`.`*id* or `None` where each `id` comes
          from the `.`*attr*`_id` values;
          see the `suffix_ents` method.
    '''
    try:
      return super().__getattr__(attr)
    except AttributeError:
      if attr.endswith('_or_none'):
        attr0 = attr[:-8]
        try:
          return getattr(self, attr0)
        except AttributeError as e:
          vvwarning(f'{self.__class__.__name__}.{attr=}: no .{attr0=}: {e}')
          return None
      try:
        base, suffix = attr.rsplit('_', 1)
      except ValueError:
        pass
      else:
        if base:
          cls = self.__class__
          suffix_handler = getattr(cls, f'suffix_{suffix}', None)
          if suffix_handler is not None:
            try:
              return pfx_call(suffix_handler, self, attr)
            except ValueError as e:
              raise AttributeError(
                  f'{self.__class__.__name__}.{attr}: {e}'
              ) from e
      raise

  @classmethod
  def from_str(cls, name):
    zone, subname, key = ZonedTypes.type_parts_of(name)
    return cls[zone][f'{subname}.{key}']

  @cached_property
  def tags(self):
    ''' A default `.tags` property which obtains a `TagSet` from `self.tags_db`
        via using the `TagSet` name `self.tags_entity_key`.
        This is for subclasses which might fetch the `.tags` on demand.

        Subclasses typically set `.tags` during `__init__` and
        therefore have no need for a `.tags_entity_key` property.
    '''
    return self.tags_db[self.tags_entity_key]

  def as_dict(self):
    ''' Proxy `.as_dict()` to `self.tags`.
    '''
    return self.tags.as_dict()

  @format_attribute
  def json(self):
    return self.tags.json()

  def printt(self, label=None, **printt_kw):
    if label is None:
      label = str(self)
    return printt([label], self.as_dict(), **printt_kw)

  def print(self):
    ''' The default `print()` runs `self.printt()`.
        This is intended to be a nice print of important stuff.
    '''
    return self.printt()

  @cached_property
  def tags_entity_key(self):
    ''' Our tagged entity key, `self.tags.name`.

        This is only really needed by the `.tags` cached
        property; most subclasses of `Entity` set `.tags` during
        `__init__`.
        If you have an "on demand" subclass you should override
        this method to compute the entity key without relying on
        the (missing) `.tags` attribute.
    '''
    if 'tags' not in self.__dict__:
      raise RuntimeError(
          f'{self.__class__.__name__}:HasSQLTags.tags_entity_key: no .tags attribute!'
      )
    return self.tags.name

  def __contains__(self, key):
    return key in self.tags

  def __delitem__(self, tag_name: str):
    ''' Remove an entry from `self.tags`.
    '''
    del self.tags[tag_name]

  def __getitem__(self, tag_name: str):
    ''' Index `self.tags`.
    '''
    return self.tags[tag_name]

  @uses_verbose
  def __setitem__(self, tag_name, value, *, verbose=False):
    ''' Set a tag value.
    '''
    self.tags[tag_name] = value

  def get(self, tag_name: str, default=None):
    ''' Call `.tags.get(tag_name)`.
    '''
    return self.tags.get(tag_name, default)

  def __iter__(self):
    return iter(self.keys())

  def keys(self):
    return self.tags.keys()

  def values(self):
    ''' The tags values.
    '''
    return self.tags.values()

  def items(self):
    ''' The tags items.
    '''
    return self.tags.items()

  def setdefault(self, key, default_value):
    ''' Set `self[key]=default_value` if `key` is not present.
    '''
    return self.tags.setdefault(key, default_value)

  def update(self, *update_a, **update_kw):
    ''' Update the tags, tupically from a mapping or keyword arguments.
    '''
    self.tags.update(*update_a, **update_kw)

  #######################################################
  # Methods supports FormatableMixin

  def get_arg_name(self, field_name):
    return self.tags.get_arg_name(field_name)

  def format_kwargs(self):
    ''' A `format_kwargs` method to support `cs.lex.FormatableMixin`.
    '''
    tags = self.tags
    kwargs = dict(tags)
    # Allow the format attributes to override the tags,
    # partly for correctness and partly to allow fallback if a tag is missing,
    # since the attribute's implementation  might choose the tag if present.
    kwargs.update(self.format_attributes)
    # TODO: grab type_* from ZonedTypes.__dict__.keys() ?
    for type_attr in (
        'type_key',
        'type_name',
        'type_subname',
        'type_zone',
        'type_zone_key',
    ):
      kwargs[type_attr] = getattr(self, type_attr)

    def missing(kwargs, key):
      ''' Look up this missing key as a method of the `tags`.
      '''
      try:
        method = getattr(tags, key)
      except AttributeError:
        raise KeyError(key)
      if not getattr(method, 'is_format_attribute', False):
        raise KeyError(key)
      return method

    return FormatMapping(self, kwargs, missing)

  def type_zone_update(self, mapping, prefix=None, *, lc_=False):
    ''' Update `self` with `mapping`, using `prefix`.
        The default `prefix` is self.type_zone`.
    '''
    if prefix is None:
      prefix = self.type_zone
    self.update(mapping, lc_=lc_, prefix=prefix)

  #################################################################
  # Properties supporting Refreshable.
  def refresh_key(self):
    ''' The unique key identifying this object for use in recursive refreshes.
    '''
    return self.name

  @property
  def refresh_last_update(self):
    ''' The last time a refresh update time.
    '''
    return self.tags.refresh_last_update

  @refresh_last_update.setter
  def refresh_last_update(self, when: float):
    ''' Save the last refresh update time.
    '''
    self.tags.refresh_last_update = when

  #################################################################
  # Attribute suffix resolvers.

  @require(lambda attr: attr.endswith('_ent'))
  def suffix_ent(self, attr) -> Self | None:
    ''' Resolve *subtype*`_ent` to `self[type_zone.`*subtype*`.id]`
        or `None` if no `self[`*subtype*`_id]`
    '''
    ref_subtype = attr.removesuffix('_ent')
    ref_key = f'{ref_subtype}_id'
    idvalue = getattr(self, ref_key, None)
    if idvalue is None:
      return None
    # demote sequence to first value or None
    if not isinstance(idvalue, str) and isinstance(idvalue, Sequence):
      if len(idvalue) == 0:
        return None
      idvalue = idvalue[0]
    ref_name = f'{self.type_zone}.{ref_subtype}.{idvalue}'
    return self.tags_db[ref_name]

  @require(lambda attr: attr.endswith('_ents'))
  def suffix_ents(self, attr) -> Sequence[Self]:
    ''' Resolve *subtype*`_ents` to [self[type_zone.`*subtype*`.id]]`
        or `()` if no `self[`*subtype*`_id]]`.
    '''
    ref_subtype = attr.removesuffix('_ents')
    ref_key = f'{ref_subtype}_id'
    idvalues = getattr(self, ref_key, None)
    if idvalues is None:
      return ()
    # promote scalar to list
    if isinstance(idvalues, str) or not isinstance(idvalues, Sequence):
      idvalues = [idvalues]
    ents = [
        # NB: no zone because Entities.__getitem__ knows its zone
        self.tags_db[ref_subtype, idvalue] for idvalue in idvalues
    ]
    return ents

  @require(lambda attr: attr.beginswith('in_'))
  def prefix_in(self, attr) -> Sequence[Self]:
    ''' Resolve `in_`*subtype*[`_`*field* to the `Entity` instance
        of subtype *subtype* whose *field*`_id` attribute contains
        `self.type_key`.
        The default *field* is `self.type_subname`.

        For example, if `self.name` is `"tvdb.episode.1234"` then
        `self.in_season` would return a list of all the `tvdb.season`
        entities whose `episode_id` attributes referred to `1234`.

        Where the
    '''
    ref_subtype = attr.removeprefix('in_')
    try:
      ref_subtype, ref_field = ref_subtype.rsplit('_', 1)
    except ValueError:
      ref_field = self.type_subname
    print(f'{ref_subtype} {ref_field}')
    breakpoint()

  #################################################################
  # The .entity and .entity_ namespaces, mapping to other Entity
  # instances from the id.* tags.

  class _EntityMap:
    ''' The `.entity` attribute space.
    '''

    def __init__(self, tags, missing_ok=False):
      self.tags = tags
      self.missing_ok = missing_ok

    def __getattr__(self, zone) -> "Entity":
      ''' Consult the tag `id.{zone}` and return the corresponding
          entity from the appropriate `Entities` instance.

          Example:

              tags = TagSet({'id.playon':'recording.1234567'})
              playon_recording = tags.entity.playon
      '''
      zone_id_tag = f'id.{zone}'
      try:
        zone_key = self.tags[zone_id_tag]
      except KeyError as e:
        if self.missing_ok:
          return None
        raise AttributeError(f'no .entity.{zone}: {e}') from e
      entity_id = f'{zone}.{zone_key}'
      return Entities.by_entity_id(entity_id)

    def __iadd__(self, ent: str | ZonedTypes):
      ''' Store a reference to `ent` as the tag `ent.type_zone` with value `ent.zone_key`.
      '''
      if isinstance(ent, str):
        zone, subname, key = ZonedTypes.type_parts_of(ent)
      else:
        zone, subname, key = ent.type_parts
      self.tags[f'id.{zone}'] = f'{subname}.{key}'

    def __isub__(self, ent: ZonedTypes):
      ''' Remove the reference to `ent` if present.
          Raise `KeyError` if there is no reference.
          Raise `ValueError` if the reference is to another entity.
      '''
      tags = self.tags
      zone_key = tags[ent.type_zone]
      if zone_key != ent.zone_key:
        raise ValueError(
            '{tags.name!r}-={ent!r}: {ent.type_zone}=zone_key refers to a different entity'
        )
      del tags[ent.zone_key]

  @cached_property
  def entity(self):
    ''' The `.entity` attribute space, whose attributes map to
        entities which are `UsesTags` instances from the appropriate
        `Entities` instances according to their zone.

          Example:

              tags = TagSet({'id.playon':'recording.1234567'})
              playon_recording = tags.entity.playon
    '''
    return self._EntityMap(self)

  @cached_property
  def entity_(self):
    ''' The `.entity_` attribute space, whose attributes map to
        entities which are `UsesTags` instances from the appropriate
        `Entities` instances according to their zone.
        Unlike `.entity`, a missing `id.` tag returns `None` instead
        of raising `AttributeError`.

          Example:

              tags = TagSet({'id.playon':'recording.1234567'})
              playon_recording = tags.entity.playon
    '''
    return self._EntityMap(self, missing_ok=True)

class Entities:
  ''' A mixin to support classes which use a `.tagsets:BaseTagSets` attribute to store their data.

      Subclasses may define the following class attributes:
      - `EntityClass`: a subclass of `Entity` which represents data entities;
        the default is `Entity` which should be enough if there is no `.tYPE_ZONE`
      - `TYPE_ZONE`: the type zone identifying entities in the
        larger `BaseTagSets` data; if this is not supplied it is
        obtained from `EntityClass.TYPE_ZONE`, if defined

      A typical use subclasses `cs.sqltags.UsesSQLTags`, a subclass
      of this which uses an `SQLTags` as the storage backend.

      If there is a `.TYPE_ZONE`, the meaning of the type *zone*,
      *subname* and *key* are as described for the `ZonedTypes`
      class.
  '''

  # see if we can get by with the minimal example
  # typically a would use a class with a TYPE_ZONE
  EntityClass = Entity
  TagsetsClass = None

  # a mapping of type zone to Entities subclass
  class_by_type_zone = {}

  # a mapping of type zone names to their most recent Entities subclass instance
  by_type_zone = WeakValueDictionary()

  def __init_subclass__(cls, **kw):
    ''' Inititialise a subclass by defining `.TYPE_ZNE` if already present.
    '''
    if hasattr(cls, 'HasTagsClass'):
      warning(
          f'new Entities subclass {cls.__name__} has a .HasTagsClass, should have .EntityClass'
      )
      breakpoint()
    super().__init_subclass__(**kw)
    zone = getattr(cls, 'TYPE_ZONE', None)
    if zone is None:
      zone = getattr(cls.EntityClass, 'TYPE_ZONE', None)
    if zone is None:
      vwarning(
          f'new subclass {cls.__name__}, no cls.TYPE_ZONE or {cls.EntityClass.__name__}.TYPE_ZONE'
      )
    else:
      cls.TYPE_ZONE = zone
      cls.class_by_type_zone[zone] = cls

  def __init__(self, tagsets=None, **kw):
    super().__init__(**kw)
    cls = self.__class__
    ##print(f'{cls=}')
    ##print(f'{self.TagsetsClass=}')
    ##breakpoint()
    self.tagsets = tagsets or self.TagsetsClass()
    try:
      zone = cls.TYPE_ZONE
    except AttributeError:
      pass
    else:
      self.set_as_zone(zone, if_unset=True)

  @classmethod
  def default(cls, zone: str | None = None) -> "Entities":
    ''' Return the default `Entities` instance for `zone`.
        If `zone` is not defined it is taken from `cls.TYPE_ZONE`.
        Raise `KeyError` for an unregistered `zone`.
        Raise `TypeError` if there is no registered default
        and the class for `zone` cannot be instantiated with `entcls()`.
    '''
    if zone is None:
      zone = cls.TYPE_ZONE
    try:
      entities = cls.by_type_zone[zone]
    except KeyError as e:
      try:
        entcls = cls.class_by_type_zone[zone]
      except KeyError:
        raise KeyError(
            f'{cls.__module__}.{cls.__name__}.default({zone=}): no zone {zone=}'
        ) from e
      else:
        # make the default instance
        try:
          entities = entcls()
        except TypeError as e:
          raise KeyError(
              f'{cls.__module__}.{cls.__name__}.default({zone=}): cannot make default {entcls} instance for zone {zone=}: {e}'
          ) from e
    return entities

  @classmethod
  def __class_getitem__(cls, index):
    ''' An `Entities` subclass may be indexed with a string.

        If there is no `cls.TYPE_ZONE` the string is treated either as:
        - if the string ha no dots, a `TYPE_ZONE` value - the
          `Entities` instance for that zone is returned
        - if the string has dots, as an `Entity.name` and looked
          up with `cls.by_entity_id(index)`.

        If there is a `cls.TYPE_ZONE`, such as with a `SiteMap`,
        the string is treated as a `ZonedTypes.type_zone_key` and
        looked up as by indexing that zone's `Entities` instance.

        Example using `TheTVDBAPI`, which has a `TYPE_ZONE`:

            # fetch the TV series entity with id 1234
            # there is a TheTVDBAPI.TYPE_ZONE
            series = TheTVDBAPI['series.1234']

            # fetch an arbtrary Entity
            # the value of `TheTVDBAPI.TYPE_ZONE` is "tvdb"
            series = Entities['tvdb.series.1234']

        Example using `SiteMap`, the base class for site maps, and
        which has no `.TYPE_ZONE`:

            smh_map = SiteMap['smh']
            smh_topic = SiteMap['smh.topic.technology']
            smh_article = SiteMap['smh']['article.abcd']
    '''
    # preempt the typing stuff
    if isinstance(index, type):
      return GenericAlias(cls, (index,))
    if isinstance(index, str):
      try:
        zone = cls.TYPE_ZONE
      except AttributeError:
        if '.' in index:
          return cls.by_entity_id(index)
        return cls.by_type_zone[index]
      # has a TYPE_ZONE
      entities = cls.default(zone)
      return entities[index]
    raise TypeError(f'{type(index)}:{index!r} is not a type or a string')

  def set_as_zone(self, zone: str, if_unset=False):
    ''' Set this `Entities` instance as the one handling entities in `zone`.
    '''
    cls = self.__class__
    by_type_zone = cls.by_type_zone
    try:
      old = by_type_zone[zone]
    except KeyError:
      pass
    else:
      if if_unset or old is self:
        return
      warning(
          "%s.%s: type zone %r already mapped to %s, replacing with %s",
          cls.__module__, cls.__name__, zone, old, self
      )
      ##breakpoint()
    vprint(f'{cls.by_type_zone}.by_type_zone[{zone=}] = {self=}')
    by_type_zone[zone] = self

  @contextmanager
  def as_zone(self, zone=None):
    ''' Push this `Entities` instance as the default mapping for
        `zone`, whose default is `self.__class__.TYPE_ZONE`.
        Yields the zone, or `None` if there is no
    '''
    cls = self.__class__
    if zone is None:
      try:
        zone = cls.TYPE_ZONE
      except AttributeError:
        yield None
        return
    with stackkeys(cls.by_type_zone, **{zone: self}):
      yield zone

  @classmethod
  def by_entity_id(cls, entity_id: str) -> Entity:
    ''' Return the `Entity` instance corresponding to `entity_id`
        from the full tb
        Raise `ValueError` if `entity_id` cannot be parsed by
        `ZonedTypes.type_parts_of`.
        Raise `KeyError` if there is no `Entities` instance for the zone
        and we cannot make a default instance.
    '''
    zone, subname, key = ZonedTypes.type_parts_of(entity_id)
    tags_db = cls.default(zone)
    assert zone == tags_db.TYPE_ZONE
    return tags_db[subname, key]

  #############################################################################
  # Entity interface
  def zone_entity(self, zone: str) -> "Entity":
    ''' Return the `Entity` entity associated with a per-type-zone key.
        For example, `self.zone_entity('tvdb')` would return the entity
        for `tvdb.`*tvdb_id* where `tvdb_id` comes from `self['id.tvdb']`.
    '''
    assert '.' not in zone
    zone_key = self[f'id.{zone}']
    assert isinstance(zone_key, str) and '.' in zone_key, (
        f'no . in {zone_key=} (from self[{zone=}.id])'
    )
    entity_id = f'{zone}.{zone_key}'
    return self.by_entity_id(entity_id)

  def __getitem__(
      self,
      index: str | tuple[str, str | int] | tuple[str, str, str | int],
  ) -> Entity:
    ''' `self.__getitem__(index)` calls `self.entity(index)`.
    '''
    return self.entity(index, zone=None)

  @pfx_method
  def entity(
      self,
      index: str | tuple[str, str | int] | tuple[str, str, str | int],
      zone=None
  ) -> Entity:
    ''' Fetch the `Entity` instance for the supplied `index`.
        This underlies the `__getitem__` method.

        The meaning of the type *zone*, *subname* and *key* are as
        described for the `ZonedTypes` class.

        The `index` may take the following forms:
        - `str`: a string which will be split into *subname* and *key*
          for use in `self.TYPE_ZONE`
        - `(subname,key)`: a 2-tuple of the type *subname* and *key*
          in `self.TYPE_ZONE`
          the subname make also be a subclass of `self.EntityClass`
        - `(zone,subname,key)`: a 3-tuple of the type zone, subname and key
        The *subname* may also be a class (normally a subclass of
        `Entity`, usually a subclass of `type(self).EntityClass`);
        in this case the *subname* will be taken from `type(self).TYPE_SUBNAME`
        attribute.
        The *key* may also be an `int` or a `uuid.UUID`, in which
        case it will be used as `str(key)`.

        Examples:

            # the Entity subclass Artist, and the Entities
            # subclass MBDB which hold MusicbrainzNG information
            from cs.cdrip import Artist, MBDB
            mbdb = MBDB()

            # Various indices obtaining the record for Jon Cleary,
            # whose key is 'mbdb.artist.a417f0e5-2c14-445a-9a07-5a7ad2bdeafa'

            # the subname.key as a single string
            artist = mbdb['artist.a417f0e5-2c14-445a-9a07-5a7ad2bdeafa']

            # the subname and key in a 2-tuple
            artist = mbdb['artist', 'a417f0e5-2c14-445a-9a07-5a7ad2bdeafa']

            # the record but not from the default MBDB zonne
            artist = mbdb['mbdb2', 'artist', 'a417f0e5-2c14-445a-9a07-5a7ad2bdeafa']

            # the preferred way to obtain it, using the entity type
            artist = mbdb[Artist, 'a417f0e5-2c14-445a-9a07-5a7ad2bdeafa']

            # or if you're working with UUIDs
            artist_uuid = UUID('a417f0e5-2c14-445a-9a07-5a7ad2bdeafa')
            artist = mbdb[Artist, artist_uuid]
    '''
    if zone is None:
      zone = getattr(self, 'TYPE_ZONE', None)
    if zone is not None:
      assert isinstance(zone, str), f'zone={r(zone)} is not a str'
      assert '.' not in zone, f'dot in {zone=}'
    if isinstance(index, str):
      with Trace('index is a str') as sT:
        if zone is None:
          tag_key = index
        else:
          type_, key = index.rsplit('.', 1)
          # strip leading zone if it's ours (common misuse)
          type_ = type_.removeprefix(f'{zone}.')
          tag_key = f'{zone}.{type_}.{key}'
    elif isinstance(index, tuple):
      try:
        # (type,key) -> TYPE_ZONE, type, key
        type_, key = index
      except ValueError:
        # (zone,type,key)
        zone, type_, key = index
        tag_key = f'{zone}.{type_}.{key}'
      else:
        assert zone is not None
      # an Entity subclass may be named
      if isinstance(type_, type):
        type_ = type_.TYPE_SUBNAME
      if isinstance(key, (int, UUID)):
        key = str(key)
      assert '.' not in key, f'dot in {key=}'
      tag_key = f'{zone}.{type_}.{key}'
    else:
      raise TypeError(
          f'{self}[{r(index)}]: expected str or (subname|EntityType,key) or (zone,subname|EntityType,key)'
      )
    if zone is None or zone == getattr(self, 'TYPE_ZONE', None):
      # no zone or our zone
      entity_zone = self
    else:
      # an enitty with another zone may come from a different data store
      entity_zone = self.by_type_zone[zone]
    with Trace(f'get ent from {type(entity_zone).__name__}') as T:
      tags = entity_zone.tagsets[tag_key]
      ent = entity_zone.EntityClass(tags, entity_zone)
    return ent

  def keys(self, subname=None):
    ''' Return the keys from `self.tagsets` as `(subname,type_key)` 2-tuples
        suitable as indices of `self`.
        If `subname` is not `None`, restrict the keys to those with that subname.
    '''
    yield from map(
        lambda key: tuple(ZonedTypes.type_zone_key_of(key).rsplit('.', 1)),
        self.tagsets.keys(
            prefix=(
                f'{self.TYPE_ZONE}.' if subname is
                None else f'{self.TYPE_ZONE}.{subname}.'
            )
        )
    )

  def items(self, subname=None):
    for k in self.keys(subname=subname):
      yield k, self[k]

  def find(self, *criteria, **crit_kw) -> list[Entity]:
    ''' Find entities in the database.

        This calls `self.tagsets.find()` and returns the associated
        `Entity` instances.
    '''
    return [
        self.EntityClass(te, self)
        for te in self.tagsets.find(*criteria, **crit_kw)
    ]

class ScanData:
  ''' A class to manage data obtained about `Entity` instances,
      for example from an API or scanning a web page.
      This contains a `.entities` for the reference `Entities`
      and a `.ent_data_map` for the mapping from `Entity`
      instances to their scanned data `dict`.

      The data for an `Entity` can be obtained by indexing the
      `ScanData` instance with an `Entity` instance or a valid index
      for the `entities` such as a `(Entity-subclass, key)` 2-tuple.
      The (optional) supplied `entities` is only required for the
      tuple indexing, in order to resolve the index to an `Entity`.
  '''

  def __init__(self, entities: Entities | None = None, *, name: str = None):
    # mapping of Entity instances to a data dict
    self.name = name
    self.entities = entities
    self.ent_data_map = defaultdict(dict)

  def __iter__(self):
    ''' Iteration yields `(Entity,datadict)` 2-tuples.
    '''
    return iter(self.ent_data_map.items())

  ##@trace
  @typechecked
  def __getitem__(self, ent: Union[tuple, "Entity"]):
    ''' The data for the supplied `ent`.
    '''
    if isinstance(ent, tuple):
      if self.entities is None:
        print(f'NO ScanData.entities, tuple index {ent=}')
        breakpoint()
        raise KeyError(
            f'self.entities is None, tuple indices cannot be resolved: {ent=}'
        )
      ent = self.entities[ent]
    return self.ent_data_map[ent]

  def keys(self):
    return self.ent_data_map.keys()

  def update(self, ent: Union[tuple, "Entity"], **data_kw):
    ''' Update the data for `ent` from `data_kw`.
          If `ent` is a tuple, use it to obtain a `Entity` from `self.entities`.
      '''
    self[ent].update(**data_kw)

  def conv(self, ent: Union[tuple, "Entity"], mapping, key, conv=None):
    ''' Update the data for `ent` from `mapping[key]` if present.
        If `conv` is not `None` it should be a callable accepting
        the value from `mapping[key]` and returning a converted
        value to store in the entity data.
    '''
    try:
      value = mapping[key]
    except KeyError:
      warning("no mapping[%r]", key)
      return
    if conv is None:
      cvalue = value
    else:
      try:
        cvalue = conv(value)
      except ValueError as e:
        warning("%s(mapping[%r]:%r) -> %s", conv, key, value, e)
        return
    self.update(ent, **{key: cvalue})

  def apply(self, *refresh_ents):
    ''' Apply the scanned data to its entities.

        If an entity `ent` is a member of `refresh_ents` then call
        `ent.refresh(data=data)` on the basis that the data are
        complete enough to consider the entity refreshed, otherwise
        call `ent.type_zone_update(data)`.

        The purpose of the call to `ent.refresh()` is to exercise
        the refresh machinery. On a `Refreshable` object `ent` this
        marks the object as current with the new data; the data are
        applied with `Refreshable._refresh()`, the zone specific
        method, which typically _also_ uses `ent.type_zone_update(data)`.

        This follows the tag name design outlined in the `Entity` docstring,
        where API/site data are stored with tags named *zone*`.`*field*.
    '''
    ent_data = list(self)
    for ent, data in progressbar(
        ent_data,
        f'{self.__class__.__name__}{repr(self.name) if self.name else ""}.apply',
    ):
      if ent in refresh_ents:
        # if this is the sitepage, consider the entity refreshed
        ent.refresh(data=data)
      else:
        # otherwise just annotate it with whatever was learned
        ent.type_zone_update(data)

  def printt(self, title=None):
    ''' Call `cs.lex.printt()` to print the scanned data.
    '''
    rows = []
    if title is not None:
      rows.append(title)
    for ent, data in sorted(self, key=lambda ed: ed[0].name):
      rows.append(ent.name)
      rows.append(data)
    printt(*rows)

# provide a default scandata:ScanData parameter
uses_scandata = default_params(scandata=ScanData)
