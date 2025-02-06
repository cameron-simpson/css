#!/usr/bin/env python3

''' Base class for site maps.
'''

from collections import ChainMap
from dataclasses import dataclass
from fnmatch import fnmatch
from functools import cached_property
import re
from typing import Any, Iterable, Mapping, Optional, Tuple

from cs.deco import promote, Promotable
from cs.lex import cutsuffix
from cs.urlutils import URL

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
    return re.compile(self.url_regexp)

  @promote
  def match(
      self,
      url: URL,
      extra: Optional[Mapping] = None,
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

@dataclass
class SiteMap(Promotable):
  ''' A base class for site maps.

      A `Pilfer` instance obtains its site maps from the `[sitemaps]`
      clause in the configuration file, see the `Pilfer.sitemaps`
      property for specific.

      Example:

          docs.python.org = docs:cs.app.pilfer.sitemap:DocSite
          docs.mitmproxy.org = docs
          *.readthedocs.io = docs
  '''

  name: str

  URL_KEY_PATTERNS = ()

  @classmethod
  def from_str(
      cls, sitemap_name: str, *, P: Optional["Pilfer"] = None
  ) -> "SiteMap":
    ''' Return the `SiteMap` instance known as `sitemap_name` in the ambient `Pilfer` instance.
    '''
    if P is None:
      from .pilfer import Pilfer
      P = Pilfer.default()
      if P is None:
        raise ValueError(
            f'{cls.__name__}.from_str({sitemap_name!r}): no Pilfer to search for sitemaps'
        )
    for name, sitwmap in P.sitemaps:
      if name == sitemap_name:
        return sitemap
    raise ValueError(
        f'{cls.__name__}.from_str({sitemap_name!r}): unknown sitemap name'
    )

  def matches(
      self,
      url: URL,
      patterns: Iterable,  # [Tuple[Tuple[str, str], Any]],
      extra: Optional[Mapping] = None,
  ) -> Iterable[Tuple[str, str, dict, dict]]:
    ''' A generator to match `url` against `patterns`, an iterable
        of `(match_to,arg)` 2-tuples which yields
        a `(match_to,arg,match,mapping)` 4-tuple for each pattern
        which matches `url`.

        Parameters:
        * `url`: a `URL` to match
        * `patterns`: the iterable of `(match_to,arg)` 2-tuples
        * `extra`: an optional mapping to be passed to the match function

        Each returned match is a `(match_to,arg,match,mapping)` 4-tuple
        with the following values:
        * `match_to`: the pattern's first component, used for matching
        * `arg`: the pattern's second component, used by the caller to produce some result
        * `match`: the match object returned from the match function
        * `mapping`: a mapping of values cleaned during the match

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
        mapping = dict(
            (
                (attr, getattr(url, attr)) for attr in (
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
            )
        )
        # set _ to the url.path, __ to histname/path
        mapping.update(
            _=url.cleanrpath,
            __=f'{url.hostname}/{url.cleanrpath}',
        )
        mapping.update(url.query_dict())
        mapping.update(match)
        yield match_to, arg, match, mapping

  def match(
      self,
      url: URL,
      patterns: Iterable,
      extra: Optional[Mapping] = None,
  ) -> Tuple[str, str, dict, dict] | None:
    ''' Scan `patterns` for a match to `url`,
        returning the first match tuple from `self.matches()`
        or `None` if no match is found.
    '''
    for match_to, on_match, match, mapping in self.matches(url, patterns,
                                                           extra=extra):
      return match_to, on_match, match, mapping
    return None

  @promote
  def url_key(
      self,
      url: URL,
      extra: Optional[Mapping] = None,
  ) -> str | None:
    ''' Return a string which is a persistent cache key for the
        supplied `url` within the content of this sitemap, or `None`
        for URLs which do not have a key i.e. should not be cached persistently.

        A site with semantic URLs might have keys like
        *entity_type*`/`*id*`/`*aspect* where the *aspect* was
        something like `html` or `icon` etc for different URLs
        associated with the same entity.

        This base implementation matches the patterns in `URL_KEY_PATTERNS`
        class attribute which is `()` for the base class.
    '''
    matched = self.match(url, self.URL_KEY_PATTERNS, extra=extra)
    if not matched:
      return None
    match_to, keyfn, match, mapping = matched
    return keyfn.format_map(ChainMap(mapping, extra or {}))

# Some presupplied site maps.

@dataclass
class DocSite(SiteMap):
  ''' A general purpose doc site map with keys for `.html` and `.js` URLs
      along with several other common extensions.
  '''

  # the URL path suffixes which will be cached
  CACHE_SUFFIXES = tuple(
      '/ .css .gif .html .ico .jpg .js .png .svg .webp .woff2'.split()
  )

  @promote
  def url_key(self, url: URL) -> str | None:
    ''' Return a key for `.html` and `.js` and `..../` URLs.
    '''
    if url.path.endswith(self.CACHE_SUFFIXES):
      key = url.path.lstrip('/')
      if key.endswith('/'):
        key += 'index.html'
      return f'{url.hostname}/{key}'

@dataclass
class Wikipedia(SiteMap):

  URL_KEY_PATTERNS = [
      # https://en.wikipedia.org/wiki/Braille
      (
          (
              '*.wikipedia.org',
              'wiki/(?P<title>[^:/]+)$',
          ),
          'wiki/{title}',
      ),
  ]

  @promote
  def url_key(self, url: URL, extra: Optional[Mapping] = None) -> str | None:
    ''' Include the domain name language in the URL key.
    '''
    key = super().url_key(url, extra=extra)
    if key is not None:
      key = f'{cutsuffix(url.hostname, ".wikipedia.org")}/{key}'
    return key
