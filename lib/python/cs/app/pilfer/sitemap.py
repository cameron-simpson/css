#!/usr/bin/env python3

''' Base class for site maps.
'''

from abc import ABC, abstractmethod
from dataclasses import dataclass

from cs.deco import promote
from cs.urlutils import URL

@dataclass
class SiteMap(ABC):
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

  @abstractmethod
  @promote
  def url_key(self, url: URL) -> str | None:
    ''' Return a string which is a persistent cache key for the
        supplied `url` within the content of this sitemap, or `None`
        for URLs which shoul not be cached persistently.

        This default implementation always returns `None`.

        A site with semantic URLs might have keys like
        *entity_type*`/`*id*`/`*aspect* where the *aspect* was
        something like `html` or `icon` etc for different URLs
        associated with the same entity.
    '''
    return None
