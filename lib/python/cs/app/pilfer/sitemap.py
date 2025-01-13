#!/usr/bin/env python3

''' Base class for site maps.
'''

from dataclasses import dataclass

from cs.deco import promote
from cs.urlutils import URL

@dataclass
class SiteMap:
  ''' A site map.
  '''

  name: str

  @promote
  def url_key(self, url: str | URL) -> str | None:
    ''' Return a string which is a persistent cache key for the
        supplied `url` within the content of this sitemap, or `None`
        for URLs which shoul not be cached persistently.

        This default implementation always returns `None`.
    '''
    return None
