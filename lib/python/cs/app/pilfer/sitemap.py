#!/usr/bin/env python3

''' Base class for site maps.
'''

from cs.deco import promote
from cs.urlutils import URL

class SiteMap:
  ''' A site map.
  '''

  @promote
  def url_cache_key(self, url: str | URL) -> str | None:
    ''' Return a string which is a persistent cache key for the
        supplied `url`, or `None` for URLs which shoul not be cached
        persistently.

        This default implementation always returns `None`.
    '''
    return None
