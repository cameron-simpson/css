#!/usr/bin/env python3

''' Class mixins to support generating feeds in RSS (and soon Atom) formats.
'''

from abc import ABC, abstractmethod
from datetime import date, datetime, timezone
from functools import cached_property
from io import TextIOBase
from os.path import splitext
import sys
from typing import final, Iterable, Literal, Sequence

from bs4.element import Tag as BS4Tag
from lxml.builder import ElementMaker
from lxml.etree import tostring as xml_tostring

from cs.bs4utils import as_xml as bs4_as_xml
from cs.fileutils import atomic_filename
from cs.gimmicks import warning
from cs.lex import html_escape
from cs.obj import NoAttrs, Refreshable
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

  def for_atom(self, tag: str, *, E):
    ''' Return a `tag` Element for this `FeedPerson`.
    '''
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
    ''' Produce a `FeedPerson` from a `SiteEntity`.
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

  # an `lxml.builder.ElementMaker` instance for making Atom XML
  @cached_property
  def ATOM_MAKER(self):
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

  # an `lxml.builder.ElementMaker` instance for making RSS XML
  #cached_property
  @cached_property
  def RSS_MAKER(self):
    return ElementMaker(
        ##namespace=?,
        nsmap=dict(
            content="http://purl.org/rss/1.0/modules/content/",
            dc="http://purl.org/dc/elements/1.1/",
            atom=ATOM_NS,
            sy="http://purl.org/rss/1.0/modules/syndication/",
            slash="http://purl.org/rss/1.0/modules/slash/",
            webfeeds="http://webfeeds.org/rss/1.0",
            media="http://search.yahoo.com/mrss/",
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
      # missing atom_field looks for feed_field
      value = getattr(self, f'feed_{attr[5:]}')
    elif attr.startswith('rss_'):
      # missing rss_field looks for feed_field
      value = getattr(self, f'feed_{attr[4:]}')
    elif attr.startswith('feed_'):
      # missing feed_field looks for .field, but returns None if missing
      value = lambda refresh=True: getattr(self, attr[5:], None)
    else:
      value = super().__getattr__(attr)
    self._feed_refreshed(value,)
    return value

  @staticmethod
  def _feed_refreshed(obj, refresh=True):
    ''' Refresh `obj` is it is `Refreshable`, unless `refresh` is false (default `True`).
        Return `obj`.
    '''
    if refresh and isinstance(obj, Refreshable):
      obj.refresh()
    return obj

  def _feed_kwv(
      self, field: str, synd: Literal["atom", "rss"], kw, *, refresh: bool
  ):
    ''' Return the value of `field` from `kw` or via `.{synd}_{field}()`.
        `synd` may be one of `"atom"` or `"rss"` as required.

        If the value is `Refreshable` and `refresh` is true, call
        `value.refresh()` before return.

        This is used in the feed and entry `.atom()` and `.rss()` methods.
    '''
    try:
      value = kw.pop(field)
    except:
      method_name = f'{synd}_{field}'
      method = getattr(self, method_name)
      value = method()
    self._feed_refreshed(value, refresh=refresh)
    return value

  def _feed_image_info(self, v, kw):
    ''' Return a `(image_url,image_width,image_height,image_title,image_link)` 4-tuple.
    '''
    image_url = v('image_url')
    image_size = v('image_size')
    image_title = v('image_title')
    image_link = v('image_link')
    if image_url:
      if image_size:
        image_width, image_height = image_size
      else:
        image_width = getattr(self, 'opengraph.image:width', None)
        if image_width: image_width = int(image_width)
        image_height = getattr(self, 'opengraph.image:height', None)
        if image_height: image_height = int(image_height)
    else:
      image_width, image_height = None, None
    return image_url, image_width, image_height, image_title, image_link

  @final
  @staticmethod
  def atom_date_string(dt: float | str | date | datetime):
    ''' Return a timestamp (a UNIX time or a timezone aware `datetime`)
        as an RFC3339 date and time with a 4 digit year.

        Atom date constructs: https://www.rfc-editor.org/info/rfc4287/#section-3.3
        RFC3339 Internet Date/Time Format: https://www.rfc-editor.org/info/rfc3339/#section-5.6
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

  def feed_categories(self) -> Sequence[str]:
    ''' Obtain the categories from `self.category` if present, default `()`.
        If `self.category` is a string, return it in a 1-tuple.
    '''
    categories = getattr(self, 'category', None) or ()
    if isinstance(categories, str):
      categories = (categories,)
    return categories

  def feed_generator(self):
    ''' The default generator string.
    '''
    return f'{self.__class__.__module__}:{self.__class__.__name__}'

  @final
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

  def rss_author(self):
    ''' RSS presents the author email in its `author` tag.
    '''
    author = self.feed_author()
    return None if author is None else getattr(author, 'email', None)

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
      raise TypeError(f'unhandled type {type(text)} for {text=}')
    return E(name, content, type=type)

class FeedMixin(FeedCommon, ABC):
  '''" The Atom/Rss feed top level.
  '''

  def feed_save(
      self,
      file: TextIOBase | str,
      xml,
      *,
      exists_ok: bool = False,
      xmlv: bool = False,
      **gen_kw
  ):
    ''' Save a feed to `file`.

        Parameters:
        * `file`: the save file, a TextIOBase or a filename `str`
        * `xml`: the feed XML to save
        * `exists_ok`: default `False`; it is an error if this is false and `file` already exists
        * `xmlv`: default `False`; if true then prepend the XML version string to the saved XML

        If `file` is a `str`, the value `"-"` saves to `sys.stdout`
        and other strings are treated as filesystem paths, written
        to using `atomic_filename`.

        If `xml` is a callable it is called with any other keyword
        arguments to generated the XML to save, which return an XML
        top level feed `Element` or a `str` containing the XML text.
        If not a `str`, the XML is converted to a string using
        `lxml.etree.tostring(xml,encoding='unicode',pretty_print=True)`.

        The convenience `atom_save` and `rss_save` methods call
        `feed_save` with `xml=slf.atom` or `xml=self.rss` respectively.
    '''
    if isinstance(file, str):
      if file == "-":
        return self.atom_save(sys.stdout, xmlv=xmlv, **gen_kw)
      with atomic_filename(file, mode='w', exists_ok=exists_ok) as T:
        return self.atom_save(T, xmlv=xmlv, **gen_kw)
    if callable(xml):
      xml = xml(**gen_kw)
    else:
      assert not gen_kw
    if not isinstance(xml, str):
      xml = xml_tostring(xml, encoding='unicode', pretty_print=True)
    if xmlv:
      print('<?xml version="1.0" encoding="UTF-8"?>', file=T)
    print(xml, end='', file=file)

  def atom_save(self, file: TextIOBase | str, **feed_save_kw):
    ''' Generate and save an Atom feed to `file`.
        Keyword arguments are passed through to `self.feed_save`.
    '''
    return self.feed_save(file, xml=self.atom, **feed_save_kw)

  def rss_save(self, file: TextIOBase | str, **feed_save_kw):
    ''' Generate and save an RSS feed to `file`.
        Keyword arguments are passed through to `self.feed_save`.
    '''
    return self.feed_save(file, xml=self.rss, **feed_save_kw)

  def atom(
      self,
      *,
      refresh=False,
      **kw,
  ):
    ''' Return the Atom `feed` for this entity as an `lxml feed Element`.
        It can be converted to text with `ElementTree.tostring()`.

        Parameters:
        * `refresh`: optional flag, default `False`; if true call `self.refresh()`

        Other keyword arguments are cnsumed to generate the `feed` XML.
    '''
    E = self.ATOM_MAKER
    v = lambda field: self._feed_kwv(field, "atom", kw, refresh=refresh)
    if refresh: self.refresh()
    authors = self.atom_authors(refresh=refresh)
    title = v('title')
    link = v('link')
    atom = E.feed(
        *not_none(
            (
                # atomCommonAttributes,
                title and E.title(title),
                # subtitle
                E.generator(v('generator')),
                E.updated(
                    self.atom_date_string(self.atom_last_build_timestamp())
                ),
                *(author.for_atom(E=E) for author in (authors or ())),
                link and E.link(link),
                # icon - from the favicon
                # logo
                # rights
                *(
                    entry.atom(feed=self, refresh=refresh)
                    for entry in v('entries')
                ),
                # extensionElement
            )
        ),
        xmlns=ATOM_NS,
    )
    if kw:
      warning(f'{self!r}.atom: unused keyword arguments: {kw!r}')
    return atom

  def rss(
      self,
      *,
      build_timestamp=None,
      refresh=False,
      **kw,
  ):
    ''' Return the RSS for this entity as an `lxml rss Element`.
        It can be converted to text with `ElementTree.tostring()`.

        Optional parameters:
        * `build_timestamp`: a UNIX timestamp for `lastBuildDate`,
          default from `self.rss_last_build_timestamp()`
          which is help in the `timestamp.rss_content` tag
        * `image_size`: optional size information for the image as a `(width,height)` 2-tuple
        * `refresh`: optional flag, default `False`; if true call `self.refresh()`
        Other keyword parameters override the defaults for various RSS tags.
    '''
    if refresh:
      self.refresh()
    E = self.RSS_MAKER
    v = lambda field: self._feed_kwv(field, "rss", kw, refresh=refresh)
    if build_timestamp is None:
      build_timestamp = self.rss_last_build_timestamp()
    categories = v('categories') or ()
    description = v('description')
    image_url, image_width, image_height, image_title, image_link = self._feed_image_info(
        v, kw
    )
    language = v('language')
    link = v('link')
    title = v('title') or repr(self)
    rss = E.rss(
        E.channel(
            E.title(title),
            E.link(link),
            E.description(description),
            E.generator(v('generator')),
            E.lastBuildDate(self.rss_date_string(build_timestamp)),
            E.docs('https://www.rssboard.org/rss-specification'),
            *not_none(
                (
                    E.title(title),
                    E.link(link),
                    description and E.description(description),
                    E.generator(v('generator')),
                    E.lastBuildDate(self.rss_date_string(build_timestamp)),
                    E.docs('https://www.rssboard.org/rss-specification'),
                    *not_none(
                        (
                            language and E.language(language),
                            *map(E.category, categories),
                            image_url and E.image(
                                *not_none(
                                    E.url(image_url),
                                    image_title and E.title(image_title),
                                    E.link(link),
                                    image_width and E.width(str(image_width)),
                                    image_height
                                    and E.height(str(image_height)),
                                )
                            ),
                        )
                    ),
                    *(
                        item.rss(feed=self, refresh=refresh)
                        for item in v('entries')
                    ),
                )
            )
        ),
        version="2.0",
    )
    if kw:
      warning(f'{self!r}.atom: unused keyword arguments: {kw!r}')
    return rss

class FeedEntryMixin(FeedCommon, ABC):

  @abstractmethod
  def feed_entry_signature(self, *, refresh=False, **refresh_kw):
    ''' Return a signature value used to update the
    '''
    raise NotImplementedError

  def atom(
      self,
      *,
      feed: FeedMixin | None = None,
      image_size=None,
      refresh=False,
      **kw,
  ):
    ''' Return the Atom `entry` for this entry as an `lxml entry Element`.
        It can be converted to text with `ElementTree.tostring()`.

        Optional parameters:
        * `refresh`: optiona flag, default `False`; if true call `self.refresh()`
        Other keyword parameters override the defaults for various Atom tags.
    '''
    if refresh:
      self.refresh()
    E = self.ATOM_MAKER
    v = lambda field: self._feed_kwv(field, "atom", kw, refresh=refresh)
    author = v('author') or feed.atom_author()
    categories = v('categories') or ()
    description = v('description')
    image_url, image_width, image_height, image_title, image_link = self._feed_image_info(
        v, kw
    )
    link = v('link')
    pub_date = v('pub_date')
    atom = E.entry(
        *not_none(
            (
                feed_id and E.id(feed_id, isPermaLink="false"),
                title and E.title(title),
                author and author.for_atom('author', E=E),
                link and E.link(link),
                *map(E.category, categories),
                pub_date and E.pubDate(self.atom_date_string(pub_date)),
                image_url and E.image(
                    *not_none(
                        E.url(image_url),
                        image_title and E.title(image_title),
                        E.link(link),
                        image_width and E.width(str(image_width)),
                        image_height and E.height(str(image_height)),
                    )
                ),
                description and E.description(description),
            ),
        ),
    )
    if kw:
      warning(f'{self!r}.atom: unused keyword arguments: {kw!r}')
    return atom

  def rss(
      self,
      *,
      feed: FeedMixin | None = None,
      image_size=None,
      refresh=False,
      **kw,
  ):
    ''' Return the RSS for this entry as an `lxml item Element`.
        It can be converted to text with `ElementTree.tostring()`.

        Optional parameters:
        * `feed`: optional `FeedMixin` requesting this entry RSS;
          can be a source of fields this entry may lack
        * `refresh`: optiona flag, default `False`; if true call `self.refresh()`
        Other keyword parameters override the defaults for various RSS tags.
    '''
    if refresh:
      self.refresh()
    E = self.RSS_MAKER
    v = lambda field: self._feed_kwv(field, "rss", kw, refresh=refresh)
    author = v('author') or (feed and feed.rss_author())
    categories = v('categories') or ()
    creator = v('creator')
    description = v('description')
    image_url, image_width, image_height, image_title, image_link = self._feed_image_info(
        v, kw
    )
    image_title = v('image_title')
    if image_url:
      image_ext = splitext(image_url)[1][1:].lower()
      image_content_type = 'image/' + {
          'jpg': 'jpeg',
      }.get(image_ext, image_ext)
    link = v('link')
    pub_date = v('pub_date')
    title = v('title')
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
                    *not_none(
                        (
                            E.url(image_url),
                            image_title and E.title(image_title),
                            E.link(link),
                            image_width and E.width(str(image_width)),
                            image_height and E.height(str(image_height)),
                        )
                    )
                ),
                image_url and E.enclosure(
                    type=image_content_type,
                    url=image_url,
                ),
                description and E.description(description),
                ## TODO namespaced tags? https://lxml.de/tutorial.html#namespaces
                ## image_url and E('media:thumbnail', url=image_url),
            ),
        ),
    )
    if kw:
      warning(f'{self!r}.atom: unused keyword arguments: {kw!r}')
    return rss
