#!/usr/bin/env python3

from dataclasses import dataclass

from cs.app.pilfer.sitemap import FlowState, on, SiteMap
from cs.tagset import TagSet

@dataclass
class DocSite(SiteMap):
  ''' A general purpose doc site map with cache keys for `.html` and `.js` URLs
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
