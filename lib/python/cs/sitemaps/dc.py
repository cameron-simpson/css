#!/usr/bin/env python3

from dataclasses import dataclass
from os.path import basename
import re
from types import SimpleNamespace as NS
from typing import Self

from bs4 import BeautifulSoup, Tag as BS4Tag
from typeguard import typechecked

from cs.app.pilfer.sitemap import (
    FlowState, ScanData, SiteEntity, SiteMap, SiteMapPatternMatch, URLPattern,
    uses_scandata
)
from cs.bs4utils import child_tags
from cs.deco import promote
from cs.logutils import warning
from cs.pfx import Pfx
from cs.rfc2616 import content_type
from cs.urlutils import URL
from cs.upd import print

from cs.debug import trace, s

class _DCCOMEntity(SiteEntity):
  TYPE_ZONE = 'dccom'

class DCCOMCollection(_DCCOMEntity):
  TYPE_SUBNAME = 'collection'
  SITEPAGE_URL_PATTERN = URLPattern(
      '/collections/edt-swampthing-by-rickveitch',
      'www.dcuniverseinfinite.com',
  )

class DCCOMTalent(_DCCOMEntity):
  ''' Artists, authors etc.
  '''
  TYPE_SUBNAME = 'talent'
  SITEPAGE_URL_PATTERN = '/talent/<type_key>'

class _DCCOMPublication(_DCCOMEntity):

  @staticmethod
  def parse_info_section(soup, section_title: str) -> dict[str, BS4Tag]:
    ''' Extract a mapping if info section title to its DIVs.
    '''
    subsections = {}
    # find the talent
    for h3 in soup.find_all('h3'):
      if h3.string == section_title:
        break
    else:
      h3 = None
    if h3 is None:
      warning(f'no <h3>{section_title=}</h3> header')
      breakpoint()
      return subsections
    overdiv = h3.parent.parent.parent
    for i, type_overdiv in enumerate(overdiv.find_all('div', recursive=False)):
      # skip the heading div
      if i == 0: continue
      div1 = type_overdiv.div
      if div1 is None: continue
      list_div = div1.div
      if list_div is None:
        warning(f'talents: no inner div found in {list_div}')
        continue
      type_div, info_divs = list_div.find_all('div', recursive=False)
      type_label = type_div.p.string.strip().rstrip(':').lower().replace(
          ' ', '_'
      )
      subsections[type_label] = info_divs
    return subsections

  @uses_scandata
  @promote
  def scan_sitepage(
      self, flowstate: FlowState, *, scandata: ScanData
  ) -> ScanData:
    ''' Common scan of talent and specs for any publication.
    '''
    super().scan_sitepage(flowstate, scandata=scandata)
    data = scandata[self]
    soup = flowstate.soup
    sitemap = self.sitemap
    talent_ids = data["talent_id"] = []
    for type_label, info_divs in self.parse_info_section(soup,
                                                         "Talent").items():
      type_label = {
          'art_by': 'artist',
          'cover': 'cover_artist',
      }.get(type_label, type_label)
      type_field = f'{type_label}_id'
      ids = data[type_field] = []
      for tdiv in info_divs:
        a = tdiv.p.a
        href = a.attrs.get('href')
        if not href:
          warning(f'no HREF in {a}')
        else:
          matched = DCCOMTalent.match_url(href)
          if not matched:
            warning(f'HREF {href!r} does not look like a DCCOMTalent')
          else:
            talent_id = matched['type_key']
            tdata = scandata[DCCOMTalent, talent_id]
            tdata['fullname'] = a.string.strip()
            talent_ids.append(talent_id)
            ids.append(talent_id)
    # parse the specs section
    prices = {}
    for spec_label, info_divs in self.parse_info_section(soup,
                                                         "SPECS").items():
      text = ", ".join(info_div.get_text(strip=True) for info_div in info_divs)
      if spec_label.endswith('_price'):
        prices[spec_label.removesuffix('_price')] = text
      else:
        data[spec_label] = text
    if prices:
      data['prices'] = prices
    return scandata

class DCCOMCharacter(_DCCOMEntity):
  TYPE_SUBNAME = 'character'
  SITEPAGE_URL_PATTERN = '/characters/<type_key>'

class DCCOMgraphicNovel(_DCCOMPublication):
  TYPE_SUBNAME = 'graphic_novel'
  SITEPAGE_URL_PATTERN = '/graphic-novels/<series_lc>/<type_key>'

class DCCOMSeriesIssue(_DCCOMPublication):
  TYPE_SUBNAME = 'series_issue'
  SITEPAGE_URL_PATTERN = '/comics/<series_lc>/<type_key>'

  ISSUE_TITLE_re = re.compile(
      r'(?P<series_title>.*[^)])'
      r'(\s+\((?P<start_year>\d{4})-\))?'
      r'\s+#(?P<issue_number>\d+)'
      r'\s*\|\s*(?P<publisher>\S.*\S)'
  )

  ## TODO: mediate this through a DCCOMSeries

  def sibling_url(self, issue_number: int) -> str:
    ''' Return the URL for a sibling issue, derived from `self.sitepage_url`.
    '''
    prefix = self.sitepage_url.rsplit('-', 1)[0]
    return f'{prefix}-{issue_number}'

  def siblings(self, max_issue_number=None) -> list[Self]:
    ''' Return a list of subling `DCCOMSeriesIssue` instances up to `max_issue_number`.
        The default maximum is `self.issue_number-1`.
    '''
    if max_issue_number is None:
      max_issue_number = self.issue_number - 1
    cls = type(self)
    sitemap = self.sitemap
    return [
        sitemap[cls, basename(self.sibling_url(issue_number))]
        for issue_number in range(1, max_issue_number + 1)
    ]

  @classmethod
  def parse_issue_title(cls, title_text: s) -> NS:
    ''' Parse a series issue title text with a regpex, return a
        `types.SimpleNamespace` containing the parsed values.

        Example:

            >>> DCCOMSeriesIssue.parse_issue_title('NEW TITANS (2023-) #37 | DC')
            namespace(series_title='New Titans', start_year=2023, issue_number=37, publisher='DC')
    '''
    m = cls.ISSUE_TITLE_re.match(title_text)
    if m is None:
      print("TITLE PARSE FAIL")
      print("re =", cls.ISSUE_TITLE_re)
      print(f'{title_text=}')
      warning(f'parse_issue_title: {title_text=} vs {cls.ISSUE_TITLE_re}')
      ##breakpoint()
      return None
    md = m.groupdict()
    return NS(
        series_title=md['series_title'].title(),
        start_year=md.get('start_year') and int(md['start_year']),
        issue_number=int(md['issue_number']),
        publisher=md['publisher'],
    )

  @uses_scandata
  @promote
  def scan_sitepage(
      self, flowstate: FlowState, *, scandata: ScanData
  ) -> ScanData:
    super().scan_sitepage(flowstate, scandata=scandata)
    data = scandata[self]
    soup = flowstate.soup
    sitemap = self.sitemap
    # parse the title into fields
    title = flowstate.opengraph['title']
    if title.startswith('404 '):
      warning(f"DC's sad non404 404 error page, skipping")
      return scandata
    pt = self.parse_issue_title(title)
    if pt is None:
      warning(f'{flowstate.url.short}: could not parse {title=}')
    else:
      data.update(pt.__dict__)
    return scandata

  @property
  @trace
  def series_lc(self):
    return self.type_key.rsplit('-', 1)[0]

  def refresh_related1(self):
    yield from self.talent_ents
    yield from self.siblings()

class DCCOMBlogEntry(_DCCOMEntity):
  TYPE_SUBNAME = 'blog_entry'
  SITEPAGE_URL_PATTERN = '/blog/<isodate:date>/<title_url_part>'

class DCCOMBlog(_DCCOMEntity):

  TYPE_SUBNAME = 'blog'

  @uses_scandata
  @promote
  def scan_sitepage(
      self, flowstate: FlowState, *, scandata: ScanData
  ) -> ScanData:
    super().scan_sitepage(flowstate, scandata=scandata)
    data = scandata[self]
    soup = flowstate.soup
    for h2 in soup.find_all('h2'):
      if h2.string.lower().startswith('latest '):
        break
    else:
      h2 = None
    if h2 is not None:
      print("FOUND", h2)
      h2_div_div = h2.parent.parent
      ul = h2_div_div.next_sibling.find("ul")
      for li in child_tags(ul, "li"):
        a = li.find('a')
        print(a)
        breakpoint()
        data['title'] = a.attrs['title']
    return scandata

@dataclass
class DCComMap(SiteMap):
  URL_DOMAIN = 'www.dc.com'
  EntityClass = _DCCOMEntity

  def cmd_rss(self, argv):
    blogpage = self[DCCOMBlog, 'comics']
    scandata = blogpage.scan_sitepage('https://www.dc.com/comics')
    scandata.printt()
    breakpoint()

@dataclass
class DCMap(SiteMap):

  URL_DOMAIN = 'www.dc.com'

  URL_KEY_PATTERNS = [
      (
          # https://imgix-media.wbdndc.net/ingest/book/preview/0143fb3c-d9c9-4bd2-b903-6df13593f19d/25a3e1fa-2f34-411d-8c27-1a5b2336b071/1732085984.jpg?auto=format,compress&w=480&h=130&fit=crop&crop=entropy&q=0&px=8
          (
              'imgix-media.wbdndc.net',
              r'/ingest/[a-z].*/.*\.(jpg|png|gif)$',
          ),
          '{__}',
      ),
  ]

  PREFETCH_PATTERNS = []

  @typechecked
  def content_prefetch(
      self, match: SiteMapPatternMatch, flow, content_bs: bytes
  ):
    ''' The SMH prefetch handler.
    '''
    print("prefetch from", flow)
    with Pfx(
        "%s.content_prefetch(%s,%s,%d bytes)",
        self.__class__.__name__,
        match,
        flow,
        len(content_bs),
    ):
      rq = flow.request
      rsp = flow.response
      ct = content_type(rsp.headers)
      if ct is None:
        warning('no content-type')
        return
      if ct.content_type != 'text/html':
        warning('not HTML')
        return
      encoding = ct.params.get('charset') or 'utf8'
      soup = BeautifulSoup(content_bs, 'html.parser', from_encoding=encoding)
      url = URL(rq.url, soup=soup)
      for a in soup.find_all('a'):
        href = a.get('href')
        if not href:
          continue
        print("  href", url.urlto(href))
