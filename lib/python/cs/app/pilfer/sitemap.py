#!/usr/bin/env python3

''' Base class for site maps.
'''

from collections import ChainMap, defaultdict, namedtuple
from dataclasses import dataclass
from datetime import datetime
from fnmatch import fnmatch
from functools import cached_property
from itertools import zip_longest
import re
import time
from types import SimpleNamespace as NS
from typing import Any, Callable, Iterable, Mapping

from cs.binary import bs
from cs.cmdutils import popopts, vprint
from cs.deco import (
    decorator, default_params, fmtdoc, OBSOLETE, promote, Promotable,
    uses_verbose
)
from cs.lex import (
    cutprefix, cutsuffix, FormatAsError, get_nonwhite, printt, r, skipwhite
)
from cs.logutils import warning
from cs.pfx import Pfx, pfx_call, pfx_method
from cs.py.func import funccite
from cs.queues import ListQueue
from cs.resources import MultiOpenMixin, RunState, uses_runstate
from cs.rfc2616 import content_encodings, content_type
from cs.seq import ClonedIterator
from cs.tagset import BaseTagSets, HasTags, TagSet, TagSetTyping, UsesTagSets
from cs.threads import HasThreadState, ThreadState
from cs.urlutils import URL

from bs4 import BeautifulSoup
from bs4.element import Tag as BS4Tag
from mitmproxy.flow import Flow
import requests
from typeguard import typechecked

# The default HTML parser to use with BeautifulSoup.
BS4_PARSER_DEFAULT = 'lxml'  # vs eg 'html5lib'

def default_Pilfer():
  ''' Obtain the ambient `Pilfer` instance via a late import.
  '''
  from .pilfer import Pilfer
  P = Pilfer.default()
  if P is None:
    P = Pilfer()
  return P

@decorator
def uses_pilfer(func):
  ''' Set the optional `P:Pilfer` parameter via a late import.
  '''

  def func_with_Pilfer(*a, P: "Pilfer" = None, **kw):
    if P is None:
      P = default_Pilfer()
    with P:
      return func(*a, P=P, **kw)

  return func_with_Pilfer

def parse_img_srcset(srcset, offset=0) -> Mapping[str, list[str]]:
  ''' Parse an `IMG` tag `srcset` attribute into a mapping of URL to conditions.
  '''
  mapping = defaultdict(list)
  offset = skipwhite(srcset, offset)
  while offset < len(srcset):
    url, offset = get_nonwhite(srcset, offset)
    offset = skipwhite(srcset, offset)
    try:
      condition, srcset = srcset[offset:].split(',', 1)
      offset = skipwhite(srcset)
    except ValueError:
      condition = srcset[offset:].rstrip()
      offset = len(srcset)

    mapping[url].append(condition)
  return mapping

@dataclass
class URLMatcher(Promotable):
  ''' A class for matching a `URL` against a `(hostname_fnmatch,url_regexp)` pair.
  '''

  hostname_fnmatch: str | None
  url_regexp: str

  @classmethod
  def from_str(cls, url_regexp):
    return cls(hostname_fnmatch=None, url_regexp=url_regexp)

  @classmethod
  def from_tuple(cls, spec):
    hostname_fnmatch, url_regexp = spec
    return cls(hostname_fnmatch=hostname_fnmatch, url_regexp=url_regexp)

  @cached_property
  def url_re(self):
    with Pfx('url_re( %s )', self.url_regexp):
      return re.compile(self.url_regexp)

  @promote
  def match(
      self,
      url: URL,
      extra: Mapping | None = None,
  ) -> dict | None:
    ''' Compare `url` against this matcher.
        Return `None` on no match.
        Return the regexp `groupdict()` on a match.
    '''
    if self.hostname_fnmatch is not None and not fnmatch(
        url.hostname, self.hostname_fnmatch):
      return None
    m = self.url_re.match(url.path)
    if m is None:
      return None
    return m.groupdict()

  @classmethod
  def promote(cls, obj):
    ''' Promote `obj` to `URLMatcher`:
        - `(hostname_fnmatch,url_regexp)` 2-tuples
        - `url_regexp` strings
    '''
    if isinstance(obj, cls):
      return obj
    try:
      hostname_fnmatch, url_regexp = obj
    except (TypeError, ValueError):
      return super().promote(obj)
    return cls(hostname_fnmatch=hostname_fnmatch, url_regexp=url_regexp)

class FlowState(NS, MultiOpenMixin, HasThreadState, Promotable):
  ''' An object with some resemblance to a `mitmproxy` `Flow` object
      with various utility properties and methods.
      It may be initialised from lesser objects such as just a URL.

      This is intented as a common basis for working in a `mitmproxy`
      flow or from outside via the `requests` package.

      Note that its `.request` and `.response` objects might be from `mitmproxy`
      or from `requests`, so they are mostly useful for their headers.

      However, the various `FlowState` attributes and properties
      are based around the `mitmproxy` `Response` attributes:
      https://docs.mitmproxy.org/stable/api/mitmproxy/http.html#Response
      In particular the `.content` and so forth are different from
      those of `requests.Response.content`.
  '''

  # class attribute holding the per-thread state stack
  perthread_state = ThreadState()

  @fmtdoc
  @promote
  def __init__(self, url: URL, **ns_kw):
    ''' Initialise `self` from the keyword parameters.

        Accepted parameters:
        - `bs4parser`: the desired BeautifulSoup parser,
          default from `{BS4_PARSER_DEFAULT==}`.
        - `flow`: a `mitmproxy` `Flow` instance
        - `request`: a `Request` instance
        - `response`: a `Response` instance
        - `url`; a URL

        The end result is that we have `.flow`, `.request`,
        `.response` and `.url` attributes, which may be `None`
        if omitted.
        Some of these have computed defaults if omitted:
        - `.request` and `.response` are obtained from `.flow`
        - `.url` is obtained from `.request.url`
    '''
    super().__init__(url=url, **ns_kw)
    extra_attrs = self.__dict__.keys() - (
        'bs4parser',
        'flow',
        'request',
        'response',
        'url',
    )
    if extra_attrs:
      raise ValueError(f'unexpected attributes supplied: {extra_attrs}')
    if rsp := self.__dict__.get('response'):
      self.iterable_content = ClonedIterator(rsp.iter_content(chunk_size=None))

  def __str__(self):
    attr_listing = ",".join(
        f'{attr}={value}' for attr, value in self.__dict__.items()
    )
    return f'{self.__class__.__name__}({attr_listing})'

  __repr__ = __str__

  def __enter_exit__(self):
    ''' Run both the inherited context managers.
    '''
    with self.session:
      for _ in zip_longest(
          MultiOpenMixin.__enter_exit__(self),
          HasThreadState.__enter_exit__(self) if self.default else (),
      ):
        yield

  def __del__(self):
    ''' Close `self.response` on delete.
    '''
    rsp = self.__dict__.get('response')
    if rsp is not None:
      rsp.close()

  # NB: no __getattr__, it preemptys @cached_property

  @classmethod
  def from_str(cls, url_s: str):
    ''' Promote a `str` URL to a `FlowState`.
    '''
    return cls(url=URL(url_s))

  @classmethod
  def from_URL(cls, url: URL):
    ''' Promote a `URL` URL to a `FlowState`.
    '''
    return cls(url=url)

  @classmethod
  def from_Flow(cls, flow: Flow):
    ''' Promote a `Flow` to a `FlowState`.
    '''
    return cls(
        url=flow.request.url,
        flow=flow,
        request=flow.request,
        response=flow.response,
    )

  @classmethod
  @uses_pilfer
  @uses_runstate
  def iterable_flowstates(
      cls,
      flowstates: Iterable,
      P: "Pilfer",
      runstate: RunState,
      **later_map_kw,
  ):
    ''' A generator yielding `FlowState` instances with a ready
        `.iterable_content` attribute, promoted from `flowstates`.

        This prepares the `FlowState`s concurrently, performing a
        `GET` if necessary, yielding them in the supplied order.

        Parameters:
        * `flowstates`: an iterable, notionally of `FlowState`s but
          actually any object which can be promoted to a `FlowState`
          such as a URL
        * `P`: optional `Pilfer` if not the ambient one
        * `runstate`: optional `RunState` if not the ambient one
        Other keyword arguments are passed to `Later.map` to control
        how things are fulfilled.

        Example:

            # call self.grok_the_page on several page URLs
            pages = [ f'url-of-page-n' for n in range(1,count) ]
            for flowstate in FlowState.iterable_flowstates(*pages):
                # we know this page can be parsed immediately
                info = self.grok_the_page(flowstate)
                ... apply info ...
    '''

    @promote
    def get_iterable_fs(fs: "FlowState") -> "FlowState":
      ''' Promote `fs` to a `FlowState` and ready its `.iterable_content` attribute.
          This will do a `GET` if necessary.
      '''
      with P:
        fs.iterable_content  # ensure the content has been made available
      return fs

    with P:
      for flowstate in P.later.map(get_iterable_fs, flowstates,
                                   **later_map_kw):
        yield flowstate
        runstate.raiseif()

  def clear(self, *attrs):
    ''' Delete the named attrubtes `attrs`.
        We do this to clear derived attributes when we set an
        antecedant attribute.
    '''
    assert len(attrs) > 0
    for attr in attrs:
      try:
        delattr(self, attr)
      except AttributeError:
        pass

  @cached_property
  @fmtdoc
  def bs4parser(self):
    ''' The beautifulSoup parser name.
        The default comes from `{BS4_PARSER_DEFAULT==}`.
    '''
    # TODO: envvar? Pilfer config setting?
    return BS4_PARSER_DEFAULT

  @cached_property
  def url(self) -> URL:
    ''' The URL, obtained from `self.response.url` if missing.
    '''
    return URL(self.response.url)

  @cached_property
  def method(self) -> str:
    ''' The uppercase form of the request method.
    '''
    return self.request.method.upper()

  @cached_property
  def content_type(self) -> str:
    ''' The base `Content-Type`, eg `'text/html'`.
    '''
    ct = content_type(self.response.headers)
    if ct is None:
      return ''
    return ct.content_type

  @cached_property
  def content_type_params(self) -> Mapping[str, str]:
    ''' The parameters from the `Content-Type` as a mapping of the
        parameter's lowercase form to its value.
    '''
    params = {}
    for param_s in self.response.headers.get('content-type',
                                             '').split(';')[1:]:
      k, v = param_s.split('=', 1)
      params[k.strip().lower()] = v
    return params

  @property
  def content_charset(self) -> str | None:
    ''' The supplied `Content-Type` character set if specified, or `None`.
    '''
    return self.content_type_params.get('charset')

  @property
  def content_encoding(self) -> str:
    ''' The `Content-Encoding` response header, or `''`.
    '''
    return self.response.headers.get('content-encoding', '')

  @content_encoding.setter
  def content_encoding(self, new_encoding: str):
    ''' Set the `Content-Encoding` response header.
    '''
    self.response.headers['Content-Encoding'] = new_encoding or 'identity'

  @content_encoding.deleter
  def content_encoding(self):
    ''' Delete the `Content-Encoding` response header.
    '''
    self.response.headers.pop('content-encoding', None)

  @property
  def content_encodings(self) -> list[str]:
    ''' A list of the transforming encodings named in the `Content-Encoding` response header.
          The encoding `'identity'` is discarded.
      '''
    return [
        enc for enc in content_encodings(self.response.headers)
        if enc != 'identity'
    ]

  def _new_content(self):
    ''' Clear various properties derived from the content.
    '''
    self.clear(
        'iterable_content',
        'json',
        'links',
        'meta',
        'opengraph_tags',
        'soup',
        'text',
    )

  @uses_pilfer
  @promote(params=('url',))
  def GET(self, url: URL = None, *, P: "Pilfer", **rq_kw) -> requests.Response:
    ''' Do a `Pilfer.GET` of `self.url` and return the `requests.Response`.
        This also updates `self.request` and `self.response`, sets
        `self.iterable_content`, and clears `self.content` and
        `self.soup` (meaning they will be rederived on next access).
    '''
    if url is None:
      url = self.url
    else:
      self.url = url
    # TODO: is the response closed in a timely fashion?
    #       Until consumed it occupies a slot in the urllib3 connection pool.
    #       We try to close in in self.__del__.
    rsp = self.response = P.GET(url, stream=True, **rq_kw)
    # forget any cached derived values
    self.request = rsp.request
    self.url = url
    self._new_content()
    # this should be the decoded content, eg ungzipped
    self.iterable_content = ClonedIterator(rsp.iter_content(chunk_size=None))
    return rsp

  @cached_property
  def response(self):
    ''' Cached response object, obtained via `self.GET()` if needed.
    '''
    return self.GET()

  @cached_property
  def iterable_content(self) -> Iterable[bytes]:
    ''' An iterable of the _decoded_ content.

        After a `self.GET()` this will be a clone of the `requests.Response` stream,
        otherwise it will be `[self.content]`.
    '''
    # there is no .iterable_content yet, expect it from the flow.response.content
    # TODO: need to decode?
    try:
      flow = self.flow
    except AttributeError:
      self.GET()
      return self.iterable_content
    # TODO: can we accomodate a flow whose content is streaming in?
    return [flow.response.content]

  @cached_property
  @typechecked
  def content(self) -> bytes:
    ''' The response content, concatenated as a single `bytes` instance
        from `self.iterable_content`.
    '''
    content = b''.join(self.iterable_content)
    self.set_content(content)  # to clear the derived attributes
    return content

  @typechecked
  def set_content(self, content: bytes):
    ''' Set `self.content` and forget the `text` and `soup` attributes.
    '''
    self._new_content()
    self.content = content

  @cached_property
  def text(self) -> str:
    ''' The text content of the URL.
    '''
    # assume UTF-8 if not specified
    charset = self.content_charset or 'utf-8'
    text = self.content.decode(charset)
    self.url.text = text
    return text

  @cached_property
  def json(self):
    ''' A python object decoded from the response JSON payload, an
        alias for `self.response.json`.
    '''
    return self.response.json()

  @cached_property
  def soup(self):
    ''' A `BeautifulSoup` of `self.content` for `text/html`, otherwise `None`.
    '''
    if self.content_type == 'text/html':
      soup = BeautifulSoup(self.text, self.bs4parser)
      self.url.soup = soup
      return soup
    return None

  @cached_property
  def meta(self):
    ''' The meta information from this page's body head meta tags.
        Return an object with the following attriubutes:
        - `.http_equiv`: the `meta` tags with `http-equiv` attributes
        - `.properties`: the `meta` tags with `property` attributes
        - `.tags`: the `meta` tags with `name` attributes
    '''
    meta_tags = TagSet()
    meta_properties = TagSet()
    meta_http_equiv = TagSet()
    soup = self.soup
    if soup is not None:
      if soup.head is None:
        warning("no HEAD tag")
        print(soup)
      else:
        for tag in soup.head.descendants:
          if isinstance(tag, str):
            ##if tag.strip(): warning("SKIP HEAD tag %r", tag[:40])
            continue
          if tag.name != 'meta':
            continue
          tag_content = tag.get('content')
          if not tag_content:
            continue
          if tag_name := tag.get('name'):
            meta_tags[tag_name] = tag_content
          if prop_name := tag.get('property'):
            try:
              tag_content = datetime.fromisoformat(tag_content)
            except ValueError:
              try:
                tag_content = int(tag_content)
              except ValueError:
                pass
            current = meta_properties.get(prop_name)
            if current is None:
              meta_properties[prop_name] = tag_content
            elif isinstance(current, list):
              meta_properties[prop_name].append(tag_content)
            else:
              meta_properties[prop_name] = [current, tag_content]
          if http_equiv := tag.get('http-equiv'):
            meta_http_equiv[http_equiv] = tag['content']
    return NS(
        tags=meta_tags, properties=meta_properties, http_equiv=meta_http_equiv
    )

  @cached_property
  def links(self) -> Mapping[str, list[str]]:
    ''' A `defaultdict(list)` mapping `link` `rel=` values a list of `link` tags.
    '''
    links_by_rel = defaultdict(list)
    soup = self.soup
    if soup is not None:
      for link in soup.find_all('link'):
        for rel in link.attrs.get('rel', ('',)):
          links_by_rel[rel].append(link)
    return links_by_rel

  @cached_property
  def opengraph_tags(self) -> dict:
    ''' The open graph properties, see https://ogp.me/
        Each key has the form `'opengraph.`*prop*`'`
        for the OpenGraph property named `og:`*prop*.
    '''
    # I have seen these misplaced into the META tags,
    # so get those then overwrite from the properties.
    return {
        f'opengraph.{cutprefix(k,"og:")}': v
        for k, v in (*self.meta.tags.items(), *self.meta.properties.items())
        if k.startswith("og:")
    }

uses_flowstate = default_params(flowstate=FlowState.default)

class SiteEntity(HasTags):
  ''' A base class for entities associated with a `SiteMap`.

      This provides the following additional facilities:

      If the entity class has a *FOO*`_FORMAT` attribute
      then `self.`*foo*` will return that formatted against the entity
      A common example is to provide a `SITEPAGE_FORMAT` class
      attribute to enable a `.sitepage` attribute which returns the
      primary web page for the entity.
  '''

  def __getattr__(self, attr):
    if attr.islower():
      # .fmtname returns self.format_as(cls.FMTNAME_FORMAT)
      fmtattr_name = f'{attr.upper()}_FORMAT'
      try:
        format_s = getattr(self.__class__, fmtattr_name)
      except AttributeError:
        pass
      else:
        try:
          return self.format_as(format_s)
        except FormatAsError as e:
          warning("%s.format_as %r: %s", self, format_s, e)
          raise AttributeError(
              f'format {self.__class__.__name__}.{fmtattr_name} {format_s!r}: {e}'
          ) from e
    return super().__getattr__(attr)

  def format_kwargs(self):
    ''' The format keyword mapping for a `SiteEntity`.

        This includes:
        - the `HasTags.format_kwargs()`
        - the values for any names in `type(self.tags)` with an
          upper case leading letter such as `URL_DOMAIN`
    '''
    kwargs = super().format_kwargs()
    kwargs.update(
        {
            k: v
            for k, v in self.tags.__class__.__dict__.items()
            if k[:1].isupper()
        }
    )
    return kwargs

  @cached_property
  def url_root(self):
    ''' Proxy to `self.tags_db.url_root`.
    '''
    return self.tags_db.url_root

  def urlto(self, path):
    ''' Proxy to `self.tags_db.urlto()`.
    '''
    return self.tags_db.urlto(path)

  @property
  def sitepage(self) -> str:
    ''' The `sitepage` is derived from `self.SITEPAGE_FORMAT`.
    '''
    try:
      url = self["sitepage"]
    except KeyError:
      url = self.__getattr__('sitepage')
    if url.startswith('/'):
      url = f'{self.url_root}{url[1:]}'
    return url

  def equivalents(self):
    ''' Return a list of equivalent `SiteEntity` instances from other type zones,
        derived from the keys in `self['equivalents']`.
        Unhandled key elicit a warning and are discarded.
    '''
    with Pfx("%s.equivalents", self):
      equiv_keys = self.get('equivalents', [])
      equivs = []
      for eqk in equiv_keys:
        with Pfx(eqk):
          try:
            equiv = SiteMap.by_db_key(eqk)
          except KeyError as e:
            warning("no SiteEntity for key: %s", e)
            continue
          equivs.append(equiv)
    return equivs

  @classmethod
  def by_db_key(cls, db_key: str):
    ''' Return the `SiteEntity` for the database wide `key`.
    '''
    return SiteMap.by_db_key(db_key)

  @promote
  def grok_sitepage(self, flowstate: FlowState):
    ''' Parse information from `flowstate` and apply to `self`.

        We expect subclasses to provide site specific implementations.

        Note that the `SiteMap.updated_entities()` method does not
        mark the `last_update_unixtime` tag if this method raises
        `NotImplementedError`, which this default implementation does.
    '''
    warning("%s.grok_sitepage: not grokking anything from %s", self, flowstate)
    raise NotImplementedError(
        f'no grok_sitepage implementation for {type(self)}'
    )

  def update_entity(self, *, force=False):
    ''' Update this entity from its sitepage if stale.
    '''
    for _ in self.tags_db.updated_entities((self,), force=force):
      pass

class SiteMapPatternMatch(namedtuple(
    "SiteMapPatternMatch", "sitemap pattern_test pattern_arg match mapping")):
  ''' A pattern match result:
      * `sitemap`: the source `SiteMap` instance
      * `pattern_test`: the pattern test object
      * `pattern_arg`: the argument to the pattern
      * `match`: the match result object from the pattern test
        such as an `re.Match` instance
      * `mapping`: a mapping of named values gleaned during the match
  '''

  def format_arg(self, extra: Mapping | None = None) -> str:
    ''' Treat `self.pattern_arg` as a format string and format it
        using `self.mapping` and `extra`.
    '''
    return self.pattern_arg.format_map(ChainMap(self.mapping, extra or {}))

@dataclass
class SiteMap(UsesTagSets, Promotable):
  ''' A base dataclass for site maps.

      A `SiteMap` data class embodies domain specific knowledge about a
      particular website or collection of websites.

      It subclasses `cs.tagset.UsesTagSets` and gets its `.tagsets`
      from `self.pilfer` if unspecified. Use as a `UsesTagSet`
      domain requires setting:
      - `TYPE_ZONE` to the domain prefix
      - `HasTagsClass` to the base `HasTags` entity class
      See `cs.tagset.UsesTagSets` for details.

      A `Pilfer` instance obtains its site maps from the `[sitemaps]`
      clause in the configuration file, see the `Pilfer.sitemaps`
      property for specifics.

      A pilferrc configuration example:

          [sitemaps]
          docs.python.org = docs:cs.app.pilfer.sitemap:DocSite
          docs.mitmproxy.org = docs
          *.readthedocs.io = docs

      This says that websites whose domain matches `docs.python.org`,
      `docs.mitmproxy.org` or the filename glob `*.readthedocs.io`
      are all associated with the `SiteMap` referred to as `docs`
      whose definition comes from the `DocSite` class from the module
      `cs.app.pilfer.sitemap`. The `DocSite` class will be a subclass
      of this `SiteMap` base class.

      `SiteMap`s have a few class attributes:
      * `TYPE_ZONE`: the `TagSetTyping.type_zone` value for entities
        associated with this class
      * `URL_KEY_PATTERNS`: this is a list of `(match,keyformat)`
        2-tuples specifying cache keys for caching URL contents; the
        `pilfer mitm ... cache` filter consults these to decide what
        URLs to cache.
        See the `SiteMap.url_key` method.
      * `PREFETCH_PATTERNS`: this is a list of `(match,keyformat)`
        2-tuples specifying prefetch URLs for a URL's contents; the
        `pilfer mitm ... prefetch` filter consults these to decide what
        URLs to queue for prefetching.
        See the `SiteMap.content_prefetch` method.
  '''

  name: str = None
  pilfer: object = None
  tagsets: BaseTagSets = None

  # a registry of SiteMap subclasses nstancesby their TYPE_ZONE
  sitemap_by_type_zone = {}

  URL_KEY_PATTERNS = ()

  @uses_pilfer
  def __post_init__(self, *, P: "Pilfer"):
    ''' Initialise `.pilfer` if omitted`, and then `.tagsets` from `self.pilfer`.
    '''
    if self.name is None:
      self.name = self.TYPE_ZONE
    if self.pilfer is None:
      self.pilfer = P
    # Register this `SiteMap` by its `TYPE_ZONE`.
    sitemap_by_type_zone = self.__class__.sitemap_by_type_zone
    try:
      self.TYPE_ZONE
    except AttributeError:
      warning(f'no .TYPE_ZONE for SiteMap instance {self}')
    else:
      try:
        other_map = sitemap_by_type_zone[self.TYPE_ZONE]
      except KeyError:
        pass
      else:
        warning(f'replacing {self.TYPE_ZONE=} -> {other_map} with {self}')
      sitemap_by_type_zone[self.TYPE_ZONE] = self
    super().__init__(tagsets=self.pilfer.sqltags)

  @classmethod
  def by_db_key(cls, db_key: str):
    ''' Return the `SiteEntity` for the database wide `key`.
    '''
    try:
      zone, subname, key = TagSetTyping.type_parts_of(db_key)
    except ValueError as e:
      raise KeyError(f'{db_key=}: cannot parse into type parts: {e}') from e
    try:
      sitemap = cls.sitemap_by_type_zone[zone]
    except KeyError as e:
      raise KeyError(f'{db_key=}: no SiteMap registered for {zone=}') from e
    return sitemap[subname, key]

  @classmethod
  @uses_pilfer
  def from_str(cls, sitemap_name: str, *, P: "Pilfer") -> "SiteMap":
    ''' Return the `SiteMap` instance known as `sitemap_name` in the ambient `Pilfer` instance.
    '''
    for name, sitemap in P.sitemaps:
      if name == sitemap_name:
        return sitemap
    raise ValueError(
        f'{cls.__name__}.from_str({sitemap_name!r}): unknown sitemap name'
    )

  def urlto(self, suburl, domain=None, *, scheme='https'):
    ''' Return the full URL for `suburl`.
    '''
    return f'{scheme}://{domain or self.URL_DOMAIN}/{suburl.lstrip("/")}'

  @cached_property
  def url_root(self) -> str:
    ''' The root URL for this site, derived from `self.URL_DOMAIN`.
        This includes the trailing slash, eg `https://example.com/`
    '''
    return self.urlto('')

  # TODO: some notion of staleness
  @classmethod
  @uses_pilfer
  def updated_entities(
      cls, entities: Iterable["SiteEntity"], *, P: "Pilfer", force=False
  ):
    ''' A generator yielding updated `SiteEntity` instances
        from an iterable of `SiteEntity` instances.

        Parameters:
        - `entities`: an iterable of `SiteEntity` instances to update
        - `force`: optional flag to force an update even if the
          entity does not appear stale
    '''

    # Prepare queues for processing:
    #
    # entities -> process_entities --ent_spQ-> process_entity_sitepages
    #                    |                            |
    #                    v                            |
    #                 ent_fsQ <-----------------------+
    #                    |
    #                    v
    #         call entity.grok_sitepage(flowstate)
    #                    |
    #                    v
    #             updated entities

    ent_spQ = IterableQueue()
    ent_fsQ = IterableQueue()

    def process_entities():
      ''' Process the iterable of entities.
          Entities with no sitepage or which are not stale
          are put diectly only `ent_fsQ` with `None` for the `flowstate`.
          Other entities are put only `flowstateQ` to be fetched.
      '''
      try:
        for entity in entities:
          print(f'PROCESS_ENTITIES: entity {entity}')
          sitepage = getattr(entity, "sitepage", None)
          if sitepage is None:
            ent_fsQ.put((entity, None))
          elif not force and "sitepage.last_update_unixtime" in entity:
            ent_fsQ.put((entity, None))
          else:
            ent_spQ.put((entity, sitepage))
      finally:
        # close the (entity,sitepage) processor
        ent_spQ.close()
        # send one of the 2 end markers for the (entity,flowstate) queue
        ent_fsQ.put((None, None))

    def process_entity_sitepages():
      ''' Process the queue of `(entity,sitepage)` pairs from `ent_spQ`
          and put `(entity,flowstate)` 2-tuples onto `ent_fsQ`
          as the flowstates become ready (HTTP response received,
          content available in streaming mode).
      '''
      try:
        ent_sps = ClonedIterator(ent_spQ)
        for (entity, sitepage), flowstate in zip(
            ent_sps,
            FlowState.iterable_flowstates(
                (ent_sp[1] for ent_sp in ent_sps),
                unordered=False,
            ),
        ):
          ent_fsQ.put((entity, flowstate))
      finally:
        # send one of the 2 end markers
        ent_fsQ.put((None, None))

    # dispatch the workers
    process_entitiesT = Thread(target=process_entities, daemon=True)
    process_entity_sitepagesT = Thread(
        target=process_entity_sitepages, daemon=True
    )
    process_entitiesT.start()
    process_entity_sitepagesT.start()

    # Process the (entity,flowstate) queue.
    # Each 2-tuple received will be:
    # - (None,None): an end marker from one of the workers - we expect 2
    # - (entity,None): an entity whose sitepage is unknown or unobtainable
    # - (entity,flowstate): an entity and a ready flowstate whose content can be processed
    n_end_markers = 0
    for qitem in ent_fsQ:
      print("ent_fsQ ->", r(qitem))
      entity, flowstate = qitem
      if entity is None:
        assert flowstate is None
        n_end_markers += 1
        if n_end_markers == 2:
          break
        continue
      if flowstate is not None:
        # process the flowstate
        try:
          pfx_call(entity.grok_sitepage, flowstate)
        except Exception as e:
          warning("exception calling %s.grok_sitepage: %s", entity, e)
      yield entity

  @staticmethod
  @decorator
  @typechecked
  def on(method, *patterns, **tags_kw):
    ''' A decorator for handler methods which specifies conditions
        which must match for this handler to be called.
        This decorator may be applied multiple times
        if the handler method should match various flows.

        Its positional parameters indicate the conditions under
        which this method will be fired; all must be true.
        Each use of the decorator appends a `(conditions,tags_kw)`
        2-tuple to the method's `.on_conditions` attribute,
        where `conditions` is list storing the conjunction of conditions
        and `tags_kw` is any *tag*`=`*format*` supplied in
        the decorator keyword arguments.

        The positional parameters have the following meaning:
        - a string consisting entirely of uppercase letters;
          this matches an HTTP method name such as `GET` or `POST`
        - a string containing no slash character (`'/'`):
          a filename glob to match against the URL hostname
        - a string containing a slash:
          a regular expression to apply against the URL path;
          a leading slash anchors the regexp against the start of the path
          otherwise it may match anywhere in the path
        - a callable: a function accepting a `FlowState` and the
          current match `TagSet`;
          it may return `None` or `False` for no match,
          or `True` or a `Mapping[str,str]` for a match
        Note that to avoid confusing the decorator the first condition
        cannot be a callable. However, it's usually a domain glob anyway.

        The keyword parameters specify `Tag`s to set on the match `TagSet`
        if the conditions have matched. The parameter value may be:
        - a callable: a function accepting a `FlowState` and the
          current match `TagSet` which computes the tag value
        - otherwise it should be a string which will be formatted
          using `.format_map(match)`

        For example this decoration matches the URL hostname
        `docs.python.org` and the URL path
        `/3/library/`*module_name*`.html`.
        On a match the resulting match tags will contain:
        * `'module_name'`: from the regular expression
        * `'cache_key'`: from the `cache_key=` argument

            @on(
                'docs.python.org',
                r'/3/library/(?P<module_name>[^/]+).html$',
                cache_key='module/{module_name}',
            )
            def cache_key_pydoc(self, flowstate, match: TagSet) -> str:
                # here one might fill in match['cache_key']
                # aka match.cache_key
                ........

        You may notice that the cache key doesn't mention the
        hostname; the caching system qualifies cache keys with their
        `SiteMap` name and so the key here need only be unique
        within domains served with this `SiteMap`.
    '''
    conditions = []
    for pattern in patterns:
      with Pfx(f'pattern={r(pattern)}'):
        if isinstance(pattern, str):
          if pattern.isupper():
            # a method name
            def maketest(method_name):
              test_name = f'flowstate.method == {method_name=}'

              def test(flowstate, match):
                vprint(f'@on: {method_name=} vs {method_name=}')
                return flowstate.method == method_name

              test.__name__ = test_name
              test.method_name = method_name  # for use in breakpoints
              return test

          elif '/' in pattern:
            # a path match
            def maketest(regexp):
              regexp = pfx_call(re.compile, pattern)
              if pattern.startswith('/'):
                # match at the start of the path
                test_name = f'flowstate.url.path ~ ^{regexp}'

                def test(flowstate, match):
                  vprint(f'@on: {flowstate.url.path=} ~ ^{regexp}')
                  m = pfx_call(regexp.match, flowstate.url.path)
                  if m is None:
                    return None
                  return m.groupdict()
              else:
                test_name = f'flowstate.url.path ~ {regexp}'

                def test(flowstate, match):
                  vprint(f'@on: {flowstate.url.path=} ~ {regexp}')
                  m = pfx_call(regexp.search, flowstate.url.path)
                  if m is None:
                    return None
                  return m.groupdict()

              test.__name__ = test_name
              return test
          else:
            # filename glob on the URL host
            def maketest(glob):

              def test(flowstate, match):
                vprint(f'@on: {flowstate.url.hostname=} ~ {glob=}')
                return pfx_call(fnmatch, flowstate.url.hostname, glob)

              test.__name__ = f'flowstate.url.hostname ~ {glob=}'
              test.glob = glob
              return test

        elif callable(pattern):
          # it should be a callable accepting a FlowState and the match TagSet
          # TODO: can it be inspected?
          _: Callable[FlowState, TagSet] = pattern

          def maketest(condition_func):
            citation = funccite(condition_func)

            def test(flowstate, match):
              vprint(f'@on: {citation}(flowstate:{flowstate.url=})')
              return condition_func(flowstate)

            test.__name__ = f'{citation}(flowstate)'
            return test

        else:
          raise RuntimeError
        condition = maketest(pattern)
        assert condition is not None
        assert callable(condition)
        conditions.append(condition)
        del condition
    try:
      cond_attr = method.on_conditions
    except AttributeError:
      cond_attr = method.on_conditions = []
    cond_attr.append((conditions, tags_kw))
    return method

  @classmethod
  @pfx_method
  @uses_verbose
  @promote
  def on_matches(
      cls,
      flowstate: FlowState,
      methodglob: str | None = None,
      *,
      verbose: bool,
      **match_kw,
  ) -> Iterable[tuple[Callable, TagSet]]:
    ''' A generator yielding `(method,match)` 2-tuples for methods matched
        by `flowstate` and `match_kw`, being the matching method
        and a `TagSet` of values obtained during the match test.

        Parameters:
        * `flowstate`: the `FlowState` on which to match
        * `methodglob`: an optional filename glob constraining the chosen method names
        * `match_kw`: the `on_match` keyword arguments which must match

        The matching methods are identified by consulting the
        conditions in the method's `.on_conditions` attribute, a
        list of conjunctions normally defined by applying the `@on`
        decorator to the method.
        A `(method,match)` 2-tuple is yielded for each matching conjunction.

        Note that this means the same methods may be yielded multiple
        times if different conjunctions match (eg multiple matching
        `@on` decorators); this is because each condition may provide
        different `match` match results.
    '''
    for method_name in dir(cls):
      if methodglob is not None and not fnmatch(method_name, methodglob):
        continue
      try:
        method = getattr(cls, method_name)
      except AttributeError:
        continue
      try:
        conditions = method.on_conditions
      except AttributeError:
        # no conditions, skip
        continue
      # Prepare the final match result.
      # Start with the URL attributes.
      url = flowstate.url
      match = TagSet(
          {
              attr: getattr(url, attr)
              for attr in (
                  'basename',
                  'cleanpath',
                  'cleanrpath',
                  'dirname',
                  'domain',
                  'hostname',
                  'netloc',
                  'path',
                  'port',
                  'rpath',
                  'scheme',
              )
          }
      )
      # set various other things
      match.update(
          # url cleaned relative path
          _=url.cleanrpath,
          # hostname/cleanpath
          __=f'{url.hostname}/{url.cleanrpath}',
          # rq method eg GET
          method=flowstate.method,
      )
      # eg "tvdb" for thetvdb.com, for use with sqltags
      try:
        type_zone = cls.TYPE_ZONE
      except AttributeError:
        pass
      else:
        match["type_zone"] = type_zone
      for conjunction, tags_kw in conditions:
        with Pfx("match %r", conjunction):
          for condition in conjunction:
            cond_spec = getattr(condition, "__name__", str(condition))
            with Pfx("on_matches: test %r vs %s", method_name, cond_spec):
              print('ON_MATCHES', f'{method_name} vs {cond_spec}')
              if verbose:
                printt(*[(f'  {k}', v) for k, v in sorted(match.items())],)
              try:
                test_result = pfx_call(condition, flowstate, match)
              except Exception as e:
                warning("exception in condition: %s", e)
                # TODO: just fail? print a traceback if we do this
                raise
              print(f'  -> {test_result=}')
              # test ran, examine result
              if test_result is None or test_result is False:
                # failure
                break
              # success
              if test_result is True:
                # success, no side effects
                pass
              else:
                print(
                    "  true but not True, should be a mapping", r(test_result)
                )
                # should be a mapping, update the match TagSet
                # typical example: the result is a re.Match.groupdict()
                for k, v in test_result.items():
                  print(f'    set match[{k=}] = {v=}')
                  match[k] = v
          else:
            # no test failed, this is a match
            # update match with any format strings from @on
            for name, fmt in tags_kw.items():
              if callable(fmt):
                match[name] = fmt(flowstate, match)
              else:
                match[name] = fmt.format_map(match)
            yield method, match

  @pfx_method
  @promote
  def run_matches(
      self,
      flowstate: FlowState,
      flowattr: str | None = None,
      methodglob: str | None = None,
      **match_kw,
  ) -> Iterable[tuple[Callable, TagSet, Any]]:
    ''' Run all the methods in this `SiteMap` whose `.on_conditions`
        match `flowstate` and ``match_kw`, as matched by `SiteMap.on_matches`.
        Yield `(method,match,result)` 3-tuples from each method called.

        Parameters:
        * `flowstate`: the `FlowState` on which to match
        * `flowattr`: an optional attribute name of the `flowstate`
        * `methodglob`: an optional filename glob constraining the chosen method names
        * `match_kw`: the `on_match` keyword arguments which must match

        Each `method` is called as `method(self,flowstate,match)`
        where `method` and `match` were yielded from
        `on_matches(flowstate,**match_kw)`.

        If `flowattr` is not `None`, `getattr(flowstate,flowattr)`
        is passed as an additional positional parameter and if the
        method result is not `None` then the result is set as an
        updated value on `flowstate`.
    '''
    for method, match in self.on_matches(flowstate, methodglob, **match_kw):
      with Pfx("call %s", method.__qualname__):
        try:
          if flowattr is None:
            result = method(self, flowstate, match)
          else:
            attrvalue = pfx_call(getattr, flowstate, flowattr)
            result = method(self, flowstate, match, attrvalue)
        except Exception as e:
          warning("%s.%s: url=%s: %s", self, method.__name__, flowstate.url, e)
          raise
        else:
          if flowattr is not None and result is not None:
            pfx_call(setattr, flowstate, flowattr, result)
          yield method, match, result

  @pfx_method
  @promote
  def grok(
      self,
      flowstate: FlowState,
      flowattr: str | None = None,
      **run_match_kw,
  ) -> Iterable[tuple[Callable, TagSet, Any]]:
    ''' A generator to grok the fullness of this `flowstate`, deriving information.
        Usually this involves consulting the URL contents.
        This is a shim for `SiteMap.run_matches` calling any matching
        methods named `grok_*`.
        Yield `(method,match,result)` 3-tuples from each method called.
        Usually the `result` is a `TagSet`.
    '''
    if flowstate.response.status_code != 200:
      warning(f'{flowstate.response.status_code=} != 200, not grokking')
      return
    yield from self.run_matches(flowstate, flowattr, 'grok_*', **run_match_kw)

  @staticmethod
  @decorator
  def grok_entity_sitepage(func, *, ent_class):
    ''' A decorator for sitepage `grok_*` methods which apply grokked
        information to a `SiteEntity`.
        This obtains the `entity` from `self[ent_class,match["type_key"]]`,
        calls `entity.grok_sitepage(flowstate)`,
        calls `func(self,flowstate,match,entity)`,
        returns the `entity`.

        Example:

            @on(
                URL_DOMAIN,
                r'/something/(?P<type_key>[^/+])/....',
            )
            @grok_entity_sitepage(ent_class=FrogEntity)
            def grok_frog_sitepage(self, flowstate: FlowState, match, entity:FrogEntity):
                pass # this example does no additional work
    '''

    def _grok_sitepage_wrapper(
        self, flowstate: FlowState, match
    ) -> SiteEntity:
      entity = self[ent_class, match["type_key"]]
      entity.grok_sitepage(flowstate)
      func(self, flowstate, match, entity)
      return entity

    return _grok_sitepage_wrapper

  def matches(
      self,
      url: URL,
      patterns: Iterable,  # [Tuple[Tuple[str, str], Any]],
      extra: Mapping | None = None,
  ) -> Iterable[SiteMapPatternMatch]:
    ''' A generator to match `url` against `patterns`, an iterable
        of `(match_to,arg)` 2-tuples which yields
        a `SiteMapPatternMatch` for each pattern which matches `url`.

        Parameters:
        * `url`: a `URL` to match
        * `patterns`: the iterable of `(match_to,arg)` 2-tuples
        * `extra`: an optional mapping to be passed to the match function

        Each yielded match is a `SiteMapPatternMatch` instance
        with the following atttributes:
        * `sitemap`: `self`
        * `pattern_test`: the pattern's first component, used for the test
        * `pattern_arg`: the pattern's second component, used by the caller to produce some result
        * `match`: the match object returned from the match function
        * `mapping`: a mapping of values gleaned during the match

        This implementation expects all the patterns to be
        `(match_to,arg)` 2-tuples, where `match_to` is either
        `URLMatcher` instance or a `(domain_glob,path_re)` 2-tuple
        which can be promoted to a `URLMatcher`.
        The match function is the `URLMatcher`'s `.match` method.

        The match is a mapping returned from the match function.

        The mapping is a `dict` initialised as follows:
        1: with the following attributes of the `url`:
           `basename`, `cleanpath`, 'cleanrpath', `dirname`, `domain`,
           `hostname`, `netloc`, `path`, `port`, `scheme`.
        2: with `_=url.cleanrpath` and `__=hostname/cleanrpath`
        3: with the entries from `url.query_dict()`
        4: with the contents of the `match` mapping
        Later items overwrite earlier items where they conflict.
    '''
    for match_to, arg in patterns:
      matcher = URLMatcher.promote(match_to)
      if (match := matcher.match(url, extra=extra)) is not None:
        # start with the URL attributes
        mapping = {
            attr: getattr(url, attr)
            for attr in (
                'basename',
                'cleanpath',
                'cleanrpath',
                'dirname',
                'domain',
                'hostname',
                'netloc',
                'path',
                'port',
                'scheme',
            )
        }
        # set _ to the url.path, __ to histname/path
        mapping.update(
            _=url.cleanrpath,
            __=f'{url.hostname}/{url.cleanrpath}',
        )
        # overlay any URL query terms
        mapping.update(url.query_dict())
        # overlay any match results
        mapping.update(match)
        yield SiteMapPatternMatch(self, match_to, arg, match, mapping)

  def match(
      self,
      url: URL,
      patterns: Iterable,
      extra: Mapping | None = None,
  ) -> SiteMapPatternMatch | None:
    ''' Scan `patterns` for a match to `url`, returning the first
        match `SiteMapPatternMatch` from `self.matches()`
        or `None` if no match is found.
    '''
    for matched in self.matches(url, patterns, extra=extra):
      return matched
    return None

  @OBSOLETE('SiteMap.default_cache_key or cache_key_*')
  @promote
  def url_key(
      self,
      url: URL,
      extra: Mapping | None = None,
  ) -> str | None:
    ''' Return a string which is a persistent cache key for the
        supplied `url` within the context of this sitemap, or `None`
        for URLs which do not have a key i.e. should not be cached persistently.

        A site with semantic URLs might have keys like
        *entity_type*`/`*id*`/`*aspect* where the *aspect* was
        something like `html` or `icon` etc for different URLs
        associated with the same entity.

        This base implementation matches the patterns in `URL_KEY_PATTERNS`
        class attribute which is `()` for the base class.
    '''
    match = self.match(url, self.URL_KEY_PATTERNS, extra=extra)
    if not match:
      return None
    return match.format_arg(extra=extra)

  @uses_pilfer
  ##@typechecked # we don't import Pilfer (circular)
  def content_prefetch(
      self,
      match: SiteMapPatternMatch,
      flow,
      content_bs: bs,
      *,
      P: "Pilfer",
  ):
    ''' The generic prefetch handler.

        This parses `content_bs` and queues URLs for prefetching
        based on the value of `match.pattern_arg`.

        The `match.pattern_arg` should be a list of strings (or a single string).
        The supported strings are:
        - `"hrefs"`: all the anchor `href` values
        - `"srcs"`: all the anchor `src` values
    '''
    rq = flow.request
    rsp = flow.response
    url = rq.url
    print("PREFETCH from", url)
    ct = content_type(rsp.headers)
    with Pfx("content_prefetch: %s: %s", ct.content_type, url):
      if ct is None:
        warning('no content-type')
        return
      # parse the content
      if ct.content_type == 'text/html':
        encoding = ct.params.get('charset') or 'utf8'
        soup = BeautifulSoup(content_bs, 'html.parser', from_encoding=encoding)
      # TODO: text/xml, for RSS etc
      else:
        soup = None
      url = URL(url, soup=soup)
      to_fetch = match.pattern_arg
      prefetcher = P.state.prefetcher
      # promote bare string to list
      if isinstance(to_fetch, str):
        to_fetch = [to_fetch]
      with P:
        for pre in to_fetch:
          with Pfx(pre):
            match pre:
              case 'hrefs' | 'srcs':
                print("PREFETCH", pre, "...")
                if soup is None:
                  warning("unoparsed")
                  return
                a_attr = pre[:-1]  # href or src
                for a in soup.find_all('a'):
                  ref = a.get(a_attr)
                  if not ref:
                    continue
                  absurl = url.urlto(ref)
                  print("PREFETCH, put", absurl)
                  prefetcher.put(
                      absurl, get_kw=dict(headers={'x-prefetch': 'no'})
                  )
              case _:
                warning("unhandled prefetch arg")

  @staticmethod
  @uses_pilfer
  def update_tagset_from_meta(
      te: str | TagSet,
      flowstate: FlowState,
      *,
      P: "Pilfer",
      **update_kw,
  ):
    ''' Update a `TagSet` from `flowstate.meta`.
        Return the `TagSet`.

        If `te` is a string, obtain the `TagSet` from `P.sqltags[te]`,
        thus the need to return the `TagSet`.

        This applies the following updates:
        - `meta`: `flowstate.meta.tags`
        - `properties`: `flowstate.meta.properties`
        - `opengraph.*`: from properties commencing with `og:`
        - *type*`.*`: from properties commencing with *type*`:`
          where *type* comes from the `og:type` property
    '''
    # promote a tagset name to an SQLTagSet from P.sqltags
    if isinstance(te, str):
      te = P.sqltags[te]
    # stash the raw meta and properties
    te.meta = flowstate.meta.tags
    te.properties = flowstate.meta.properties
    og = flowstate.opengraph_tags
    te.update(**og)
    og_type = og.get('opengraph.type')
    if og_type:
      type_prefix = f'{og_type}:'
      te.update(
          **{
              f'{og_type}.{cutprefix(k,type_prefix)}': v
              for k, v in te.properties.items()
              if k.startswith(type_prefix)
          }
      )
    # apply whatever update_kw were supplied
    te.update(**update_kw)
    return te

  @classmethod
  @promote
  def entity_key(cls, flowstate: FlowState, **match) -> str | None:
    ''' Given a URL or `FlowState`, return the name of its primary `TagSet`.
        Return `None` if there is none.
    '''
    return None

  @on
  @promote
  def patch_soup_toolbar(
      self,
      flowstate: FlowState,
      match: Mapping[str, Any] | None = None,
      soup=None,
  ):
    # a list of tags for the toolbar
    tags = []
    for link, link_tags in flowstate.links.items():
      for tag in link_tags:
        if tag.attrs.get('type') == "application/rss+xml":
          try:
            href = tag.attrs['href']
          except KeyError:
            warning("no href in %s", tag)
          else:
            widget = BS4Tag(name='a', attrs=dict(href=href))
            widget.string = "RSS"
            tags.append(widget)
    if tags:
      # place a toolbar above the body content
      body = soup.body
      toolbar = BS4Tag(name='div')
      toolbar.append("Toolbar: ")
      for i, widget in enumerate(tags):
        if i > 0:
          toolbar.append(", ")
        toolbar.append(widget)
      toolbar.append('.')
      body.insert(0, BS4Tag(name='br'))
      body.insert(0, toolbar)
    return soup

  @popopts(l=('long_mode', 'Long Mode.'))
  def cmd_ls(self, argv):
    ''' Usage: {cmd} subname [type [key...]]
          List entities for this SiteMap.
    '''
    options = self.options
    long_mode = options.long_mode
    if not argv:
      # list subnames - entity types
      for subname in sorted(set(subname for subname, type_key in self.keys())):
        print(subname)
        if long_mode:
          printt(
              *(
                  [
                      f'  {entity.type_key}',
                      entity.get('fullname') or entity.get('title', ''),
                  ] for entity in map(
                      lambda key: self[key],
                      sorted(self.keys(subname=subname))
                  )
              )
          )
      return 0
    subname = argv.pop(0)
    if not argv:
      # list all entities of this type
      argv = sorted(self.keys(subname=subname))
    Q = ListQueue((self[key] for key in argv), unique=lambda ent: ent.name)
    for ent in self.updated_entities(Q):
      print(ent.type_subname, ent.type_key)
      if long_mode:
        printt(
            *(
                [f'  {tag_name}', tag_value]
                for tag_name, tag_value in sorted(ent.items())
            )
        )

# expose the @on decorator globally
on = SiteMap.on

# Some presupplied site maps.

@dataclass
class DocSite(SiteMap):
  ''' A general purpose doc site map with keys for `.html` and `.js` URLs
      along with several other common extensions.
  '''

  # the URL path suffixes which will be cached
  CACHE_SUFFIXES = tuple(
      [
          # web pages
          '.html',
          # style sheets
          '.css',
          #images
          '.gif',
          '.ico',
          '.jpg',
          '.png',
          '.svg',
          '.webp',
          # scripts
          '.js',
          # fonts
          '.woff2',
      ]
  )

  @on('/', cache_key='{__}')
  @on(
      ''.join(
          (
              '/.*(',
              '|'.join(ext.replace('.', r'\.') for ext in CACHE_SUFFIXES),
              ')$',
          )
      ),
      cache_key='{__}',
  )
  def cache_key_docsite(self, flowstate: FlowState, match: TagSet) -> str:
    return match['cache_key']

@dataclass
class Wikipedia(SiteMap):

  TYPE_ZONE = 'wikipedia'

  URL_KEY_PATTERNS = [
      # https://en.wikipedia.org/wiki/Braille
      (
          (
              '*.wikipedia.org',
              r'/wiki/(?P<title>[^:/]+)$',
          ),
          'wiki/{title}',
      ),
      # https://upload.wikimedia.org/wikipedia/commons/thumb/3/35/Carbonate-outcrops_world.jpg/620px-Carbonate-outcrops_world.jpg
      (
          (
              'upload.wikipedia.org',
              r'/wikipedia/commons/(?<subpath>.*\.(jpg|gif|png))$',
          ),
          'wiki/commons/{subpath}',
      ),
  ]

  @promote
  def url_key(self, url: URL, extra: Mapping | None = None) -> str | None:
    ''' Include the domain name language in the URL key.
    '''
    key = super().url_key(url, extra=extra)
    if key is not None:
      key = f'{cutsuffix(url.hostname, ".wikipedia.org")}/{key}'
    return key

@dataclass
class DockerIO(SiteMap):

  TYPE_ZONE = 'dockerio'

  # https://registry-1.docker.io/v2/linuxserver/ffmpeg/blobs/sha256:6e04116828ac8a3a5f3297238a6f2d0246440a95c9827d87cafe43067e9ccc5d
  @on(
      'registry-*.docker.io',
      r'/v2/.*/blobs/[^/]+:[^/]+$',
      cache_key='blobs/{__}',
  )
  def cache_key_image_blob(self, flowstate: FlowState, match: TagSet) -> str:
    return match['cache_key']
