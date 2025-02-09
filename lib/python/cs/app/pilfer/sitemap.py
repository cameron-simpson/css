#!/usr/bin/env python3

''' Base class for site maps.
'''

from dataclasses import dataclass
from fnmatch import fnmatch
from functools import cached_property
import re

from cs.deco import promote, Promotable
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
  def __call__(self, url: URL) -> dict | None:
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

  URL_KEY_PATTERNS = ()

  @promote
  def url_key(self, url: URL) -> str | None:
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
    for matcher, keyfn in self.URL_KEY_PATTERNS:
      matcher = URLMatcher.promote(matcher)
      if (m := matcher(url)) is not None:
        format_map = url.query_dict()
        format_map.update(m)
        return keyfn.format_map(fd)

# Some presupplied site maps.

@dataclass
class DocSite(SiteMap):
  ''' A general purpose doc site map with keys for `.html` and `.js` URLs
      along with several other common extensions.
  '''

  @promote
  def url_key(self, url: URL) -> str | None:
    ''' Return a key for `.html` and `.js` and `..../` URLs.
    '''
    if url.path.endswith(tuple(
        '/ .css .gif .html .ico .jpg .js .png .svg .webp'.split())):
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
  def url_key(self, url: URL) -> str | None:
    ''' Include the domain name language in the URL key.
    '''
    key = super().url_key(url)
    if key is not None:
      key = f'{cutsuffix(url.hostname,".wikipedia.org")}/{key}'
    return key
