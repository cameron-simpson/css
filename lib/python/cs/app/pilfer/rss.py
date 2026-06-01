#!/usr/bin/env python3

''' RSS XML.
'''

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Iterable
from xml.etree.ElementTree import ElementTree

from lxml.builder import ElementMaker

from cs.lex import r
from cs.seq import not_none

class RSSCommon(ABC):
  ''' Common methods for RSS channel and item entities.
  '''

  @staticmethod
  def ElementMaker():
    ''' Return an `lxml.builder.ElementMaker` instance for making RSS XML.
    '''
    return ElementMaker(
        ##namespace=?,
        nsmap=dict(
            content="http://purl.org/rss/1.0/modules/content/",
            dc="http://purl.org/dc/elements/1.1/",
            atom="http://www.w3.org/2005/Atom",
            sy="http://purl.org/rss/1.0/modules/syndication/",
            slash="http://purl.org/rss/1.0/modules/slash/",
            webfeeds="http://webfeeds.org/rss/1.0",
        ),
    )

  def rss_category(self):
    return getattr(self, 'category', None)

  @staticmethod
  def rss_date_string(dt: float | str | date | datetime):
    ''' Return a timestamp (a UNIX time or a timezone aware `datetime`)
        as an RFC822 date and time with a 4 digit year.

        RSS dates and times: https://www.rssboard.org/rss-profile#data-types-datetime
        RFC822 date and time specification: https://datatracker.ietf.org/doc/html/rfc822#section-5
    '''
    if not isinstance(dt, (date, datetime)):
      if isinstance(dt, float):
        dt = datetime.fromtimestamp(dt, tz=timezone.utc)
      elif isinstance(dt, str):
        try:
          dt = datetime.fromisoformat(dt)
        except ValueError:
          dt = datetime.strptime("%a, %d %b %Y %H:%M:%S %z")
      else:
        raise TypeError(f'cannot convert {r(dt)} to a datetime')
    return dt.strftime("%a, %d %b %Y %H:%M:%S %z")

  def rss_pubdate(self) -> None | str:
    ''' Return the publication date, or `None` if not available.
    '''
    return None

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
  def rss_items(self) -> Iterable["RSSChannelItemMixin"]:
    raise NotImplementedError

  def rss(
      self,
      *,
      E=None,
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
      refresh=False,
  ):
    ''' Return the RSS for this entity as an `lxml rss Element`.
        It can be converted to text with `ElementTree.tostring()`.

        Optional parameters:
        * `E`: optional `ElementMaker` instance; the default comes from `RSSCommon.ElementMaker()`
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
        * `refresh`: optional flag, default `False`; if true call `self.refresh()`
        * `title`: the channel title, default from `self.rss_title()`
    '''
    if E is None:
      E = self.ElementMaker()
    if refresh:
      self.refresh()
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
            *(
                item.rss_item(refresh=refresh, E=E)
                for item in (items or self.rss_items())
            ),
        ),
        version="2.0",
    )
    return rss

class RSSChannelItemMixin(RSSCommon, ABC):

  def rss_item(
      self,
      *,
      E=None,
      category=None,
      description=None,
      image_url=None,
      image_size=None,
      image_title=None,
      language=None,
      link=None,
      pub_date=None,
      title=None,
      refresh=False,
  ):
    ''' Return the RSS for this entity as an `lxml item Element`.
        It can be converted to text with `ElementTree.tostring()`.

        Optional parameters:
        * `E`: optional `ElementMaker` instance; the default comes from `RSSCommon.ElementMaker()`
        * `category`: the item category, default from `self.rss_category()`
        * `description`: the item description, default from `self.rss_description()`
        * `image_url`: an optional URL for an image for this item
        * `image_size`: optional size information for the image as a `(width,height)` 2-tuple
        * `image_title`: an optional title associate with the image,
          default from `self.rss-image_title()`
        * `language`: the channel title, default from `self.rss_language()`
        * `link`: the channel title, default from `self.rss_link()`
        * `refresh`: optiona flag, default `False`; if true call `self.refresh()`
        * `title`: the channel title, default from `self.rss_title()`
    '''
    if E is None:
      E = self.ElementMaker()
    if refresh:
      self.refresh()
    if category is None: category = self.rss_category()
    if category is None:
      categories = ()
    elif isinstance(category, str):
      categories = category,
    else:
      categories = list(category)
    if description is None: description = self.rss_description()
    if image_url is None:
      image_url = self.rss_image_url()
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
    if pub_date is None: pub_date = self.rss_pubdate()
    if title is None: title = self.rss_title()
    rss = E.item(
        *not_none(
            (
                E.guid(self.name, isPermaLink="false"),
                E.title(title),
                description and E.description(description),
                E.link(link),
                *(E.category(cat) for cat in categories),
                pub_date and E.pubDate(self.rss_date_string(pub_date)),
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
