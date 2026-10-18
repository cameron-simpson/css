#!/usr/bin/env python3

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from functools import cached_property, partial
from getopt import GetoptError
import json
import re

from typeguard import typechecked

from cs.app.pilfer.sitemap import (
    FlowState, SiteEntity, SiteMap, SiteWidget, URLPattern, on, uses_scandata
)
from cs.bs4utils import child_tags, printt_soup, Table, BS4Tag, Widget
from cs.cmdutils import popopts
from cs.deco import methodif, promote
from cs.lex import printt
from cs.logutils import warning
from cs.pfx import Pfx
from cs.tagged import ScanData
from cs.tagset import TagSet
from cs.urlutils import URL


# worth parsing:
# https://www.amazon.com.au/SANNO-Stainless-Organizer-Freezers-Adjustable/dp/B0DBZMGRYR?th=1

# Unicode points found in Amazon whitespace areas
AMAZON_WS = ' \t\r\n\N{LEFT-TO-RIGHT MARK}\N{RIGHT-TO-LEFT MARK}'

ASIN_re = re.compile(r'[\dA-Z]{10}')

def is_valid_asin(asin: str):
  ''' Test is a string is a valid ASIN.
      Amazon's own dowcs are a bit vague.
  '''
  m = ASIN_re.match(asin)
  return m and m.end() == len(asin)

def asin_from_href(href, marker='/dp/'):
  ''' Return the ASIN from an `href` like ....`/dp/`*ASIN*...
      The `/dp/` (digital product) marker may be overridden by
      the `marker` parameter.
      Raises `valueError` if the `href` is not matched.
  '''
  for m in ASIN_re.finditer(href):
    start = m.start()
    if start == 0 or start >= len(
        marker) and href[start - len(marker):start] == marker:
      end = m.end()
      if end == len(href) or href[end] in '/?#':
        return m.group(0)
  raise ValueError('no ASIN found')

def date_from_pubdate(pubdate: str) -> date:
  ''' Parse an Amazon publication date text into a `datetime.date`.
  '''
  # date.strptime only arrived in 3.14
  return datetime.strptime(pubdate.strip(), '%d %B %Y').date()

def prune_book_title(title, series: str | None = None):
  ''' Strip cruft from a book title as cited in Amazon,
      which often includes the series title and other junk.

      Examples:

          >>> prune_book_title('What Happened In London: A DI Adams mystery (book one) - an urban fantasy with monsters, ducks, & snark', series='A DI Adams mystery')
          'What Happened In London'
          >>> prune_book_title('All Out of Leeds: A DI Adams mystery - magic, menace, & snark in a Yorkshire urban fantasy (Book Two)', series='A DI Adams mystery')
          'All Out of Leeds'
          >>> prune_book_title('Trouble Brewing in Harrogate: A DI Adams mystery - magic, menace, & snark in a Yorkshire urban fantasy (Book Three)')
          'Trouble Brewing in Harrogate: A DI Adams mystery - magic, menace, & snark in a Yorkshire urban fantasy'
          >>> prune_book_title('A Right Shambles in York: A DI Adams mystery - magic, menace, & snark in a Yorkshire urban fantasy', series='A DI Adams Mystery')
          'A Right Shambles in York'
  '''
  if m := re.search(r'\s+\(book \S+\)', title, re.IGNORECASE):
    title = title[:m.start()]
  if series is not None:
    if (offset := title.lower().find(f': {series.lower()}')) > 0:
      title = title[:offset]
  return title

class _AmazonEntity(SiteEntity):
  ''' The base class for Amazon entities.
  '''

  TYPE_ZONE = 'amazon'

  @trace
  def generic_grok_amazon_page(self, flowstate: FlowState):
    breakpoint()
    raise RuntimeError
    meta = flowstate.meta
    soup = flowstate.soup
    printt(*flowstate.meta.tags.items())
    self["title"] = meta.tags["title"]
    prod_byline_div = soup.find('div', id='bylineInfo')
    self["by_line"] = " ".join(prod_byline_div.text.strip().split())
    ##for author_span in prod_byline_div.find_all('span',class_='author'):
    landing_img = soup.find('img', id='landingImage')
    if landing_img:
      self["landing_image_url"] = landing_img["src"]
    prod_desc_div = soup.find('div', id='productDescription')
    if prod_desc_div is not None:
      self["description"] = prod_desc_div.text.strip()
      desc_artist_ids = []
      for a in prod_desc_div.find_all('a'):
        href = a["href"]
        if m := re.match(r'/exec/obidos/ts/artist-glance/(?P<artist_id>\d+)',
                         href):
          desc_artist_ids.append(int(m.group("artist_id")))
      self["description_arists"] = desc_artist_ids
    breadcrumbs_div = soup.find('div', id='breadcrumbs_feature_div')
    if breadcrumbs_div:
      breadcrumbs = []
      for li in child_tags(breadcrumbs_div.ul, 'li'):
        breadcrumbs.append(
            dict(
                label=li.text.strip(),
                href=li.span.a["href"],
            )
        )
      self["breadcrumbs"] = breadcrumbs
    details_div = soup.find('div', id='detailBullets_feature_div')
    if details_div:
      details = {}
      for li in child_tags(details_div.ul, 'li'):
        print("Details LI", li)
        try:
          key_span, value = child_tags(li.span)
        except ValueError:
          print("skip details", li.text)
        else:
          key = key_span.text.strip().split("\n")[0].lower()
          details[key] = value.text.strip()
          print("DETAILS", repr(key), '->', repr(details[key]))
      self["details"] = details
    print("GROK GENERIC")
    printt(*map(list, sorted(self.items())), indent="  ")

class ASIN(_AmazonEntity):
  ''' A class for Amazon entities with an ASIN.
      We always set the `'asin'` key to be the `type_key i.e. the ASIN.

      This sophistry is because you can't recognise an Amazon URL
      with an ASIN in it as a specific type of thing (book, author,
      series, whatever), so instead we recognise them as a side
      effect of creating them from references when we scan a page.

      The flip side of this is that `AmazonSite.from_URL` looks up
      entities by ASIN, not by `.name`.
  '''

  TYPE_SUBNAME = 'asin'
  SITEPAGE_URL_PATTERN = '<*:pretext>/dp/<type_key><*:tracking>'
  ASIN_TYPES = (
      'author',
      'book',
      'book-series',
      'music',
  )

  def __init__(self, *a, **kw):
    super().__init__(*a, **kw)
    # TODO: sanity check the ASIN string per Amazon docs
    asin = self.type_key
    if not is_valid_asin(asin):
      raise ValueError(f'{self.type_key=} is not a valid ASIN')
    if (stored_asin := self.get('asin')) != asin:
      if stored_asin is not None:
        warning(
            f'{self.name}: chnage ["asin"] from {asin=} -> {self.type_key=}'
        )
        raise RuntimeError
      self['asin'] = asin

  def field_ref_type(self, field_name: str) -> str:
    ''' All the entity subtypes at amazon are `asin`.
    '''
    return self.TYPE_SUBNAME

  @uses_scandata
  @promote
  @typechecked
  def scan_sitepage(
      self, flowstate: FlowState, scandata: ScanData
  ) -> ScanData:
    with Pfx(f'{self.name}.scan_sitepage({flowstate.url.short})'):
      super().scan_sitepage(flowstate, scandata=scandata)
      data = scandata[self]
      title = data['title']
      title_data = self.parse_page_title(title)
      data.update(title_data)
      try:
        asin_type = self.asin_type
      except AttributeError:
        asin_type = None
        if ('print_length' in data
            or '-ebook/dp/' in data.get('sitepage_url', '')):
          asin_type = 'book'
        else:
          # try to guess from the page title
          asin_type = title_data.get('asin_type')
      if asin_type is not None:
        data['asin_type'] = asin_type
        subscan_name = f'scan_{asin_type.replace("-","_")}'
        try:
          subscan = getattr(self, subscan_name)
        except AttributeError as e:
          warning(f'no self.{subscan_name} method for {asin_type=}: {e}')
        else:
          trace(subscan)(flowstate, scandata=scandata)
      ##breakpoint()
      return scandata

  @uses_scandata
  def scan_author(
      self, flowstate: FlowState, *, scandata: ScanData
  ) -> ScanData:
    data = scandata[self]
    soup = flowstate.soup
    bio_div = soup.find(
        lambda tag: tag.name == 'div' and tag.attrs.get('id', '').
        startswith('author-biotile-')
    )
    if bio_div:
      data['bio'] = bio_div.get_text().strip()
    else:
      warning('did not find bio DIV with id #author-biotile-*')
    return scandata

  @uses_scandata
  def scan_book(self, flowstate: FlowState, *, scandata: ScanData) -> ScanData:
    data = scandata[self]
    soup = flowstate.soup
    landing_img = soup.find('img', id='landingImage')
    if not landing_img:
      warning("no IMG #landingImage")
    else:
      alt = landing_img.attrs.get('alt', str(landing_img))
      hires_url = landing_img.attrs.get('data-old-hires', '')
      if not hires_url:
        warning(f'{alt=}: no landing IMG data-old-hires')
      else:
        data['cover_url'] = hires_url
    byline_div = soup.find('div', id='bylineInfo')
    if byline_div is None:
      warning('no DIV #bylineInfo')
    else:
      data['author_id'] = author_ids = []
      for author_span in byline_div.find_all('span', class_='author'):
        fullname = author_span.get_text().strip()
        anchor = author_span.a
        href = anchor.attrs.get('href')
        if not href:
          warning(f'{fullname=}: no href')
        else:
          try:
            author_asin = asin_from_href(href, '/')
          except ValueError:
            warning(f'{fullname=}: {href=}: no ASIN')
            continue
          author_ids.append(author_asin)
          author_data = scandata[ASIN, author_asin]
          author_data['asin_type'] = 'author'
          author_data['fullname'] = fullname
          author_data['sitepage_url'] = href.split('?', 1)[0]
    desc_div = soup.find('div', id='bookDescription_feature_div')
    if desc_div is None:
      warning('no DIV #bookDescription_feature_div')
    else:
      content_div = desc_div.find(class_='a-expander-content')
      data['description'] = content_div.get_text().strip()
      data['description_html'] = str(content_div)
    series_info_div = soup.find('div', i='seriesInfoRow')
    if series_info_div is None:
      warning('no DIV #seriesInfoRow')
    else:
      series_image_div = series_info_div.find('div', id='seriesImageContainer')
      if series_image_div is None:
        warning('no DIV #seriesImageContainer')
      else:
        anchor = series_image_div.find('a')
        series_title = anchor.get_text().strip()
        href = anchor.attrs.get('href', '')
        if not href:
          warning(f'{series_title}: no href')
        else:
          try:
            series_asin = asin_from_href(href, '/')
          except ValueError:
            warning(f'{series_title=}: {href=}: no ASIN')
          else:
            data['series_id'] = series_asin
            series_data = scandata[ASIN, series_asin]
            series_data['asin_type'] = 'book-series'
            series_data['title'] = series_title
            series_data['sitepage_url'] = href.split('?', 1)[0]
        books_ol = series_info_div.find('ol')
        if books_ol:
          warning(f'{series_title}: no books OL')
        else:
          series_data['book_id'] = book_ids = []
          for book_n, book_li in enumerate(child_tags(books_ol, 'li'), 1):
            anchor = book_li.find('a', class_='a-link-child')
            subbook_title = anchor.get_text().strip()
            href = anchor.attrs.get('href')
            if not href:
              warning(f'{subbook_title=}: no href')
            else:
              try:
                subbook_asin = asin_from_href(href, '/')
              except ValueError:
                warning(f'{subbook_title=}: {href=}: no ASIN')
                continue
              book_ids.append(author_asin)
              subbook_data = scandata[ASIN, subbook_asin]
              subbook_data['asin_type'] = 'book'
              subbook_data['title'] = subbook_title
              subbook_data['series_id'] = series_asin
              subbook_data['series_number'] = book_n
              subbook_data['sitepage_url'] = href.split('?', 1)[0]
    return scandata

  @uses_scandata
  def scan_book_series(
      self, flowstate: FlowState, *, scandata: ScanData
  ) -> ScanData:
    data = scandata[self]
    series_title = data['title']
    soup = flowstate.soup
    header = soup.find('div', id='collectionHeaderContainer')
    if header is None:
      warning("no DIV #collectionHeaderContainer")
      series_title = None
    else:
      data['format'] = header.find('bds-book-format').attrs['format']
    by_lines = defaultdict(list)
    auth_div = soup.find('span', id='bylineContainer').parent
    assert auth_div.name == 'div'
    for link in auth_div.find_all('bds-link'):
      href = link.attrs.get('href', '')
      label = link.attrs.get('label', '')
      if not href:
        warning('no href in {link}')
        continue
      try:
        by_asin = asin_from_href(href, marker='/')
      except ValueError:
        warning(f'no ASIN found for bds-link with {href=}')
        continue
      if m := re.search(r'\s*\(([^)]+)\)$', label):
        role = m.group(1).lower()
        label = label[:m.start()]
      else:
        warning(f'no "(role)" in {label=}, pretending author')
        role = 'author'
      by_lines[role].append((by_asin, label.strip(), href))
    for role, members in by_lines.items():
      data[f'{role}_id'] = [member[0] for member in members]
      for asin, fullname, href in members:
        ent = self.sitemap[ASIN, asin]
        scandata[ent]['asin_type'] = role
        scandata[ent]['fullname'] = fullname
        scandata[ent]['sitepage_url'] = href.split('?', 1)[0]
    # series items
    data['book_id'] = item_ids = []
    for item_number, item_div in enumerate(soup.find_all(
        lambda tag: (tag.name == "div" and tag.attrs.get("id", '').startswith(
            'series-childAsin-item_')),), 1):
      item_title_anchor = item_div.find('a', class_='itemBookTitle')
      item_title = prune_book_title(
          item_title_anchor.get_text().strip(), series_title
      )
      href = item_title_anchor.attrs['href']
      item_asin = asin_from_href(href, '/')
      item_ids.append(item_asin)
      item_ent = self.sitemap[ASIN, item_asin]
      item_data = scandata[item_ent]
      item_data['asin_type'] = 'book'
      item_data['sitepage_url'] = href.split('?', 1)[0]
      item_data['title'] = item_title
      item_data['author_id'] = data['author_id']
      item_data['series_id'] = self.asin
      item_data['series_number'] = item_number
      expander_div = item_div.find(
          lambda tag: tag.name == 'div' and tag.attrs.
          get('data-a-expander-name', '').startswith('itemDescripton_')
      )
      if expander_div is None:
        warning("no expander_div")
      else:
        desc_div = expander_div.find('div', class_='collectionDescription')
        if desc_div is None:
          warning(f'no div#collectionDescription in {expander_div}')
        else:
          item_data['description_html'] = str(desc_div)
    return scandata

  @classmethod
  def parse_page_title(cls, title: str) -> dict:
    ''' Parse information from an Amazon page title.
        The returned `dict`, if not empty, should usually have an
        `"asin_type"` entry and other associated data.
    '''
    data = {}
    print(f'{title=}')
    # Series Name (n book series) Kindle Eition
    elif m := re.match(r'\s*(.*\S)\s+\((\d+) book series\) Kindle Edition',
                       title):
      data['asin_type'] = 'book-series'
      data['title'] = m.group(1)
      data['series_length'] = int(m.group(2))
    # book title (series name N)
    elif m := re.match(r'\s*(\S.*\S)\s+\((\S.*\S)\s+(\d+)\)', title):
      data['asin_type'] = 'book'
      data['title'] = m.group(1)
      data['series_name'] = m.group(2)
      data['series_number'] = int(m.group(3))
    else:
      parts = title.rsplit(': ', 2)
      # Amazon.com.au: Author Name: books, biography, latest update
      if parts[-1].startswith('books, biography'):
        if not parts[0].lower().startswith('amazon.'):
          warning(f'{title=}: {parts=}: parts[0] is not amazon.*')
        data['asin_type'] = 'author'
        data['fullname'] = parts[1].strip()
      # "book-title: amazon: books"
      elif parts[-1].lower() == "books":
        if not parts[1].lower().startswith('amazon.'):
          warning(f'{title=}: {parts=}: parts[1] is not amazon.*')
        data['asin_type'] = 'book'
        data['title'] = prune_book_title(parts[0])
      else:
        warning(f'unhandled title: {parts=}')
        breakpoint()
      # normalise the asin_type
      if not data:
        warning(f'no asin_type inferred from {title=}')
      else:
        asin_type = data['asin_type'].lower()
        asin_type = {'books': 'book'}.get(asin_type, asin_type)
        data['asin_type'] = asin_type
    return data

  def refresh_related(self):
    asin_type = getattr(self, 'asin_type', None)
    if asin_type == 'author':
      yield from self.book_ents
      yield from self.series_ents
    elif asin_type == 'book':
      pass
    elif asin_type == 'book-series':
      yield from self.book_ents
    else:
      warning(f'{self.name}.refresh_related: unhandled {asin_type=}')

  def refresh_related1(self):
    asin_type = getattr(self, 'asin_type', None)
    if asin_type == 'author':
      pass
    elif asin_type == 'book':
      yield from self.author_ents
      yield from self.series_ents
    elif asin_type == 'book-series':
      yield from self.author_ents
    else:
      warning(f'{self.name}.refresh_related: unhandled {asin_type=}')



@dataclass
class AmazonMap(SiteMap):

  EntityClass = _AmazonEntity
  TYPE_ZONE = 'amazon'
  WIDGET_CLASSES = []
  URL_DOMAIN = 'www.amazon.com.au'

  URL_KEY_PATTERNS = [
      # m.media-amazon.com images/P/B07B2KLYCF.01._SY200_SX200_TTXW__SCLZZZZZZZ_.jpg
      (
          (
              'm.media-amazon.com',
              r'/images/',
          ),
          '{_}',
      ),
      # images-fe.ssl-images-amazon.com
      (
          (
              'images-fe.ssl-images-amazon.com',
              r'/images/',
          ),
          '{_}',
      ),
  ]

  @on(
      ##URL_DOMAIN,
      AmazonGeneralProduct,
  )
  # a comic volume URL
  # https://www.amazon.com/MS-MARVEL-VOL-NORMAL-Graphic/dp/078519021X/ref=.......
  @on(
      ##URL_DOMAIN,
      AmazonDigitalProduct,
  )
  ##@trace
  @grok_entity_page(ent_class=AmazonGeneralProduct)
  def grok_product_page(self, flowstate: FlowState, match, entity):
    pass

  # an author page
  # https://www.amazon.com/stores/G.-Willow-Wilson/author/B003JLY7S8?.......
  @on(
      ## URL_DOMAIN,
      AmazonAuthor,
  )
  @trace
  @grok_entity_page(ent_class=AmazonAuthor)
  def grok_author_page(self, flowstate: FlowState, match, entity):
    pass

########################################################################
# Widgets have to come after the sitemap.
#

@dataclass
class ItemWidget(SiteWidget, entity_class=_AmazonEntity):
  FIND_ALL_CRITERIA = dict(class_='product-card')

  @property
  def entity_key(self):
    ##print("entity_key", self.tag)
    ##breakpoint()
    return int(self.tag['id'].removeprefix('card'))

  def grok(self):
    ''' Update the title from the card.
    '''
    for a in self.tag.find_all('a'):
      if (title := a.get('aria-label')) and title != self.entity.get('title'):
        self.entity['title'] = title

@dataclass
class SeriesInfoRow(SiteWidget, entity_class=AmazonSeries):
  ''' The Series Information DIV.
  '''

  ENTITY_CLASS = AmazonSeries
  FIND_ALL_CRITERIA = dict(id='seriesInfoRow')

  @cached_property
  def entity_key(self):
    ''' Obtain the series ASIN from the `href`.
    '''
    image_div = self.tag.find('div', id='seriesImageContainer')
    return asin_from_href(image_div.a.attrs['href'])

  @trace
  @uses_scandata
  @typechecked
  def scan_soup(self, *, scandata: ScanData) -> ScanData:
    ''' Scan the series info DIV.
        This extracts:
        - the series ASIN and title
        - the series author ASINs and fullnames
        - the member books ASINs and titles
    '''
    ent = self.entity
    series_asin = ent.type_key
    data = scandata[ent]
    image_div = self.tag.find('div', id='seriesImageContainer')
    # scan the series members
    series_label_span = image_div.find(
        'span', **{'data-test-id': 'seriesImageLabel'}
    )
    data['title'] = series_label_span.string.strip()
    carousel_div = self.tag.find('div', id='seriesCarousel')
    if carousel_div is None:
      print("DID NOT FIND carousel_div")
      breakpoint()
    book_asins = []
    author_asins = set()
    for pos, li in enumerate(child_tags(carousel_div.find('ol'), 'li'), 1):
      title_span = li.find('span', **{'data-test-id': 'itemTitle'})
      anchor = title_span.parent
      book_asin = asin_from_href(anchor.attrs['href'])
      book_asins.append(book_asin)
      book = ent.sitemap[AmazonBook, book_asin]
      book_data = scandata[book]
      book_data['title'] = prune_book_title(
          title_span.string.strip(), series=data['title']
      )
      author_anchor = li.find('a', **{'data-test-id': 'itemByLine'})
      author_asin = asin_from_href(author_anchor.attrs['href'], '/e/')
      book_data['author_id'] = author_asin
      book_data['series_id'] = series_asin
      book_data['series_position'] = pos
      author_asins.add(author_asin)
      author = ent.sitemap[AmazonAuthor, author_asin]
      author_data = scandata[author]
      author_data['fullname'] = author_anchor.string.strip()
    data['book_id'] = book_asins
    data['author_id'] = sorted(author_asins)
    return scandata

@dataclass
class ProductDetails(SiteWidget, entity_class=AmazonBook):
  ''' The Product Details DIV.
  '''

  ENTITY_CLASS = AmazonBook
  FIND_ALL_CRITERIA = dict(id='detailBullets_feature_div')

  @cached_property
  def entity_key(self):
    ''' Obtain the product ASIN from the ASIN detail entry.
    '''
    parsed = self.parsed
    try:
      return parsed['asin']
    except KeyError as e:
      warning(
          f'{self.__class__.__name__}.entity_key: no ASIN in {self.parsed=}: {e}'
      )
      printt(parsed)
      breakpoint()
      raise

  @cached_property
  def parsed(self):
    parsed = {}
    try:
      ul = self.tag.ul
    except AttributeError as e:
      warning(f'no .ul? {e}')
      printt_soup(self.tag)
      breakpoint()
      raise RuntimeError
      return parsed
    for li in child_tags(ul, 'li'):
      li_span, = li.children
      assert li_span.name == 'span'
      if ''.join(li_span.strings).strip().startswith(('Best Sellers Rank:',)):
        continue
      subtags = list(
          child for child in li_span.children if not isinstance(child, str)
      )
      try:
        key_span, value_tag = subtags
      except ValueError as e:
        tag_summary = ",".join(
            f'{tag.__class__.__name__}<{tag.name}>' for tag in subtags
        )
        warning(f'LI SPAN: expected 2 children, got {tag_summary}: {e}')
        printt_soup(li)
        breakpoint()
        continue
      key = key_span.string.strip(AMAZON_WS +
                                  ':').lower().replace(' ',
                                                       '_').replace('-', '_')
      if '__' in key:
        print(f'{key=}')
        print("key_span:")
        printt_soup(key_span)
        breakpoint()
        raise RuntimeError
      if value_tag.name == 'a':
        value_span, = value_tag.children
        value_href = value_tag.attrs.get('href')
        if value_href in ('', '#'):
          value_href = None
      elif value_tag.name == 'span':
        value_span = value_tag
        value_href = None
      else:
        if value_tag.name == 'div' and value_tag.attrs.get(
            'id') == 'detailBullets_averageCustomerReviews':
          # skip avegare customer reviews
          continue
        warning("unhandled value tag:")
        printt_soup(value_tag)
        continue
      value = ''.join(value_span.strings).strip()
      if key.endswith('_date'):
        try:
          value = date_from_pubdate(value)
        except ValueError as e:
          warning(f'unhandled {key_span.string} {value=}: {e}')
      parsed[key] = value
    return parsed

  @uses_scandata
  @typechecked
  def scan(self, *, scandata: ScanData) -> ScanData:
    ''' Scan the series info DIV.
        This extracts:
        - the series ASIN and title
        - the series author ASINs and fullnames
        - the member books ASINs and titles
    '''
    ent = self.entity
    data = scandata[ent]
    data.update(self.parsed)
    return scandata

@dataclass
class MusicTracks:  ## ABC ## (SiteWidget, entity_class=_AmazonEntity):
  ''' The Product Details DIV.
  '''

  ENTITY_CLASS = AmazonMusic
  FIND_ALL_CRITERIA = dict(id='detailBullets_feature_div')

  def grok_sitepage(self, flowstate: FlowState):
    self.generic_grok_amazon_page(flowstate)
    soup = flowstate.soup
    music_tracks_div = soup.find('div', id='music-tracks')
    if music_tracks_div:
      table, = Table.scan(music_tracks_div)
      print("MUSIC TRACKS:")
      table.printt()
      self["music_tracks"] = [track[1] for track in grid]

class _AmazonPrimeEntity(SiteEntity):
  TYPE_ZONE = 'prime'

class AmazonPrime(SiteMap):
  EntityClass = _AmazonPrimeEntity

class APCard(Widget):
  ''' A card from a carousel.
  '''

  @classmethod
  def find_all(cls, soup):
    ''' Find `article` tags with `data-testid="card"`.
    '''
    return soup.find_all('article', **{'data-testid': 'card'})

  @cached_property
  def title(self):
    ''' The card title.
    '''
    return self.tag.attrs['data-card-title']

  @cached_property
  def type(self):
    ''' The card type, eg "Movie" or "TV Show".
    '''
    return self.tag.attrs['data-card-entity-type']

  def entitlement(self):
    ''' The entitlement.
    '''
    return self.tag.attrs['data-card-entitlement']

  def image_url(self):
    ''' The URL of the default card image.
    '''
    return self.tag.find('img', **{'data-testid': 'base-image'}).src

class APCarousel(Widget):
  ''' A carousel row.
  '''

  @classmethod
  def find_all(cls, soup):
    ''' Find `section` tags with `data-testid="standard-carousel"`.
    '''
    return soup.find_all('section', **{'data-testid': 'standard-carousel'})

  @cached_property
  def title(self):
    return self.tag.h2.get_text()

  @cached_property
  def cards(self) -> list[APCard]:
    ''' The cards in the carousel.
    '''
    return APCard.scan(self.tag)

  #####################################################################
  # sequence methods
  def __len__(self):
    return len(self.cards)

  def __iter__(self):
    return iter(self.cards)

  def __getitem__(self, index):
    return self.cards[index]
