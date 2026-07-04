#!/usr/bin/env pyuthon3

''' API and Pilfer SiteMap for thetvdb.com.
'''

from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from functools import cached_property, partial
from itertools import batched
import os
from os.path import basename
import re
import sys
from textwrap import wrap
from threading import Semaphore
from typing import Generator, Iterable, Optional

from cs.app.pilfer.pilfer import Pilfer, uses_pilfer
from cs.app.pilfer.sitemap import SiteEntity, SiteMap, on
from cs.bs4utils import child_tags
from cs.cmdutils import BaseCommand, GetoptError, popopts
from cs.context import stackattrs
from cs.deco import default_params, Promotable
from cs.lex import printt, r, s
from cs.logutils import warning
from cs.mappings import change_mapping
from cs.obj import Refreshable, SingletonMixin
from cs.pfx import Pfx, pfx_call
from cs.resources import RunState, uses_runstate
from cs.service_api import HTTPServiceAPI
from cs.sqltags import SQLTags
from cs.tagset import Entity, TagSet, Entities
from cs.threads import pmap

from bs4.element import Tag as BS4Tag
from requests.exceptions import HTTPError
from typeguard import typechecked

from cs.debug import trace, X, r, s, pprint, printt

uses_tvdb = default_params(tvdb_api=lambda: TheTVDBAPI())

class TVDBEntity(SiteEntity, Promotable):
  ''' The base class for TheTVDB entities.
  '''

  API_ENTITY_SUBPATH_FORMAT = '{type_subname}/{type_key}'
  TVDB_SUBENTITY_FIELDS = ()  # list of (fieldname,entity-type)
  TVDB_LINKENTITY_FIELDS = ()
  TVDB_ENTITYTYPE_BY_TVDB_TYPENAME = {}

  def __init_subclass__(cls):
    super().__init_subclass__()
    api_typename = getattr(cls, 'TVDB_API_TYPENAME', cls.__name__.lower())
    cls.TVDB_ENTITYTYPE_BY_TVDB_TYPENAME[api_typename] = cls

  @classmethod
  @uses_tvdb
  def from_str(cls, entity_spec, *, tvdb_api: "TheTVDBAPI") -> "TVDBEntity":
    ''' Recognise a TVDB object id such as `"series-1234"` and
        return the corresponding `TVDBEntity` instance.
    '''
    if '.' not in entity_spec and '-' in entity_spec:
      return tvdb_api.parse_object_id(entity_spec)
    if entity_spec.startswith(f'{tvdb_api.TYPE_ZONE}.'):
      return tvdb_api[cls.type_zone_key_of(entity_spec)]
    raise ValueError(f'cannot convert {entity_spec=} to {cls.__name__}')

  def field_entity_type(cls, field_name):
    for ref_field, ref_type in cls.TVDB_SUBENTITY_FIELDS:
      if ref_field == field_name:
        return ref_type
    raise ValueError(
        f'no entity type for {field_name=} in {cls}.TVDB_SUBENTITY_FIELDS={cls.TVDB_SUBENTITY_FIELDS}'
    )

  #################################################################
  # Refreshable support.
  @uses_tvdb
  def refresh(self, *rfa, tvdb_api: "TheTVDBAPI", map=None, **rfkw):
    ''' Call `Refreshable.refresh` with `map=tvdb_api.pmap`.
    '''
    if map is None:
      map = tvdb_api.pmap
    return super().refresh(*rfa, map=map, **rfkw)

  @property
  def refresh_resource(self):
    ''' The refresh resource, which is the API endpoint.
    '''
    return self.format_as(self.API_ENTITY_SUBPATH_FORMAT)

  @uses_tvdb
  def _refresh(self, subpath=None, *, data=None, tvdb_api: "TheTVDBAPI"):
    ''' Refresh this entity from the TVDB API.
    '''
    if data is None:
      if subpath is None:
        raise ValueError(
            f'no data or subpath provided to {self.__class__.__name__}:{self.name}._refresh()'
        )
      api_id = int(self.type_key)
      try:
        data = tvdb_api / subpath
      except HTTPError as e:
        if e.response.status_code == 404:
          warning(f'{self.name}:_refresh({subpath=}) not found: {e}')
          return False
        raise
      assert data["id"] == api_id, (
          f'TVDB API {data["id"]=} != {api_id=} (from {self.type_key=})'
      )
    self.update(data, prefix=self.type_zone)
    return True

  def refresh_related(self) -> Generator["TVDBEntity"]:
    ''' Yield related entities for use in a recursive refresh.
      '''
    for link_field, link_type in self.TVDB_LINKENTITY_FIELDS:
      link_id = self.get(link_field)
      if link_id is None:
        warning(f'{self.__class__.__name__}:{self.name}: no [{link_field=}]')
        continue
      ent = self.tags_db[link_type, link_id]
      yield ent
    for ref_field, ref_type in self.TVDB_SUBENTITY_FIELDS:
      ref = self.get(ref_field)
      if ref is None:
        warning(f'{self.__class__.__name__}:{self.name}: no [{ref_field=}]')
        continue
      for ref_data in ref:
        ref_id = ref_data["id"]
        ent = self.tags_db[ref_type, ref_id]
        yield ent

  def related(self, ref_field: str) -> Generator["TVDBEntity"]:
    ''' Yield entities specified by the records in `self[ref_field]`.
    '''
    ref_type = self.field_entity_type(ref_field)
    ref = self.get(ref_field)
    if ref is None:
      warning(f'{self.__class__.__name__}:{self.name}: no [{ref_field=}]')
      return
    for ref_data in ref:
      ref_id = ref_data["id"]
      ent = self.tags_db[ref_type, ref_id]
      yield ent

class Character(TVDBEntity):
  TYPE_SUBNAME = 'character'
  SITEPAGE_URL_FORMAT = '/characters/{type_key}'
  API_ENTITY_SUBPATH_FORMAT = 'characters/{type_key}'

class Company(TVDBEntity):
  TYPE_SUBNAME = 'company'
  SITEPAGE_URL_FORMAT = '/companies/{type_key}'
  API_ENTITY_SUBPATH_FORMAT = 'companies/{type_key}'

class Episode(TVDBEntity):
  TYPE_SUBNAME = 'episode'
  SITEPAGE_URL_FORMAT = '/series/{series_name!lc_}/episodes/{type_key}'
  API_ENTITY_SUBPATH_FORMAT = 'episodes/{type_key}/extended'

class Genre(TVDBEntity):
  TYPE_SUBNAME = 'genre'
  SITEPAGE_URL_FORMAT = '/genres/{type_key}'

class Movie(TVDBEntity):
  TYPE_SUBNAME = 'movie'
  SITEPAGE_URL_FORMAT = '/movies/{type_key}'
  API_ENTITY_SUBPATH_FORMAT = 'movies/{type_key}/extended'

class Person(TVDBEntity):
  TYPE_SUBNAME = 'person'
  SITEPAGE_URL_FORMAT = '/people/{type_key}'
  API_ENTITY_SUBPATH_FORMAT = 'people/{type_key}/extended'

class Season(TVDBEntity):
  TYPE_SUBNAME = 'season'
  SITEPAGE_URL_FORMAT = '/seasons/{fullname:lc_}'
  API_ENTITY_SUBPATH_FORMAT = 'seasons/{type_key}/extended'

class Series(TVDBEntity):
  TYPE_SUBNAME = 'series'
  SITEPAGE_URL_FORMAT = '/{type_subname}/{fullname:lc_}'
  API_ENTITY_SUBPATH_FORMAT = 'series/{type_key}/extended'

  @uses_runstate
  @uses_tvdb
  def print(self, *, runstate: RunState, tvdb_api: "TheTVDBAPI"):
    table = [[self.name, self.get('tvdb.name')]]
    characters = list(self.related('tvdb.characters'))
    seasons = list(self.related('tvdb.seasons'))
    list(tvdb_api.refresh_entities(characters + seasons))
    runstate.raiseif()
    if characters:
      table.append(['Characters', len(characters)])
      chartable = []
      actors = [
          self.sitemap[Person, character['tvdb.peopleId']]
          for character in characters
      ]
      list(tvdb_api.refresh_entities(actors))
      runstate.raiseif()
      for character, actor in zip(characters, actors):
        chartable.append(
            [
                f"{character.get('tvdb.name', 'no tvdb.name')} {character.name}",
                f"{character['tvdb.personName']} - {actor.name}",
            ]
        )
      table.append(tuple(chartable))
    if seasons:
      # there are repeated season entries, keep the last
      season_map = {season['tvdb.number']: season for season in seasons}
      table.append(['Seasons', len(seasons)])
      seatable = []
      for season_number, season in sorted(season_map.items()):
        runstate.raiseif()
        episodes = list(season.related('tvdb.episodes'))
        seatable.append(
            [
                (
                    f'Specials {season.name}' if season_number == 0 else
                    f'Season {season_number} {season.name}'
                ),
                (
                    'No episodes.' if len(episodes) == 0 else '1 episode.'
                    if len(episodes) == 1 else f'{len(episodes)} episodes.'
                ),
            ]
        )
        if episodes:
          list(tvdb_api.refresh_entities(episodes))
          # there are repeated episodes, keep the last
          episode_map = {
              episode['tvdb.number']: episode
              for episode in episodes
          }
          epitable = []
          for episode_number, episode in sorted(episode_map.items()):
            runstate.raiseif()
            episode.refresh()
            epitable.append(
                [
                    f'{episode_number} {episode.name}',
                    f'{episode.get("tvdb.name","no tvb.name")}\n{"\n".join(wrap(episode.get("tvdb.overview","No overview."),60))}'
                ]
            )
          seatable.append(tuple(epitable))
      table.append(tuple(seatable))
    else:
      table.append(['Seasons', 'None'])
    printt(*table)

# cross references for refresh recursion
Character.TVDB_LINKENTITY_FIELDS = (
    ('tvdb.peopleId', Person),
    ('tvdb.seriesId', Series),
)
Season.TVDB_SUBENTITY_FIELDS = (('tvdb.episodes', Episode),)
Series.TVDB_SUBENTITY_FIELDS = (
    ('tvdb.characters', Character),
    ('tvdb.seasons', Season),
)

class TheTVDBAPI(SingletonMixin, HTTPServiceAPI, Entities):

  TYPE_ZONE = 'tvdb'
  EntityClass = TVDBEntity
  EntitiesClass = SQLTags

  # for attrubting data, sourced from https://thetvdb.com/api-information#attribution
  ATTRIBUTION_HTML = 'Metadata provided by TheTVDB. Please consider adding missing information or subscribing.'
  ATTRIBUTION_URL = 'https://thetvdb.com/subscribe'
  ATTRIBUTION_ICON_URL = 'https://thetvdb.com/images/attribution/logo1.png'

  API_HOSTNAME = 'api4.thetvdb.com'
  API_BASE = f'https://{API_HOSTNAME}/v4/'
  TVDB_API_KEY_ENVVAR = 'TVDB_API_KEY'
  TVDB_API_TOKEN_ENVVAR = 'TVDB_API_TOKEN'

  # the default concurrency limit for API calls
  API_CONCURRENCY_LIMIT = 3

  @classmethod
  def _singleton_key(cls, api_key: str = None, **_):
    ''' The filesystem path for the `db_url` or `None`.
    '''
    if api_key is None:
      api_key = os.environ[cls.TVDB_API_KEY_ENVVAR]
    return cls.API_HOSTNAME, api_key

  def __init__(
      self,
      api_key: str = None,
      **httpapi_kw,
  ):
    if hasattr(self, 'api_key'):
      return
    super().__init__(
        mode="data", check_json={"status": "success"}, **httpapi_kw
    )
    if api_key is None:
      api_key = os.environ[self.TVDB_API_KEY_ENVVAR]
    self.api_key = api_key
    # reuse a token from the environment if present
    self.token = os.environ.get(self.TVDB_API_TOKEN_ENVVAR)

  def parse_object_id(self, type_id: str) -> TVDBEntity:
    ''' Resolve a TVDB entity spec such as `"series-1234"` into the `TVDBEntity` instance.
    '''
    api_typename, id_s = type_id.split('-')
    ent_type = TVDBEntity.TVDB_ENTITYTYPE_BY_TVDB_TYPENAME[api_typename]
    return self[ent_type, id_s]

  def login(self) -> dict:
    ''' POST to /login, return the login response data.
        This sets `self.token` and `self.default_headers['Authorization']`
        as a side effect.
    '''
    data = super().suburl(
        "login", method='POST', json={"apikey": self.api_key}
    )
    self.token = data["token"]
    return data

  @property
  def token(self) -> str:
    ''' Obtain the token.
    '''
    if self._token is None:
      self.login()
    return self._token

  @token.setter
  def token(self, new_token: str):
    self._token = new_token
    if new_token is None:
      self.default_headers.pop('Authorization', None)
    else:
      self.default_headers['Authorization'] = new_token

  def suburl(self, suburl, **suburl_kw):
    self.token
    return super().suburl(suburl, **suburl_kw)

  @cached_property
  def artwork_types(self):
    return self.data('artwork/types')

  def search(self,
             query: str,
             *,
             _rqkw=None,
             **search_params) -> list[tuple[dict, TVDBEntity]]:
    if _rqkw is None:
      _rqkw = {}
    params = dict(
        query=query,
        **search_params,
    )
    results = self.suburl('search', params=params, **_rqkw)
    return [(result, self.parse_object_id(result['id'])) for result in results]

  def refresh_entities(
      self,
      entities: Iterable[Refreshable],
      **refresh_kw,
  ) -> Generator[bool]:
    ''' Refresh the `entities` in parallel using `self.pmap`.
    '''
    return pmap(lambda ent: ent.refresh(**refresh_kw), entities)

@dataclass
class TheTVDBSite(SiteMap):
  ''' A site map for `thetvdb.com`.
  '''

  TYPE_ZONE = 'tvdb'
  HasTagsClass = TVDBEntity

  BASE_DOMAIN = 'thetvdb.com'
  URL_DOMAIN = f'www.{BASE_DOMAIN}'

  @staticmethod
  def parse_date(date_s) -> date:
    ''' Parse a *Month day, year* string into a `datetime.date`.
    '''
    dt = pfx_call(datetime.strptime, date_s, '%B %d, %Y')
    return date(dt.year, dt.month, dt.day)

  @classmethod
  @typechecked
  def parse_basic_info_div(cls, div: BS4Tag, entity_type: str) -> TagSet:
    ''' Parse the basic info `DIV`, return a `TagSet`.

        The basic info `DIV` contains a single `UL` whose `LI` tags
        contain a `<strong>` tag with the item title text and a
        number of `<span>` tags containing their values.
    '''
    tags = TagSet()
    ##for li in div.ul.find_all('li', recursive=False):
    for li in child_tags(div.ul, 'li'):
      with Pfx("LI %s", li):
        field_tag, *value_tags = child_tags(li)
        if field_tag.name != 'strong':
          warning("expected a leading <strong> tag, ignoring %s", r(field_tag))
          continue
        field_name = '_'.join(field_tag.get_text(strip=True).split()).lower()
        if field_name == f'thetvdb.com_{entity_type}_id':
          field_name = 'id'
        field_subname = None
        # Prepare filed as the object to store the parsed values.
        # A list for foreign entity references,
        # a mapping for various things,
        # None for unrecognised things.
        if field_name in ('genres', 'network'):
          # a list of keys to other entity types
          field_subname = {
              'network': 'company',
              'genres': 'genre',
          }[field_name]
          field = []
        elif field_name in (
            'created',
            'first_aired',
            'modified',
            'on_other_sites',
            'recent',
        ):
          field = {}
        else:
          assert len(value_tags) == 1
          field = None
        # now parse the tags after the field name
        for span in value_tags:
          span_flat = "\\n".join(map(str.strip, str(span).split("\n")))
          with Pfx("SPAN %s", span_flat):
            if span.name != 'span':
              warning("skipping nonspan")
              continue
            field_text = span.get_text(' ', strip=True)
            if field_name == 'id':
              field_key = None
              field_value = int(field_text)
            elif field_name in ('created', 'modified'):
              # Created MMM DD, YYYY by <div>username</div>
              date_s, by_user = field_text.split("\n", 1)
              when = cls.parse_date(date_s).isoformat()
              _by, username = by_user.split()
              assert _by == 'by'
              field_key = when
              field_value = username
            elif field_name == 'favorited':
              fav_count_s, = re.match(
                  fr'This {entity_type} has been favorited by (\d+) people\.',
                  field_text,
              ).groups(1)
              field_key = None
              field_value = int(fav_count_s)
            else:
              # we kind of expect an href or an href inside a span
              a = span.a if span.name == 'span' else span
              if field_name in ('first_aired', 'recent'):
                # an href surrounding a date
                field_key = cls.parse_date(field_text).isoformat()
                field_value = a['href']
              elif a:
                # an href surrounding some text
                field_key = field_text
                field_value = a['href']
              else:
                # no href, so no key, just use the text
                field_key = None
                field_value = field_text
            # apply the parsed value
            if field_key is None:
              # scalar text value
              assert field is None, f'{field_name=}, {field=}'
              field = field_value
            else:
              if field_subname is None:
                change_mapping(field, field_key, field_value, field_name)
              else:
                field.append(basename(field_value))
        change_mapping(tags, field_name, field, "tags")
    return tags

  @on(
      URL_DOMAIN,
      r'/(?P<entity_type>companies|genres|series)/(?P<entity_name>[^/]+)$',
      # the entity_key requires an id from the page contents
  )
  @uses_pilfer
  def grok_info_page(self, flowstate, match: TagSet, P: Pilfer = None):
    ''' Parse a `/series/{series_name}` page.
    '''
    with Pfx(
        "%s.grok_info_page(%s)",
        self.__class__.__name__,
        flowstate.url.short,
    ):
      type_subname = {
          'companies': 'company',
          'genres': 'genre',
          'series': 'series',
      }[match.entity_type]
      soup = flowstate.soup
      for div_id in {
          'series': ['series_basic_info'],
          'company': ['general'],
      }.get(type_subname, ()):
        with Pfx("div#%s", div_id):
          basic_info_div = soup.find(id='series_basic_info')
          if basic_info_div is None:
            warning("no DIV with id #%s", div_id)
            continue
          assert basic_info_div is not None
          try:
            basic_tags = self.parse_basic_info_div(
                basic_info_div, type_subname
            )
          except Exception as e:
            warning("parse_basic_info_div: %s", s(e))
            return None
          printt(["basic_tags"], *sorted(basic_tags.items()))
          tve = self[type_subname, basic_tags["id"]]
          tve.update(
              **{
                  k: v
                  for k, v in basic_tags.items()
                  if k not in ('id', 'name')
              }
          )
          title_h1 = soup.find('h1', id='series_title')
          tve["fullname"] = title_h1.get_text(' ', strip=True)
          actors = soup.find(id='people-actor')
          if actors:
            actor_map = defaultdict(list)
            actors_as = list(actors.find('a'))
            actors_a = actors_as[0]
            for actor_name, role_name in batched(actors_a.stripped_strings, 2):
              print("actor", actor_name, "role", role_name)
              actor_map[actor_name].append(role_name)
            pprint(actor_map, width=60)
    return tve

class TheTVDBCommand(BaseCommand):
  ''' Command line interface to accessing the TVDB API.
  '''

  @contextmanager
  def run_context(self):
    with super().run_context() as options:
      with stackattrs(options, tvdb_api=TheTVDBAPI()):
        yield options

  @popopts(
      f=('force', 'Force refresh even if not stale.'),
      r=('recurse', 'Recursively refresh the related entities also.'),
      t=('printt', 'Use ent.printt() instead of ent.print().'),
  )
  def cmd_refresh(self, argv):
    ''' Usage: {cmd} entities...
          Refresh the specified entities and then print them.
    '''
    force = self.options.force
    recurse = self.options.recurse
    if not argv:
      raise GetoptError("missing entities")
    for arg in argv:
      ent = TVDBEntity.promote(arg)
      print(type(ent), ent.name)
      ent.refresh(force=force, recurse=recurse)
      if self.options.printt:
        ent.printt()
      else:
        ent.print()

  def cmd_search(self, argv):
    ''' Usage: {cmd} [type:]query [param=value...]
          Search the API for query with optional constraints.
          Documentation for the search endpoint:
          https://thetvdb.github.io/v4-api/#/Search
          The optional type: prefix sets the type parameter, which
          may be one of movie, series, person, or company.
    '''
    api = self.options.tvdb_api
    runstate = self.options.runstate
    if not argv:
      raise GetoptError("missing query")
    query = argv.pop(0)
    # collect the remaining arguments as parameters
    qkw = {qk: qv for qk, qv in map(lambda kv: kv.split('=', 1), argv)}
    # split off the type from query if present
    if m := re.match('^([a-z]+):', query):
      qkw['type'] = m.group(1)
      query = query[m.end():]
    results = api.search(query, **qkw)
    for i, (result, ent) in enumerate(results):
      runstate.raiseif()
      ent.refresh()
      ent.print()

if __name__ == '__main__':
  sys.exit(TheTVDBCommand().run())
