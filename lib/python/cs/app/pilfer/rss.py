#!/usr/bin/env python3

''' RSS XML.
'''

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from xml.etree.ElementTree import ElementTree

from lxml.builder import E

from cs.seq import not_none

@dataclass(kw_only=True)
class RSS:
  ''' A class for generating an RSS feed.

      Based on information from https://www.rssboard.org/rss-specification.
  '''
  title: str
  link: str
  description: str
  # W3C language codes:
  # https://www.w3.org/TR/REC-html40/struct/dirlang.html#langcodes
  language: str = None
  copyright: str = None
  # email of the editor
  managingEditor: str = None
  # email of the webmaster
  webmaster: str = None
  # using a UNIX timestamp
  pubDate: float
  # last time the content of the channel changed, using a UNIX timestamp
  lastBuildDate: float = None
  category: list[str] = field(default_factory=list)

  def __init__(self):
    self.tree = ElementTree()

  def xml(self):
    raise NotImplementedError

  @staticmethod
  def date_time_s(dt: float | datetime):
    ''' Return a timestamp (a UNIX time or a timezone aware `datetime`)
        as an RFC822 date and time with a 4 digit year.

        RSS dates and times: https://www.rssboard.org/rss-profile#data-types-datetime
        RFC822 date and time specification: https://datatracker.ietf.org/doc/html/rfc822#section-5
    '''
    if not isinstance(dt, datetime):
      dt = datetime.fromtimestamp(dt, tz=timezone.utc)
    return dt.strftime("%a, %d %b %Y %H:%M:%S %z")

  @staticmethod
  def encoded_plain_text(s: str) -> str:
    ''' Return the text `s` with the characters `&`, `<` and `>`
        replaced by hex escapes.
        See: https://www.rssboard.org/rss-profile#data-types-characterdata
    '''
    return s.replace('&', '&#x26;').replace('<',
                                            '&#x2C').replace('>', '&#x3E;')

class RSSCommon(ABC):
  ''' Common methods for RSS channel and item entities.
  '''

  def rss_category(self):
    return getattr(self, 'category', None)

  @staticmethod
  def rss_date_string(timestamp):
    ''' Return the UNIX `timestamp` as an RFC822 date time string.
    '''
    return datetime.fromtimestamp(timestamp
                                  ).strftime('%a, %d %b %Y %H:%M:%S %Z')

  def rss_description(self):
    return getattr(self, 'description', '')

  def rss_title(self):
    return self.title

  def rss_image_url(self):
    return self.get('opengraph.image')

  def rss_image_title(self):
    return self.rss_title()

  def rss_link(self):
    return self.sitepage_url

  def rss_language(self):
    og_locale = self.get('opengraph.locale')
    if not og_locale:
      return None
    return og_locale.lower().replace('_', '-')

class RSSChannelMixin(RSSCommon, ABC):
  '''" The RSS top level.
  '''

  def rss_content_signature(self):
    ''' Return an object which should change if the content changes.
        This default base method returns `sorted(self[self.RSS_ITEM_KEYS])`.
    '''
    return sorted(self[self.RSS_ITEM_KEYS])

  def rss_last_build_timestamp(self):
    return self.update_content_timestamp(
        'rss_content', self.rss_content_signature()
    )

  @abstractmethod
  def rss_items(self):
    raise NotImplementedError

  def rss(
      self,
      build_timestamp=None,
      category=None,
      description=None,
      generator=None,
      image_url=None,
      image_size=None,
      language=None,
      link=None,
      title=None,
      items=None,
  ):
    ''' Return the RSS for this entity as an `lxml rss Element`.
        It can be converted to text with `ElementTree.tostring()`.

        Optional parameters:
        * `build_timestamp`: a UNIX timestamp for `lastBuildDate`,
          default from `self.rss_last_build_timestamp()`
          which is help in the `timestamp.rss_content` tag
        * `category`: the item category, default from `self.rss_category()`
        * `description`: the channel title, default from `self.rss_description()`
        * `generator`: the name of the RSS generator, default from the `Pilfer` package name
        * `image_url`: an optional URL for an image for this channel
        * `image_size`: optional size information for the image as a `(width,height)` 2-tuple
        * `language`: the channel title, default from `self.rss_language()`
        * `link`: the channel title, default from `self.rss_link()`
        * `title`: the channel title, default from `self.rss_title()`
    '''
    if category is None: category = self.rss_category()
    if category is None:
      categories = ()
    elif isinstance(category, str):
      categories = category,
    else:
      categories = list(category)
    if description is None: description = self.rss_description()
    if generator is None:
      generator = self.sitemap.pilfer.__class__.__module__.rsplit('.', 1)[0]
    if image_url is None: image_url = self.rss_image_url()
    if image_size:
      image_width, image_height = image_size
    else:
      image_width = self.get('opengraph.image:width')
      if image_width: image_width = int(image_width)
      image_height = self.get('opengraph.image:height')
      if image_height: image_height = int(image_height)
    if image_width and image_height: image_size = image_width, image_height
    if link is None: link = self.rss_link()
    if title is None: title = self.rss_title()
    rss = E.rss(
        E.channel(
            E.title(title),
            E.link(link),
            E.description(description),
            E.generator(generator),
            E.lastBuildDate(
                self.rss_date_string(self.rss_last_build_timestamp())
            ),
            E.docs('https://www.rssboard.org/rss-specification'),
            *not_none(
                (
                    language and E.language(language),
                    category and E.category(category),
                    image_url and E.image(
                        E.url(image_url),
                        E.link(self.rss_link()),
                        ##E.width(str(topic['opengraph.image:width'])),
                        ##E.height(str(topic['opengraph.image:height'])),
                    ),
                )
            ),
            ##E( 'atom:link', href="https://www.rssboard.org/files/sample-rss-2.xml", rel="self", type="application/rss+xml"),
            *(item.rss_item() for item in (items or self.rss_items())),
        ),
        version="2.0",
        **{
            ##"xmlns:content": "http://purl.org/rss/1.0/modules/content/",
            ##"xmlns:dc": "http://purl.org/dc/elements/1.1/",
            ##"xmlns:atom": "http://www.w3.org/2005/Atom",
            ##"xmlns:sy": "http://purl.org/rss/1.0/modules/syndication/",
            ##"xmlns:slash": "http://purl.org/rss/1.0/modules/slash/",
            ##"xmlns:webfeeds": "http://webfeeds.org/rss/1.0",
        },
    )
    return rss

class RSSChannelItemMixin(RSSCommon, ABC):

  def rss_item(
      self,
      description=None,
      image_url=None,
      image_size=None,
      image_title=None,
      language=None,
      link=None,
      title=None,
      category=None,
  ):
    ''' Return the RSS for this entity as an `lxml item Element`.
        It can be converted to text with `ElementTree.tostring()`.

        Optional parameters:
        * `category`: the item category, default from `self.rss_category()`
        * `description`: the item description, default from `self.rss_description()`
        * `image_url`: an optional URL for an image for this item
        * `image_size`: optional size information for the image as a `(width,height)` 2-tuple
        * `image_title`: an optional title associate with the image,
          default from `self.rss-image_title()`
        * `language`: the channel title, default from `self.rss_language()`
        * `link`: the channel title, default from `self.rss_link()`
        * `title`: the channel title, default from `self.rss_title()`
    '''
    if category is None:
      category = self.rss_category()
    if category is None:
      categories = ()
    elif isinstance(category, str):
      categories = category,
    else:
      categories = list(category)
    if description is None: description = self.rss_description()
    if image_size:
      image_width, image_height = image_size
    else:
      image_width = self.get('opengraph.image:width')
      if image_width: image_width = int(image_width)
      image_height = self.get('opengraph.image:height')
      if image_height: image_height = int(image_height)
    if image_width and image_height: image_size = image_width, image_height
    if image_title is None: image_title = self.rss_image_title()
    if link is None: link = self.rss_link()
    if title is None: title = self.rss_title()
    if category is None: category = self.rss_category()
    rss = E.item(
        *not_none(
            (
                E.guid(self.name, isPermaLink="false"),
                E.title(title),
                description and E.description(description),
                E.link(link),
                *(E.category(cat) for cat in categories),
                image_url and E.image(
                    E.url(image_url),
                    E.title(image_title),
                    E.link(self.rss_link()),
                    image_width and E.width(str(image_width)),
                    image_height and E.height(str(image_height)),
                ),
            ),
        ),
    )
    return rss
