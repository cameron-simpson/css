#!uusr/bin/env python3

''' Site map for The Sydney Morning Herald.
'''

from contextlib import redirect_stdout
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import cached_property
from getopt import GetoptError
from os.path import basename
import re
import sys
import time
from typing import Any, Mapping, Optional
from urllib.parse import parse_qs, urlparse, urljoin as up_urljoin

from bs4 import BeautifulSoup
from bs4.element import Tag as BS4Tag
from lxml.etree import Element, tostring as xml_tostring
import xml.etree.ElementTree as ET
from typeguard import typechecked

from cs.app.pilfer.pilfer import Pilfer, uses_pilfer
from cs.app.pilfer.rss import RSSChannelMixin, RSSChannelItemMixin
from cs.app.pilfer.sitemap import (
    on, FlowState, pagemethod, ScanData, SiteEntity, SiteMap,
    SiteMapPatternMatch, uses_scandata, with_base_url
)
from cs.binary import bs
from cs.cmdutils import popopts
from cs.deco import promote
from cs.excutils import unattributable
from cs.lex import format_attribute, lc_, printt
from cs.logutils import warning
from cs.mappings import PrefixedMappingProxy
from cs.pfx import Pfx, pfx_method
from cs.resources import RunState, uses_runstate
from cs.rfc2616 import content_type
from cs.seq import unrepeated, with_neighbours
from cs.tagset import TagSet
from cs.urlutils import URL
from cs.upd import print

from cs.debug import trace, X, r, s

class _SMHEntity(SiteEntity):
  ''' The base class for the SMH entities.
  '''
  TYPE_ZONE = 'smh'

class _SMHWebPage(_SMHEntity):

  ##  @property
  ##  @trace(retval=True)
  ##  def articles(self):
  ##    sitemap = self.sitemap
  ##    return [sitemap[Article, ent_id] for ent_id in self.get('article_id', ())]
  ##
  ##  @property
  ##  def topics(self):
  ##    sitemap = self.sitemap
  ##    return [sitemap[Topic, ent_id] for ent_id in self.get('topic_id', ())]

  @uses_runstate
  def soup_href_entities(
      self,
      soup,
      sitemap,
      entity_cls,
      *,
      runstate: RunState,
  ) -> set[_SMHEntity]:
    ''' Return a set of `entity_cls` instances from some soup
        by scanning all the anchor hrefs.
    '''
    ents = set()
    for a in soup.find_all('a'):
      runstate.raiseif()
      href = a.get("href", "")
      if not href:
        continue
      ent_url = URL(href)
      if not ent_url.isabs():
        ent_url = ent_url.resolve(self.sitepage_url)
      ##print(str(ent_url))
      try:
        ent = entity_cls.from_URL(ent_url)
      except ValueError as e:
        # unrecognised, skip
        continue
      ##print("  ->", ent)
      ents.add(ent)
    return ents

  @with_base_url
  def soup_articles(self, soup, *, base_url: str) -> set["Article"]:
    ''' Return a set of `Article`s from some soup.
    '''
    return Article.from_soup_hrefs(soup, base_url=base_url)

  @with_base_url
  def soup_topics(self, soup, *, base_url: str) -> set["Topic"]:
    ''' Return a set of `Topic`s from some soup.
    '''
    return Topic.from_soup_hrefs(soup, base_url=base_url)

  @promote
  def OLDgrok_sitepage(self, flowstate: FlowState, match=None):
    super().grok_sitepage(flowstate, match)
    soup = flowstate.soup
    # record the linked articles and topics
    if match and (date8 := match.get("date8")):
      self.setdefault('date8', date8)
    ##print(f'grok_sitepage({flowstate}): {soup.head=} {soup.head.title=}')
    # we accrue article ids over time
    # even though they vanish from a site TOC
    self["article_id"] = sorted(
        set(self.tags.get("article_id") or [])
        | set(art.type_key for art in self.soup_articles(soup, self.sitemap))
    )
    self["topic_id"] = sorted(
        topic.type_key for topic in self.soup_topics(soup, self.sitemap)
    )

  @property
  def title(self):
    return self.refreshed()['smh.opengraph']['title']

class Article(_SMHWebPage, RSSChannelItemMixin):
  ''' An article.
  '''
  TYPE_SUBNAME = 'article'
  SITEPAGE_URL_PATTERN = '/<wordpath:topic_key>/<title_url_part>-<date8>-p<type_key>.html'
  ##SITEPAGE_URL_FORMAT = '/{topic_url_part}/{title_url_part}-{date8}-p{type_key}.html'

  ##URL_RE = r'/(?P<topic_id>[-a-z]+(/[-a-z]+)*)/[a-z][^/]*-(?P<date8>\d\d\d\d\d\d\d\d)-p(?P<type_key>[^./]{5})\.html$'

  @staticmethod
  @promote
  def url_topic_part(url: URL):
    return "/".join(url.path_elements[:-1])

  @property
  @unattributable
  def mtime(self):
    '''The article modified_time as a UNIX timestamp.
    '''
    try:
      properties = self['smh.html.properties']
    except KeyError as e:
      warning(f'{self.name}.mtime: no .properties: {e}')
      return time.time()
    ##dt = self.tags.get("properties", {}).get("article:modified_time")
    dt = properties.get("article:modified_time")
    if dt is None:
      date8 = self.get("date8")
      if not date8:
        return time.time()
      dt = datetime.strptime(date8, '%Y%m%d').astimezone(timezone.utc)
    elif not isinstance(dt, datetime):
      dt = datetime.fromisoformat(dt)
    return dt.timestamp()

  @format_attribute
  def topic_url_part(self):
    ''' The topic section for use in the sitepage URL.
    '''
    return self["topic"]

  @format_attribute
  def title_url_part(self):
    ''' The title section for use in the sitepage URL.
    '''
    return lc_(self.refreshed().get('smh.opengraph', {}).get('title', ''))
    return lc_(self.get('opengraph.type.twitter:title', self.title))

  @property
  def topic(self) -> "Topic":
    ''' The associated `Topic` instance.
    '''
    return self.refreshed().topic_ent

  def rss_category(self):
    return self.topic

  def rss_description(self):
    meta = self['smh.html.meta']
    try:
      return f'{meta["description"]} - {meta.get("author","no author")}, {meta["pubdate"]}'
    except KeyError as e:
      warning(f'{self.name}.rss_description: {e}')
      printt(meta)
      breakpoint()
      return "no description"

  def rss_pubdate(self) -> None | str:
    ''' Return the publication date, or `None` if not available.
    '''
    return self.mtime
    ##return self["properties"].get('article:published_time')

  def update_from_flowstate_meta(self, flowstate: FlowState, **update_kw):
    ''' Call `SiteMap.update_tagset_from_meta` to apply the `flowstate`
        meta tags to this entity.
    '''
    self.sitemap.update_tagset_from_meta(self, flowstate, **update_kw)

  @trace
  @uses_scandata
  @promote
  def scan_sitepage(
      self,
      flowstate: FlowState,
      match: Optional[Mapping[str, Any]] = None,
      *,
      scandata: ScanData,
  ) -> ScanData:
    scanadata = super().scan_sitepage(flowstate, scandata=scandata)
    data = scandata[self]
    # TODO: date, byline etc
    data["title"] = flowstate.meta.tags["title"]
    topic_id = self.url_topic_part(flowstate.url)
    assert '.' not in topic_id
    data["topic_id"] = topic_id
    return scandata

  @trace
  @promote
  def OBSOLETE_grok_sitepage(
      self, flowstate: FlowState, match: Optional[Mapping[str, Any]] = None
  ):
    if str(flowstate.url).startswith('/'):
      breakpoint()
    super().grok_sitepage(flowstate, match=match)
    self["title"] = flowstate.meta.tags["title"]
    topic = self.url_topic_part(flowstate.url)
    assert '.' not in topic
    self["topic"] = topic
    # infill some sometimes missing items so that we do not
    # gratuitously refetch the page
    self.setdefault('opengraph.description', None)
    ##self.printt()
    return self

class Topic(_SMHWebPage, RSSChannelMixin):
  ''' A topic.
  '''
  TYPE_SUBNAME = 'topic'
  SITEPAGE_URL_PATTERN = '/<wordpath:type_key>'
  ##patterns##SITEPAGE_URL_FORMAT = '/{type_key}'
  ##patterns##URL_RE = r'/(?P<type_key>[-a-z]+(/[-a-z]+)*)$'
  # topics become stale after an hour
  STALE_LIFESPAN = 3600

  @classmethod
  def from_anchor(cls, sitemap, a: BS4Tag):
    ''' Make a `Topic` from an anchor (an `A` bs4 tag with an `HREF`).
        If we don't have a title yet, apply the anchor text.
        Return the `Topic` instance.
    '''
    self = sitemap[cls, a["href"].lstrip('/')]
    self.setdefault("title", a.string)
    return self

  @pfx_method
  @pagemethod
  @uses_scandata
  def scan_sitepage(
      self, flowstate: FlowState, *, scandata: ScanData
  ) -> ScanData:
    ''' Scan `flowstate.soup` for `Article` references.
    '''
    scandata = super().scan_sitepage(flowstate, scandata=scandata)
    data = scandata[self]
    soup = flowstate.soup
    data['article_id'] = sorted(
        set(article.type_key for article in self.soup_articles(soup))
    )
    return scandata

  @property
  def short_title(self):
    return self.title.split('|', 1)[0].strip()

  @property
  def articles(self):
    ''' The `.articles` property returns the articles prefilled with a topic.
        This is because `Article.sitepage_url` is partly derived from the topic.
    '''
    type_key = self.type_key
    articles = set(self.refreshed().article_ents)
    for article in articles:
      print(article.name, id(article))
      article.setdefault("topic_id", type_key)
    ##breakpoint()
    return articles

  def refresh_related(self):
    return self.articles

  def refresh_related1(self):
    return self.authors

  def rss_content_signature(self):
    ''' Return the RSS content signature - the ordered list of article ids.
    '''
    return sorted(self.article_id)

  def rss_items(self):
    ''' The RSS items are the articles.
    '''
    return self.articles

class Author(Topic):
  ''' An author, much like a topic by for a journalist.
  '''
  TYPE_SUBNAME = 'author'
  # https://www.smh.com.au/by/richard-glover-hve2q
  SITEPAGE_URL_PATTERN = '/by/<name_url_part>-<type_key>'

  @pfx_method
  @pagemethod
  @uses_scandata
  def scan_sitepage(
      self, flowstate: FlowState, *, scandata: ScanData
  ) -> ScanData:
    ''' Scan `flowstate.soup` for `Article` references.
    '''
    scandata = super().scan_sitepage(flowstate, scandata=scandata)
    data = scandata[self]
    soup = flowstate.soup
    try:
      og = self.opengraph
    except AttributeError as e:
      print("NO .OPENGRAPH")
    else:
      try:
        data['fullname'] = og['title'].split('|', 1)[0].strip()
      except (KeyError, AttributeError) as e:
        warning(str(e))
    return scandata

class TOC(_SMHWebPage):
  ''' Table of contents entities.
  '''

  TYPE_SUBNAME = 'toc'
  SITEPAGE_URL_FORMAT = '/{type_key}'

@dataclass
class SMHMap(SiteMap):

  TYPE_ZONE = 'smh'
  HasTagsClass = _SMHEntity
  BASE_DOMAIN = 'smh.com.au'
  URL_DOMAIN = f'www.{BASE_DOMAIN}'

  PREFETCH_PATTERNS = [
      (
          (URL_DOMAIN, r'/$'),
          'hrefs',
      ),
      (
          (URL_DOMAIN, Article),
          'hrefs',
      ),
  ]

  @on(
      URL_DOMAIN,
      Article,
      cache_key='story/{date8}--{type_key}',
  )
  def cache_key_smh(self, flowstate, match_tags: TagSet) -> str:
    return match_tags.cache_key.format_map(match_tags)

##  @on(
##      URL_DOMAIN,
##      Article,
##      cache_key='story/{date8}--{type_key}',
##  )
##  @trace
##  @typechecked
##  def scan_article(self, flowstate, match: Mapping = None) -> ScanData:
##    print("match", match)
##    article = self[Article, match['type_key']]
##    return article.scan_sitepage(flowstate)

  @on(
      ##('content_type', 'text/html'),
      URL_DOMAIN,
      r'\.html$',
  )
  @trace
  @uses_pilfer
  def patch_soup_smh(
      self, flowstate, match_tags: TagSet, soup, P: Pilfer = None
  ):
    ''' Modify the soup of a SMH page to put "smh:" at the front of the title.
    '''
    print("SMHMap.patch_smh: URL =", flowstate.url)
    title = soup.head.title
    title.string = f'smh: {title.string}'
    return soup

  @on(URL_DOMAIN)
  @typechecked
  def grok_title(self, flowstate, match_tags: TagSet) -> TagSet:
    return TagSet(title=flowstate.soup.title.string)

  @on(URL_DOMAIN, Article)
  def grok_article(self, flowstate: FlowState, match) -> TagSet:
    article = self[Article, match["type_key"]]
    article["date8"] = match["date8"]
    article.grok_sitepage(flowstate)
    return article

  @cached_property
  def topics(self):
    '''The master list of topics from the SMH Site Guide.
    '''
    return self[TOC, 'siteguide'].topics

  @popopts(rss=('rss', 'Emit TOC in RSS format.'))
  def cmd_toc(self, argv):
    ''' Usage: {cmd} [topic|toc-page]
          Emit RSS XML for the contents of a topic or a page with links to articles.
    '''
    with redirect_stdout(sys.stderr):
      if not argv:
        src = self.url_root
      else:
        src = argv.pop(0)
      if argv:
        raise GetoptError(f'extra arguments after topic|toc-page: {argv!r}')
      if src.startswith('https://'):
        try:
          topic = Topic.from_URL(src, self)
        except ValueError as e:
          raise GetoptError(f'does not look like a Topic URL: {e}') from e
      elif m := Topic.url_re.match(f'/{src.lstrip("/")}'):
        topic = self[Topic, m.group('type_key')]
      else:
        raise GetoptError(
            f'unrecognised topic|toc-page, expected https:// URL or topic: {src!r}'
        )
      topic.refresh()
      articles = topic.articles
      articles = sorted(
          articles,
          key=lambda article: (
              article.topic.title,
              article.mtime,
              ##article["properties"].  get("article:modified_time", datetime.utcnow(),),
              article.title,
          ),
      )
      toc = []
      prev = None
      for prev_article, article, _ in with_neighbours(articles):
        toc.append(
            [
                article.name,
                (
                    article.topic.short_title if not prev_article
                    or article.topic_id != prev_article.topic_id else ''
                ),
                datetime.fromtimestamp(article.mtime).strftime("%Y-%m-%d"),
                article.title,
            ]
        )
        toc.append(['', '', '', article.sitepage_url])
    if self.options.rss:
      rss = topic.rss(items=articles)
      print('<?xml version="1.0" encoding="UTF-8"?>')
      print(xml_tostring(rss, encoding='unicode', pretty_print=True), end='')
    else:
      printt(*toc)

if __name__ == '__main__':
  SMH = SMHMap()
  print(SMH.topics)
  breakpoint()
