#!/usr/bin/env python3

from dataclasses import dataclass
from datetime import date, datetime
from functools import cached_property
import re

from typeguard import typechecked

from cs.app.pilfer.sitemap import (
    FlowState, SiteEntity, SiteMap, SiteWidget, URLMatcher, uses_scandata,
    grok_entity_page, on
)
from cs.bs4utils import child_tags, printt_soup
from cs.deco import promote
from cs.bs4utils import child_tags, table_grid
from cs.lex import printt
from cs.logutils import warning
from cs.tagset import ScanData
from cs.urlutils import URL

from cs.debug import trace, r, pprint, printt

# worth parsing:
# https://www.amazon.com.au/SANNO-Stainless-Organizer-Freezers-Adjustable/dp/B0DBZMGRYR?th=1

# Unicode points found in Amazon whitespace areas
AMAZON_WS = ' \t\r\n\N{LEFT-TO-RIGHT MARK}\N{RIGHT-TO-LEFT MARK}'

def asin_from_href(href, marker='/dp/'):
  ''' Return the ASIN from an `href` like ....`/dp/`*ASIN*...
      The `/dp/` (digital product) marker may be overridden by
      the `marker` parameter.
      Raises `valueError` if the `href` is not matched.
  '''
  assert marker.endswith('/'), f'expected {marker=} to end with a slash'
  regexp_s = marker + r'([^/?]+)'
  if m := re.search(regexp_s, href):
    return m.group(1)
  raise ValueError(f'{href=} does not look like {marker=}ASIN')

def date_from_pubdate(pubdate: str) -> date:
  ''' Parse an Amazon publication date text into a `datetime.date`.
  '''
  # date.strptime only arrived in 3.14
  return datetime.strptime(pubdate.strip(), '%d %B %Y').date()

def prune_book_title(title, series=None):
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
  if m := re.search(r'\s+\(book \S+\)$', title, re.I):
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

class AmazonAuthor(_AmazonEntity):
  ''' An author.
  '''
  TYPE_SUBNAME = 'author'
  SITEPAGE_URL_PATTERN = '.*/author/<type_key>'

  def grok_sitepage(self, flowstate: FlowState):
    self.generic_grok_amazon_page(flowstate)
    soup = flowstate.soup
    print("MISSING AUTHOR GROK CODE")

class AmazonDigitalProduct(_AmazonEntity):
  ''' An Amazon item, which has an ASIN.
  '''
  TYPE_SUBNAME = 'item'
  SITEPAGE_URL_PATTERN = '<*:pretext>/dp/<type_key><*:tracking>'

  def grok_sitepage(self, flowstate: FlowState):
    self.generic_grok_amazon_page(flowstate)

class AmazonSeries(_AmazonEntity):
  ''' A book series.
  '''
  TYPE_SUBNAME = 'series'

class AmazonGeneralProduct(_AmazonEntity):
  ''' A single Amazon product.
  '''
  TYPE_SUBNAME = 'product'
  SITEPAGE_URL_PATTERN = '<*:pretext>/gp/product/<type_key><*:tracking>'

@dataclass
class AmazonMap(SiteMap):

  EntitiesClass = _AmazonEntity
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
  ENTITY_CLASS = AmazonDigitalProduct
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
class SeriesInfoRow(SiteWidget, entity_class=_AmazonEntity):
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
  def scan(self, *, scandata: ScanData) -> ScanData:
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
      book = ent.sitemap[AmazonDigitalProduct, book_asin]
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
class ProductDetails(SiteWidget, entity_class=_AmazonEntity):
  ''' The Product Details DIV.
  '''

  ENTITY_CLASS = AmazonDigitalProduct
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

  @trace
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

  ENTITY_CLASS = AmazonDigitalProduct
  FIND_ALL_CRITERIA = dict(id='detailBullets_feature_div')

  def grok_sitepage(self, flowstate: FlowState):
    self.generic_grok_amazon_page(flowstate)
    soup = flowstate.soup
    music_tracks_div = soup.find('div', id='music-tracks')
    if music_tracks_div:
      grid = trace(table_grid, retval=True)(music_tracks_div.find('table'))
      print("MUSIC TRACKS:")
      printt(*grid)
      self["music_tracks"] = [track[1] for track in grid]
