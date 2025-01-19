#!/usr/bin/env python3

''' Base class for site maps.
'''

from abc import ABC, abstractmethod
from dataclasses import dataclass
from fnmatch import fnmatch
from functools import cached_property
import re

from cs.deco import promote, Promotable
from cs.urlutils import URL

@dataclass
class URLMatcher(Promotable):
  hostname_fnmatch: str | None
  url_regexp: str

  SITE_PATTERNS = ()

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
  def __call__(self, url: URL):
    if self.hostname_fnmatch is not None and not fnmatch(
        url.hostname, self.hostname_fnmatch):
      return None
    m = self.url_re.match(url.path)
    if m is None:
      return None
    return m.groupdict(), url.query_dict()

@dataclass
class SiteMap:
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

  @promote
  def url_key(self, url: URL) -> str | None:
    ''' Return a string which is a persistent cache key for the
        supplied `url` within the content of this sitemap, or `None`
        for URLs which shoul not be cached persistently.

        A site with semantic URLs might have keys like
        *entity_type*`/`*id*`/`*aspect* where the *aspect* was
        something like `html` or `icon` etc for different URLs
        associated with the same entity.

        This base implementation matches the patterns in `SITE_PATTERNS`
        class attribute which is `()` for the base class.
    '''
    for matcher, keyfn in self.SITE_PATTERNS:
      matcher = URLMatcher.promote(matcher)
      if mq := matcher(url):
        m, q = mq
        fd = dict(q)
        fd.update(m)
        return keyfn.format_map(fd)

# Some presupplied site maps.

@dataclass
class DocSite(SiteMap):
  ''' A general purpose doc site map with keys for `.html` and `.js` URLs.
  '''

  @promote
  def url_key(self, url: URL) -> str | None:
    ''' Return a key for `.html` and `.js` and `..../` URLs.
    '''
    if url.path.endswith(tuple(
        '/ .css .gif .html .ico .jpg .js .png .webp'.split())):
      key = url.path.lstrip('/')
      if key.endswith('/'):
        key += 'index.html'
      return f'{url.hostname}/{key}'
