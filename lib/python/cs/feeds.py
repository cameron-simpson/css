#!/usr/bin/env python3

''' Class mixins to support generating feeds in RSS (and soon Atom) formats.
'''

from abc import ABC, abstractmethod
from datetime import date, datetime, timezone
from types import SimpleNamespace as NS
from typing import Iterable, Sequence

from bs4.element import Tag as BS4Tag
from lxml.builder import ElementMaker

from cs.bs4utils import as_xml as bs4_as_xml
from cs.lex import html_escape
from cs.seq import get0, not_none

ATOM_CONTENT_TYPE = 'application/atom+xml'
ATOM_NS = 'http://www.w3.org/2005/Atom'
RSS_CONTENT_TYPE = 'application/rss+xml'

class FeedPerson:
  ''' A class to represent a person, modelled on an Atom person
      construct which has a `.name`, a `.email` (may be `None`
      and a `.uri` (may be `None`).
  '''

  def __init__(self, name, *, uri=None, email=None):
    self.name = name
    self.uri = uri
    self.email = email

  def for_atom(self, tag: str, *, E=None):
    ''' Return a `tag` Element for this `FeedPerson`.
    '''
    if E is None: E = FeedCommon.AtomElementMaker()
    return E(
        tag,
        *(
            E(tag, value)
            for tag, value in self.__dict__.items()
            if value is not None
        ),
    )

  @classmethod
  def from_SiteEntity(cls, ent, *, refresh=False):
    ''' Produce an `FeedPerson` from a `SiteEntity`.
    '''
    if refresh: ent.refresh()
    try:
      person = cls(
          name=ent.fullname,
          email=getattr(ent, 'eemail', None),
          uri=getattr(ent, 'sitepage_url', None)
      )
    except AttributeError as e:
      print(f'MISSING ATTRIBUTE {type(ent)}. {e}')
      raise
    return person

class FeedCommon(ABC):
  ''' Common methods for for feeds, supporting RSS channel and items
      and soon Atom feeds and entries.

      The `.atom()` method will return an Atom XML element (feed or entry)
      following [the Atom Format RFC4287](https://www.rfc-editor.org/info/rfc4287/).

      The `.rss()` method returns an RSS XML element (channel or item)
      following [the RSS 2.0 Specification](https://www.rssboard.org/rss-specification).

      As such the core implementation expects generic methdos named `feed_*`,
      with format specific methods named `atom_*` an `rss_*`.

      All `feed_*`, `atom_*` or `rss_*` attributes return callables
      i.e. they all act as methods, not properties.

      Missing `atom_*` or `rss_*` methods fall back to their `feed_*`
      names.

      Missing `feed_*` methods fall back to `getattr(self,suffix,None)`
      where `suffix` is the name after `feed_`. The `getattr`
      fallback allows the main class to provide attributes to support
      these; for example, for `ZonedType` subclasses such as `Entity`
      or `SiteEntity`, this tries first the `suffix` then
      `{zone}.{suffix}`.
  '''

  @staticmethod
  def AtomElementMaker():
    ''' Return an `lxml.builder.ElementMaker` instance for making Atom XML.
    '''
    return ElementMaker(
        ##namespace=?,
        nsmap=dict(
            content="http://purl.org/rss/1.0/modules/content/",
            dc="http://purl.org/dc/elements/1.1/",
            atom=ATOM_NS,
            sy="http://purl.org/rss/1.0/modules/syndication/",
            slash="http://purl.org/rss/1.0/modules/slash/",
            webfeeds="http://webfeeds.org/rss/1.0",
        ),
    )

  @staticmethod
  def RSSElementMaker():
    ''' Return an `lxml.builder.ElementMaker` instance for making RSS XML.
    '''
    return ElementMaker(
        ##namespace=?,
        nsmap=dict(
            content="http://purl.org/rss/1.0/modules/content/",
            dc="http://purl.org/dc/elements/1.1/",
            atom=ATOM_NS,
            sy="http://purl.org/rss/1.0/modules/syndication/",
            slash="http://purl.org/rss/1.0/modules/slash/",
            webfeeds="http://webfeeds.org/rss/1.0",
        ),
    )

  def __getattr__(self, attr: str):
    ''' Definition of various uniimplemented methods.

        All `feed_*`, `atom_*` or `rss_*` attributes return callables.

        Missing `atom_*` or `rss_*` attributes fall back to the
        common `feed_*` attributes.

        Missing `feed_*` attributes return callables accessing the
        `.suffix` attribute (where `suffix` is the name after
        `feed_`). For example, for `ZonedType` subclasses such as
        `Entity` or `SiteEntity`, this tries first the `suffix` then
        `{zone}.{suffix}`.
    '''
    if attr.startswith('atom_'):
      return getattr(self, f'feed_{attr[5:]}')
    if attr.startswith('rss_'):
      return getattr(self, f'feed_{attr[4:]}')
    if attr.startswith('feed_'):
      return lambda: getattr(self, attr[5:])
    return super().__getattr__(attr)

  def feed_authors(self, *, refresh=False) -> Sequence[FeedPerson]:
    ''' Return a list of `FeedPerson`s.

        This default implementation assumes that `self` looks like
        ` cs.tagset.Entity` and that `self.author_ents` produces
        an iterable of objects which look like `SiteEntity` instances,
        which have a `.fullname` attribute and which may have
        `.email` and `.sitepage_url` attributes.
    '''
    return [
        FeedPerson.from_SiteEntity(ent, refresh=refresh)
        for ent in self.author_ents
    ]

  def feed_author(self, *, refresh=False) -> FeedPerson | None:
    return get0(self.feed_authors(refresh=refresh))

  @staticmethod
  def atom_date_string(dt: float | str | date | datetime):
    ''' Return a timestamp (a UNIX time or a timezone aware `datetime`)
        as an RFC3339 date and time with a 4 digit year.

        Atom date constructs: https://www.rfc-editor.org/info/rfc4287/#section-3.3
    '''
    # turn dt into a UTC datetime
    if isinstance(dt, datetime):
      dt = dt.astimezone(tz=timezone.utc)
    elif isinstance(dt, date):
      # pretend the date is a UTC date
      # TODO: I would to assume a localtime date (for no very good reason)
      # but that seems... surprisingly hard to do.
      dt = datetime(dt.year, dt.month, dt.day, tz=timezone.utc)
    elif isinstance(dt, float):
      dt = datetime.fromtimestamp(dt, tz=timezone.utc)
    elif isinstance(dt, str):
      try:
        dt = datetime.fromisoformat(dt)
      except ValueError:
        dt = datetime.strptime("%a, %d %b %Y %H:%M:%S %z")
      dt = dt.astimezone(tz=timezone.utc)
    else:
      raise TypeError(
          f'cannot convert {type(dt).__name__}:{dt!r} to a datetime'
      )
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

  def feed_category(self) -> str:
    return getattr(self, 'category', None)

  def feed_link(self):
    return self.sitepage_url

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
        raise TypeError(
            f'cannot convert {type(dt).__name__}:{dt!r} to a datetime'
        )
    return dt.strftime("%a, %d %b %Y %H:%M:%S %z")

  def feed_pubdate(self) -> None | str:
    ''' Return the publication date, or `None` if not available.
    '''
    return None

  def rss_author(self, refresh=False):
    ''' RSS presents the author email in its `author` tag.
    '''
    return self.feed_author(refreh=refresh).email

  def feed_description(self):
    return getattr(self, 'description', '')

  def feed_image_url(self):
    return self.get('opengraph.image')

  def feed_image_title(self):
    return self.feed_title()

  def feed_language(self):
    og_locale = self.get('opengraph.locale')
    if not og_locale:
      return None
    return og_locale.lower().replace('_', '-')

  @classmethod
  # TODO: XML element support
  def atom_text(cls, name, text, type=None, *, E=None):
    ''' Return an XML Element with tag name `name` containing `text`,
        which should be a string or a BeautifulSoup `Tag` or an XML Element.

        This is to produce Atom text constructs:
        https://www.rfc-editor.org/info/rfc4287/#section-3.1

        Parameters:
        * `name`: the tag name
        * `text`: the text to enclose
        * `type`: the type of the enclosed text

        The default `text` type is inferred from the type of `text`:
        * a string: `"text"`
        * a BeautifulSoup `Tag`: `"html"`
        * an XML element: `"xhtml"`
    '''
    if E is None:
      E = cls.AtomElementMaker()
    if type is None:
      if isinstance(text, BS4Tag):
        type = "html"
        content = html_escape(str(text))
      elif isinstance(text, str):
        type = "text"
        content = html_escape(str(text))
      else:
        type = "xhtml"
        content = E.div(
            text,
            xmlns="http://www.w3.org/1999/xhtml",
        )
    elif isinstance(text, BS4Tag):
      if type == "text":
        content = html_escape(text.get_text())
      elif type == "html":
        content = html_escape(str(text))
      elif type == "xhtml":
        content = E.div(
            bs4_as_xml(text),
            xmlns="http://www.w3.org/1999/xhtml",
        )
      else:
        raise ValueError(f'unsupported {type=}')
    elif isinstance(text, str):
      content = html_escape(str(text))
    else:
      print("type(text) =", type(text))
    return E(name, content, type=type)

class FeedMixin(FeedCommon, ABC):
  '''" The RSS top level.
  '''

  def feed_last_build_timestamp(self, *, refresh=False, **refresh_kw) -> float:
    ''' Return an updated timestamp for this feed based on the signatures of its entries.
    '''
    return self.update_timestamp(
        'feed_content',
        [
            entry.feed_entry_signature(refresh=refresh, **refresh_kw)
            for entry in self.feed_entries()
        ],
    )

  @abstractmethod
  def feed_entries(self) -> Iterable["FeedEntryMixin"]:
    raise NotImplementedError

  def atom(
      self,
      *,
      E=None,
      entries=None,
      generator=None,
      refresh=False,
      title=None,
  ):
    ''' Return the Atom `feed` for this entity as an `lxml feed Element`.
        It can be converted to text with `ElementTree.tostring()`.

        Optional parameters:
        * `E`: optional `ElementMaker` instance; the default comes from `FeedCommon.RSSElementMaker()`
        * `generator`: the name of the RSS generator, default from `self.__class__`
        * `refresh`: optional flag, default `False`; if true call `self.refresh()`
        * `title`: the channel title, default from `self.rss_title()`
    '''
    if E is None: E = self.RSSElementMaker()
    if refresh: self.refresh()
    if generator is None:
      generator = f'{self.__class__.__module__}:{self.__class__.__name__}'
    if title is None:
      try:
        title = self.rss_title()
      except AttributeError as e:
        from cs.logutils import warning
        warning(f'{self.name=}.rss_title: {e}')
        breakpoint()
        raise
    atom = E.feed(
        E.title(title),
        E.generator(generator),
        E.updated(self.atom_date_string(self.feed_last_build_timestamp())),
        *(
            author.for_atom(E=E)
            for author in self.feed_authors(refresh=refresh)
        ),
        *(
            entry.atom(refresh=refresh, E=E)
            for entry in (entries or self.feed_entries())
        ),
        xmlns=ATOM_NS,
    )
    return atom

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
        * `E`: optional `ElementMaker` instance; the default comes from `FeedCommon.RSSElementMaker()`
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
      E = self.RSSElementMaker()
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
      generator = f'{self.__class__.__module__}:{self.__class__.__name__}'
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
            *(
                item.rss_item(refresh=refresh, E=E)
                for item in (items or self.feed_entries())
            ),
        ),
        version="2.0",
    )
    return rss

class FeedEntryMixin(FeedCommon, ABC):

  def rss_item(

  def atom(
      self,
      *,
      E=None,
      author=None,
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
    ''' Return the Atom `entry` for this entry as an `lxml entry Element`.
        It can be converted to text with `ElementTree.tostring()`.

        Optional parameters:
        * `E`: optional `ElementMaker` instance; the default comes from `FeedCommon.AtomElementMaker()`
        * `refresh`: optiona flag, default `False`; if true call `self.refresh()`
        * `title`: the entry title, default from `self.atom_title()`
    '''
    if E is None:
      E = self.AtomElementMaker()
    if refresh:
      self.refresh()
    if author is None: author = self.atom_author(refresh=refresh)
    if category is None: category = self.atom_category()
    if category is None:
      categories = ()
    elif isinstance(category, str):
      categories = category,
    else:
      categories = list(category)
    if description is None: description = self.atom_description()
    if image_url is None:
      image_url = self.atom_image_url()
    if image_size:
      image_width, image_height = image_size
    else:
      image_width = self.get('opengraph.image:width')
      if image_width: image_width = int(image_width)
      image_height = self.get('opengraph.image:height')
      if image_height: image_height = int(image_height)
    if image_width and image_height: image_size = image_width, image_height
    if image_title is None: image_title = self.atom_image_title()
    if link is None: link = self.atom_link()
    if pub_date is None: pub_date = self.atom_pubdate()
    if title is None: title = self.atom_title()
    atom = E.entry(
        *not_none(
            (
                E.guid(self.name, isPermaLink="false"),
                E.title(title),
                author and author.for_atom('author', E=E),
                E.link(link),
                *map(E.category, categories),
                pub_date and E.pubDate(self.atom_date_string(pub_date)),
                image_url and E.image(
                    E.url(image_url),
                    E.title(image_title),
                    E.link(link),
                    image_width and E.width(str(image_width)),
                    image_height and E.height(str(image_height)),
                ),
                description and E.description(description),
            ),
        ),
    )
    return atom
      self,
      *,
      E=None,
      author=None,
      category=None,
      creator=None,
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
        * `E`: optional `ElementMaker` instance; the default comes from `FeedCommon.RSSElementMaker()`
        * `author`: the email address of the author
        * `category`: the item category, default from `self.rss_category()`
        * `description`: the item description, default from `self.rss_description()`
        * `image_url`: an optional URL for an image for this item
        * `image_size`: optional size information for the image as a `(width,height)` 2-tuple
        * `image_title`: an optional title associate with the image,
          default from `self.rss-image_title()`
        * `language`: the channel title, default from `self.rss_language()`
        * `link`: the URL of the item, default from `self.rss_link()`
        * `refresh`: optiona flag, default `False`; if true call `self.refresh()`
        * `title`: the channel title, default from `self.rss_title()`
    '''
    if E is None:
      E = self.RSSElementMaker()
    if refresh:
      self.refresh()
    if author is None: author = self.rss_author()
    if category is None: category = self.rss_category()
    if category is None:
      categories = ()
    elif isinstance(category, str):
      categories = category,
    else:
      categories = list(category)
    if creator is None: creator = self.rss_creator()
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
                author and E.author(author),
                creator and E.creator(creator),
                E.link(link),
                *map(E.category, categories),
                pub_date and E.pubDate(self.rss_date_string(pub_date)),
                image_url and E.image(
                    E.url(image_url),
                    E.title(image_title),
                    E.link(link),
                    image_width and E.width(str(image_width)),
                    image_height and E.height(str(image_height)),
                ),
                description and E.description(description),
            ),
        ),
    )
    return rss
