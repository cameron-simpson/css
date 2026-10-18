#!/usr/bin/env python3

# we're geting a 403 prove-you're-a-human from pilfer, not clear why

from cs.app.pilfer.sitemap import SiteEntity, SiteMap
from cs.bs4utils import BS4Tag, Widget
from cs.feeds import FeedEntryMixin

class MovieCoverImage(Widget):
  ''' A mive cover image.
  '''

  # https://img.rgstatic.com/content/movie/9e609a81-21db-456b-ba08-796a059eb2e3/poster-342.webp

  ##@classmethod
  ##def find_all(cls,soup:BS4Tag)->list[BS4Tag]:

class _RGEntity(SiteEntity):
  REFRESH_LIFESPAN = 7 * 86400  # 1 week
  TYPE_ZONE = 'reelgood'

class Movie(_RGEntity, FeedEntryMixin):
  ''' A movie.
  '''

  # the primary page in the site
  SITEPAGE_URL_PATTERN = '/movie/<type_key>'

class ReelGood(SiteMap):
  EntityClass = _RGEntity
  BASE_DOMAIN = 'reelgood.com'
  URL_DOMAIN = BASE_DOMAIN
