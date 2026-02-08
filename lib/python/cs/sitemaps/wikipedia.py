#!/usr/bin/env python3

from dataclasses import dataclass
from getopt import GetoptError

from cs.app.pilfer.sitemap import SiteMap, SiteEntity
from cs.deco import promote
from cs.lex import printt
from cs.logutils import setup_logging, warning
from cs.pfx import Pfx
from cs.urlutils import URL

from cs.debug import trace

class _WikiEntity(SiteEntity):

  pass

class WikiPage(_WikiEntity):

  @classmethod
  def from_pageid(cls, pageid: int):
    return

@dataclass
class MediaWiki(SiteMap):
  ''' The SiteMap for `wikipedia.org'.
  '''

  # the archetypical site is, of course, wikipedia
  TYPE_ZONE = 'wikipedia'
  BASE_DOMAIN = 'wikipedia.org'

  ##URL_DOMAIN = f'www.{BASE_DOMAIN}'

  def api_GET(self, action='parse', format='json', **rqparams):
    ''' Make an API call with the supplied keyword parameters.
        The `action` parameter defaults to `parse`
        and the `format` parameter defaults to `json`.
    '''
    return self.pilfer.GET(
        f'https://{self.URL_DOMAIN}{self.API_ENDPOINT}',
        params=dict(
            action=action,
            format=format,
            **rqparams,
        ),
    )

  @trace
  def json(self, **rqparams) -> dict:
    ''' Make an API call with the supplied keyword parameters.
        The `action` parameter defaults to `parse`
        and the `format` parameter will be supplied as `json`.
        Return the decoded JSON.
    '''
    rsp = self.api_GET(format='json', **rqparams)
    rsp.raise_for_status()
    return rsp.json()

class FandomEntity(_WikiEntity):
  pass

class FandomSiteMap(MediaWiki):
  BASE_DOMAIN = 'fandom.com'

class _DCFandomEntity(FandomEntity):
  TYPE_ZONE = 'dcfandom'

class TVSeries(_DCFandomEntity):
  TYPE_SUBNAME = 'tvseries'

class DCFandomSiteMap(FandomSiteMap):
  TYPE_ZONE = 'dcfandom'
  HasTagsClass = _DCFandomEntity
  URL_DOMAIN = f'dc.{FandomSiteMap.BASE_DOMAIN}'
  API_ENDPOINT = '/en/api.php'

if __name__ == '__main__':
  import sys
  from pprint import pprint
  setup_logging()
  if len(sys.argv) < 2:
    raise GetoptError('missing page_specs')
  dcf = DCFandomSiteMap()
  with dcf.pilfer:
    for page_spec in sys.argv[1:]:
      with Pfx(page_spec):
        print(f'{page_spec=}')
        if not page_spec:
          warning("empty pagespec")
          continue
        try:
          pageid = int(page_spec)
        except ValueError:
          warning(f'not a pageid: {page_spec=}')
          if '_' in page_spec and len(page_spec.split()) == 1:
            data = dcf.json(page=page_spec)
          else:
            search, titles, descs, urls = dcf.json(
                action='opensearch', search=page_spec
            )
            printt(
                ["search results:"],
                ['search', search],
                ['titles', titles],
                ['descs', descs],
                ['urls', urls],
            )
          print("fetching page:", titles[0])
          data = dcf.json(page=titles[0])
        else:
          data = dcf.json(pageid=pageid)
        pprint(data)
