#!/usr/bin/env python3

''' Base class for site maps.
'''

from collections import ChainMap, defaultdict, namedtuple
from dataclasses import dataclass
from datetime import datetime
from fnmatch import fnmatch
from functools import cached_property
from getopt import GetoptError
from itertools import zip_longest
from os.path import abspath
import re
from threading import Thread
import time
from types import SimpleNamespace as NS
from typing import Any, Callable, Iterable, Mapping, Optional

from cs.binary import bs
from cs.cmdutils import popopts, qvprint, vprint
from cs.deco import (
    decorator, default_params, fmtdoc, OBSOLETE, promote, Promotable,
    uses_verbose, with_
)
from cs.fileutils import atomic_filename
from cs.lex import (
    cutprefix, cutsuffix, FormatableMixin, FormatAsError, get_nonwhite, lc_,
    printt, r, s, skipwhite
)
from cs.logutils import warning
from cs.mappings import mapped_property
from cs.obj import public_subclasses
from cs.pfx import Pfx, pfx, pfx_call, pfx_method
from cs.py.func import funccite
from cs.progress import progressbar
from cs.queues import IterableQueue, ListQueue
from cs.resources import MultiOpenMixin, RunState, uses_runstate
from cs.rfc2616 import (
    content_encodings, content_length, content_type, datetime_from_http_date
)
from cs.seq import ClonedIterator
from cs.tagset import BaseTagSets, HasTags, TagSet, TagSetTyping, UsesTagSets
from cs.threads import HasThreadState, ThreadState
from cs.units import BINARY_BYTES_SCALE
from cs.urlutils import URL

from bs4 import BeautifulSoup
from bs4.element import Tag as BS4Tag
from mitmproxy.flow import Flow
import requests
from typeguard import typechecked

# The default HTML parser to use with BeautifulSoup.
BS4_PARSER_DEFAULT = 'lxml'  # vs eg 'html5lib'

debug_fs_counts = False

def default_Pilfer():
  ''' Obtain the ambient `Pilfer` instance via a late import.
  '''
  from .pilfer import Pilfer
  P = Pilfer.default()
  if P is None:
    P = Pilfer()
  return P

@decorator
def uses_pilfer(func):
  ''' Set the optional `P:Pilfer` parameter via a late import.
  '''

  def func_with_Pilfer(
      *a,
      P: "Pilfer" = None,  # noqa: F821
      **kw
  ):
    if P is None:
      P = default_Pilfer()
    with P:
      return func(*a, P=P, **kw)

  return func_with_Pilfer

@decorator
def pagemethod(method):
  ''' A decorator for `SiteEntity` methods whose names end in
      `_`*nom*`page` to provide a default URL for the flowstate
      parameter from `self.`*nom*`page_url`.

      Note that the optional `flowstate` parameter must be the first
      positional parameter after `self`.

      This allows method definitions like:

          @pagemethod
          def grok_sitepage(self, flowstate: FlowState):

      so that the method can be called directly on the entity
      without providing a URL.

      This decorator also applies the `@promote` decorator.
  '''
  _, pagename = method.__name__.split('_', 1)
  if not pagename.endswith('page') or len(pagename) == 4:
    raise ValueError(
        f'cannot derive default page attribute name from {method.__name__=},'
        ' which should end in _*nom*page, such as grok_sitepage'
    )
  promoting_method = promote(method)

  def with_flowstate(self, flowstate: Optional[FlowState] = None, *a, **kw):
    if flowstate is None:
      flowstate = getattr(self, f'{pagename}_url')
    return promoting_method(self, flowstate, *a, **kw)

  return with_flowstate

def parse_img_srcset(srcset, offset=0) -> Mapping[str, list[str]]:
  ''' Parse an `IMG` tag `srcset` attribute into a mapping of URL to conditions.
  '''
  mapping = defaultdict(list)
  offset = skipwhite(srcset, offset)
  while offset < len(srcset):
    url, offset = get_nonwhite(srcset, offset)
    offset = skipwhite(srcset, offset)
    try:
      condition, srcset = srcset[offset:].split(',', 1)
      offset = skipwhite(srcset)
    except ValueError:
      condition = srcset[offset:].rstrip()
      offset = len(srcset)

    mapping[url].append(condition)
  return mapping

@dataclass
class URLPattern(Promotable):
  ''' A class for matching a `URL` against a `(hostname_fnmatch,url_regexp)` pair.
  '''

  path_pattern: str
  hostname_fnmatch: str | None = None

  class Converter:

    @typechecked
    def __init__(
        self,
        match_re: str | re.Pattern,
        from_str: Callable[str, Any],
        to_str: Callable[Any, str],
    ):
      if isinstance(match_re, str):
        match_re = re.compile(match_re)
      self.match_re = match_re
      self.from_str = from_str
      self.to_str = to_str

    def __repr__(self):
      return f'{self.__class__.__qualname__}({self.match_re},{self.from_str}->{self.to_str})'

  # converter specifications, a mapping of name -> (re,convert,deconvert)
  CONVERTERS = {
      # the default stops at / or &
      '': Converter(r'[^/?&]+', str, str),
      # a nonnegative integer
      'int': Converter(r'0|[1-9]\d*', int, str),
      'lc_': Converter(r'[^/&A-Z]+', str, lc_),
      'wordpath': Converter(r'\w+(/\w+)*', str, str),
  }

  class ParsedPattern(namedtuple('ParsedPattern',
                                 'pattern parts placeholders'), Promotable):

    # a <converter:name> placeholder
    PLACEHOLDER_re = re.compile(
        r'<((?P<converter>[a-z][a-z0-9_]*):)?(?P<name>[a-z][a-z0-9_]*)>'
    )

    @classmethod
    def from_str(cls, pattern: str, converters=None):
      ''' Parse `pattern` as a `weukzeug`-like pattern, but simpler.
          Return a `ParsedPattern` where `.parts` is a
          list of the pattern components and `.placeholders` is a
          mapping of placeholder name to a `URLPattern.Converter`
          instance.
      '''
      if converters is None:
        converters = URLPattern.CONVERTERS
      parts = []
      placeholders = {}
      offset = 0
      while offset < len(pattern):
        with Pfx("offset %d", offset):
          if pattern.startswith('<', offset):
            m = cls.PLACEHOLDER_re.match(pattern, offset)
            if m is None:
              raise ValueError(
                  f'expected placeholder at {offset=}, found {pattern[offset:]!r}'
              )
            matched = m.groupdict()
            name = matched['name']
            converter_name = m.group('converter') or ''
            converter = converters[converter_name]
            if name in placeholders:
              raise ValueError(f'repeated definition of <{name=}>')
            placeholders[name] = converter
            parts.append((name, converter))
            offset = m.end()
          else:
            nextpos = pattern.find('<', offset)
            if nextpos == -1:
              nextpos = len(pattern)
            else:
              assert nextpos > offset
            parts.append(pattern[offset:nextpos])
            offset = nextpos
      return cls(pattern=pattern, parts=parts, placeholders=placeholders)

  def __post_init__(self):
    ''' Parse the pattern immediately for validation purposes.
    '''
    self._parsed = self.ParsedPattern.from_str(self.path_pattern)

  @classmethod
  def from_str(cls, pattern: str):
    if pattern.isupper():
      raise ValueError(
          f'cannot promote {pattern!r} to URLPattern, looks like an HTTP METHOD name'
      )
    if '/' in pattern:
      if not pattern.startswith('/'):
        pattern = f'/<*:preamble>{pattern}'
      hostname_fnmatch = None
      path_pattern = pattern
    else:
      hostname_fnmatch = pattern
      path_pattern = None
    return cls(hostname_fnmatch, path_pattern)

  @cached_property
  def pattern_re(self):
    ''' The compiled regular expression from `self._parts`.
    '''
    re_s = ''.join(
        (
            re.escape(part) if isinstance(part, str) else
            f'(?P<{part[0]}>{part[1].match_re.pattern})'
        ) for part in self._parsed.parts
    )
    return pfx_call(re.compile, re_s)

  def url_path_for(self, fields: Mapping[str, Any]):
    ''' Return the URL path derived from `fields`, a mapping from
        placeholder names to values which might typically be a `SiteEntity`.
    '''
    subpaths = []
    for part in self._parsed.parts:
      ##print("url_path_for: part =", part)
      if isinstance(part, str):
        subpaths.append(part)
      else:
        name, converter = part
        value = fields[name]
        value_s = converter.to_str(value)
        try:
          vaule2 = converter.from_str(value_s)
        except ValueError as e:
          warning(
              "url_path_for: fields[%r]=%s does not round trip via %s: %s",
              name, r(value), s(converter), e
          )
        subpaths.append(value_s)
    return ''.join(subpaths)

  @promote
  def match(
      self,
      url: URL,
      extra: Mapping | None = None,
  ) -> dict | None:
    ''' Compare `url` against this pattern.
        Return `None` on no match.
        Return the regexp `groupdict()` on a match.
    '''
    if self.hostname_fnmatch is not None and (
        not isinstance(url.hostname, str)
        or not fnmatch(url.hostname, self.hostname_fnmatch)):
      # hostname mismatch
      return None
    if self.path_pattern is None:
      # no pattern, accept and return empty match dict
      return {}
    qpath = url.path
    if url.query:
      qpath = f'{qpath}?{url.query}'
    # first try /path?query
    m = self.pattern_re.match(qpath)
    if m is None and qpath != url.path:
      # otherwise try /path
      m = self.pattern_re.match(url.path)
    if m is None:
      return None
    if m.end() < len(url.path):
      return None
    return m.groupdict()

  @classmethod
  def promote(cls, obj):
    ''' Promote `obj` to `URLPattern`:
        - `SiteEntity` subclass: use `obj.pattern()`
        - str:UPPERCASE: rejected, should be a method test
        - str:no-slashes: hostname_fnmatch
        - str:with-slashes: path-pattern
        - `re.Pattern` -> path regexp
        - `(hostname_fnmatch,path-pattern|url_regexp)` 2-tuples
    '''
    if isinstance(obj, cls):
      return obj
    if isinstance(obj, str):
      return cls.from_str(obj)
    if isinstance(obj, type) and issubclass(obj, SiteEntity):
      # a SiteEntity - use its URLPattern
      ptn = SiteEntity.pattern()
      if ptn is None:
        raise TypeError(
            f'cannot promote SiteEntity subclass {obj} to URLPattern: no SITEPAGE_URL_PATTERN?'
        )
      return ptn
    if isinstance(obj, re.Pattern):
      # regexp -> path regexp
      hostname_fnmatch, path_pattern = None, obj
    else:
      if isinstance(obj, str):
        return cls.from_str(obj)
      try:
        hostname_fnmatch, path_pattern = obj
      except (TypeError, ValueError):
        return super().promote(obj)
    # obj is a 2-tuple of (hostname_fnmatch,path_pattern)
    return cls(hostname_fnmatch=hostname_fnmatch, path_pattern=path_pattern)

@dataclass
class URLMatcher(Promotable):
  ''' A class for matching a `URL` against a `(hostname_fnmatch,url_regexp)` pair.
  '''

  hostname_fnmatch: str | None
  url_regexp: str

  @classmethod
  def from_str(cls, url_regexp):
    return cls(hostname_fnmatch=None, url_regexp=url_regexp)

  @classmethod
  def from_tuple(cls, spec):
    hostname_fnmatch, url_regexp = spec
    return cls(hostname_fnmatch=hostname_fnmatch, url_regexp=url_regexp)

  @cached_property
  def url_re(self):
    with Pfx('url_re( %s )', self.url_regexp):
      return re.compile(self.url_regexp)

  @promote
  def match(
      self,
      url: URL,
      extra: Mapping | None = None,
  ) -> dict | None:
    ''' Compare `url` against this matcher.
        Return `None` on no match.
        Return the regexp `groupdict()` on a match.
    '''
    if self.hostname_fnmatch is not None and not fnmatch(
        url.hostname, self.hostname_fnmatch):
      return None
    m = self.url_re.match(url.path)
    if m is None:
      return None
    return m.groupdict()

  @classmethod
  def promote(cls, obj):
    ''' Promote `obj` to `URLMatcher`:
        - `(hostname_fnmatch,url_regexp)` 2-tuples
        - `url_regexp` strings
    '''
    if isinstance(obj, cls):
      return obj
    try:
      hostname_fnmatch, url_regexp = obj
    except (TypeError, ValueError):
      return super().promote(obj)
    return cls(hostname_fnmatch=hostname_fnmatch, url_regexp=url_regexp)

class FlowState(NS, MultiOpenMixin, HasThreadState, FormatableMixin,
                Promotable):
  ''' An object with some resemblance to a `mitmproxy` `Flow` object
      with various utility properties and methods.
      It may be initialised from lesser objects such as just a URL.

      This is intended as a common basis for working in a `mitmproxy`
      flow or from outside via the `requests` package.

      Note that its `.request` and `.response` objects might be from `mitmproxy`
      or from `requests`, so they are mostly useful for their headers.

      However, the various `FlowState` attributes and properties
      are based around the `mitmproxy` `Response` attributes:
      https://docs.mitmproxy.org/stable/api/mitmproxy/http.html#Response
      In particular the `.content` and so forth are different from
      those of `requests.Response.content`.
  '''

  # class attribute holding the per-thread state stack
  perthread_state = ThreadState()

  # Shared count of the existing FlowState instance
  # raised by __init__, lowered by __del__.
  # Used to look for leaking FlowSTate references.
  nfs = 0

  @fmtdoc
  @promote
  def __init__(self, url: URL, **ns_kw):
    ''' Initialise `self` from the keyword parameters.

        Accepted parameters:
        - `bs4parser`: the desired BeautifulSoup parser,
          default from `{BS4_PARSER_DEFAULT==}`.
        - `flow`: a `mitmproxy` `Flow` instance
        - `request`: a `Request` instance
        - `response`: a `Response` instance
        - `url`; a URL

        The end result is that we have `.flow`, `.request`,
        `.response` and `.url` attributes, which may be `None`
        if omitted.
        Some of these have computed defaults if omitted:
        - `.request` and `.response` are obtained from `.flow`
        - `.url` is obtained from `.request.url`
    '''
    if debug_fs_counts:
      FlowState.nfs += 1
      vprint("FlowStates += 1 ->", FlowState.nfs)
    super().__init__(url=url, **ns_kw)
    extra_attrs = self.__dict__.keys() - (
        'bs4parser',
        'flow',
        'request',
        'response',
        'url',
    )
    if extra_attrs:
      raise ValueError(f'unexpected attributes supplied: {extra_attrs}')

  def __str__(self):
    attr_listing = ",".join(
        f'{attr}={value}' for attr, value in self.__dict__.items()
    )
    return f'{self.__class__.__name__}({attr_listing})'

  __repr__ = __str__

  def __enter_exit__(self):
    ''' Run both the inherited context managers.
    '''
    with self.session:
      for _ in zip_longest(
          MultiOpenMixin.__enter_exit__(self),
          HasThreadState.__enter_exit__(self) if self.default else (),
      ):
        yield

  def __del__(self):
    ''' Close `self.response` on delete.
    '''
    if debug_fs_counts:
      FlowState.nfs -= 1
      vprint("FlowStates -= 1 ->", FlowState.nfs)
    rsp = self.__dict__.get('response')
    if rsp is not None:
      try:
        rsp_close = rsp.close
      except AttributeError as e:
        warning("no .close for %s", r(rsp))
      else:
        rsp_close()

  # NB: no __getattr__, it preemptys @cached_property

  def as_URL(self) -> URL:
    ''' Return the `URL` from this `FlowState`, utilised by `@promote`.
    '''
    return self.url

  @classmethod
  def from_str(cls, url_s: str):
    ''' Promote a `str` URL to a `FlowState`.
    '''
    return cls(url=URL(url_s))

  @classmethod
  @promote
  def from_URL(cls, url: URL):
    ''' Promote a `URL` URL to a `FlowState`.
    '''
    return cls(url=url)

  @classmethod
  def from_Flow(cls, flow: Flow):
    ''' Promote a `Flow` to a `FlowState`.
    '''
    return cls(
        url=flow.request.url,
        flow=flow,
        request=flow.request,
        response=flow.response,
    )

  @classmethod
  @uses_pilfer
  @uses_runstate
  def iterable_flowstates(
      cls,
      flowstates: Iterable,
      P: "Pilfer",  # noqa: F821
      runstate: RunState,
      **later_map_kw,
  ):
    ''' A generator yielding `FlowState` instances with a ready
        `.iterable_content` attribute, promoted from `flowstates`.

        This prepares the `FlowState`s concurrently, performing a
        `GET` if necessary, yielding them in the supplied order.

        Parameters:
        * `flowstates`: an iterable, notionally of `FlowState`s but
          actually any object which can be promoted to a `FlowState`
          such as a URL
        * `P`: optional `Pilfer` if not the ambient one
        * `runstate`: optional `RunState` if not the ambient one
        Other keyword arguments are passed to `Later.map` to control
        how things are fulfilled.

        Example:

            # call self.grok_the_page on several page URLs
            pages = [ f'url-of-page-n' for n in range(1,count) ]
            for flowstate in FlowState.iterable_flowstates(*pages):
                # we know this page can be parsed immediately
                info = self.grok_the_page(flowstate)
                ... apply info ...
    '''

    @promote
    def get_iterable_fs(fs: "FlowState") -> "FlowState":
      ''' Promote `fs` to a `FlowState` and ready its `.iterable_content` attribute.
          This will do a `GET` if necessary.
      '''
      with P:
        fs.iterable_content  # ensure the content has been made available
      return fs

    with P:
      for flowstate in P.later.map(
          get_iterable_fs,
          flowstates,
          concurrent=True,
          **later_map_kw,
      ):
        yield flowstate
        runstate.raiseif()

  def clear(self, *attrs):
    ''' Delete the named attrubtes `attrs`.
        We do this to clear derived attributes when we set an
        antecedant attribute.
    '''
    assert len(attrs) > 0
    for attr in attrs:
      try:
        delattr(self, attr)
      except AttributeError:
        pass

  @cached_property
  @fmtdoc
  def bs4parser(self):
    ''' The beautifulSoup parser name.
        The default comes from `{BS4_PARSER_DEFAULT==}`.
    '''
    # TODO: envvar? Pilfer config setting?
    return BS4_PARSER_DEFAULT

  @cached_property
  def url(self) -> URL:
    ''' The URL, obtained from `self.response.url` if missing.
    '''
    return URL(self.response.url)

  @cached_property
  def method(self) -> str:
    ''' The uppercase form of the request method.
    '''
    try:
      rsp = self.request
    except AttributeError:
      return 'GET'
    return rsp.method.upper()

  @cached_property
  def content_type(self) -> str:
    ''' The base `Content-Type`, eg `'text/html'`.
    '''
    ct = content_type(self.response.headers)
    if ct is None:
      return ''
    return ct.content_type

  @cached_property
  def content_type_params(self) -> Mapping[str, str]:
    ''' The parameters from the `Content-Type` as a mapping of the
        parameter's lowercase form to its value.
    '''
    params = {}
    for param_s in self.response.headers.get('content-type',
                                             '').split(';')[1:]:
      k, v = param_s.split('=', 1)
      params[k.strip().lower()] = v
    return params

  @property
  def content_charset(self) -> str | None:
    ''' The supplied `Content-Type` character set if specified, or `None`.
    '''
    return self.content_type_params.get('charset')

  @property
  def content_encoding(self) -> str:
    ''' The `Content-Encoding` response header, or `''`.
    '''
    return self.response.headers.get('content-encoding', '')

  @content_encoding.setter
  def content_encoding(self, new_encoding: str):
    ''' Set the `Content-Encoding` response header.
    '''
    self.response.headers['Content-Encoding'] = new_encoding or 'identity'

  @content_encoding.deleter
  def content_encoding(self):
    ''' Delete the `Content-Encoding` response header.
    '''
    self.response.headers.pop('content-encoding', None)

  @property
  def content_encodings(self) -> list[str]:
    ''' A list of the transforming encodings named in the `Content-Encoding` response header.
          The encoding `'identity'` is discarded.
      '''
    return [
        enc for enc in content_encodings(self.response.headers)
        if enc != 'identity'
    ]

  def _new_content(self):
    ''' Clear various properties derived from the content.
    '''
    self.clear(
        'iterable_content',
        'json',
        'links',
        'meta',
        'opengraph_tags',
        'soup',
        'text',
    )

  @uses_pilfer
  @promote(params=('url',))
  def GET(
      self,
      url: URL = None,
      *,
      P: "Pilfer",  # noqa: F821
      **rq_kw,
  ) -> requests.Response:
    ''' Do a `Pilfer.GET` of `self.url` and return the `requests.Response`.
        This also updates `self.request` and `self.response`, sets
        `self.iterable_content`, and clears `self.content` and
        `self.soup` (meaning they will be rederived on next access).
    '''
    if url is None:
      url = self.url
    else:
      self.url = url
    # TODO: is the response closed in a timely fashion?
    #       Until consumed it occupies a slot in the urllib3 connection pool.
    #       We try to close in in self.__del__.
    rsp = self.response = P.GET(url, stream=True, **rq_kw)
    # forget any cached derived values
    self.request = rsp.request
    self.url = url
    self._new_content()
    # this should be the decoded content, eg ungzipped
    self.iterable_content = ClonedIterator(rsp.iter_content(chunk_size=None))
    return rsp

  @cached_property
  def request(self):
    ''' Cached request object, obtained via `self.GET()` if needed.
    '''
    self.GET()
    return self.request

  @cached_property
  def response(self):
    ''' Cached response object, obtained via `self.GET()` if needed.
    '''
    return self.GET()

  @cached_property
  def iterable_content(self) -> Iterable[bytes]:
    ''' An iterable of the _decoded_ content.

        After a `self.GET()` this will be a clone of the `requests.Response` stream,
        otherwise it will be `[self.content]`.
    '''
    # there is no .iterable_content yet, expect it from the flow.response.content
    # flow.response.content?
    try:
      flow = self.flow
    except AttributeError:
      # no mitmproxy.http.HTTPFlow, use requests
      # requests.Response.iter_content?
      iter_content = self.response.iter_content
      return ClonedIterator(iter_content(chunk_size=65536))
    # TODO: can we accomodate a flow whose content is streaming in?
    return [flow.response.content]

  @cached_property
  @typechecked
  def content(self) -> bytes:
    ''' The response content, concatenated as a single `bytes` instance
        from `self.iterable_content`.
    '''
    content = b''.join(self.iterable_content)
    self.set_content(content)  # to clear the derived attributes
    return content

  @typechecked
  def set_content(self, content: bytes):
    ''' Set `self.content` and forget the `text` and `soup` attributes.
    '''
    self._new_content()
    self.content = content

  @uses_runstate
  def download(
      self,
      save_filename,
      *,
      format_filename=False,
      runstate: RunState,
      **atfn_kw,
  ):
    ''' Download this `FlowState` to `save_filename`.
        Return the saved save_filename.

        The optional argument `format_filename` may be set to true
        to treat the `save_filename` as a format string to format using
        `self.format_as()`.
    '''
    filename0 = save_filename
    if format_filename:
      save_filename = self.format_as(save_filename)
    content = self.iterable_content
    dl_length = self.url.content_length
    with pfx_call(atomic_filename, save_filename, **atfn_kw) as f:
      for chunk in progressbar(
          content,
          label=save_filename,
          total=dl_length,
          units_scale=BINARY_BYTES_SCALE,
          itemlenfunc=len,
          report_print=qvprint,
      ):
        runstate.raiseif()
        offset = 0
        length = len(chunk)
        while offset < length:
          with Pfx("write %d bytes", length - offset):
            written = f.write(chunk[offset:])
            if written < 1:
              warning("fewer than 1 bytes written: %s", written)
            else:
              offset += written
              assert offset <= length
        assert offset == length
    return save_filename

  @classmethod
  def download_url(cls, url: URL, save_filename: str, **dl_kw):
    ''' A convenience class method to download `url` to `save_filename`.
    '''
    return cls(url=url).download(save_filename, **dl_kw)

  @cached_property
  def text(self) -> str:
    ''' The text content of the URL.
    '''
    # assume UTF-8 if not specified
    charset = self.content_charset or 'utf-8'
    text = self.content.decode(charset)
    self.url.text = text
    return text

  @cached_property
  def json(self):
    ''' A python object decoded from the response JSON payload, an
        alias for `self.response.json`.
    '''
    return self.response.json()

  @cached_property
  def soup(self):
    ''' A `BeautifulSoup` of `self.content` for `text/html`, otherwise `None`.
    '''
    if self.content_type == 'text/html':
      soup = BeautifulSoup(self.text, self.bs4parser)
      self.url.soup = soup
      return soup
    return None

  @cached_property
  def meta(self):
    ''' The meta information from this page's body head meta tags.
        Return an object with the following attriubutes:
        - `.http_equiv`: the `meta` tags with `http-equiv` attributes
        - `.properties`: the `meta` tags with `property` attributes
        - `.tags`: the `meta` tags with `name` attributes
    '''
    meta_tags = TagSet()
    meta_properties = TagSet()
    meta_http_equiv = TagSet()
    soup = self.soup
    if soup is not None:
      if soup.head is None:
        warning("no HEAD tag")
        print(soup)
      else:
        for tag in soup.head.descendants:
          if isinstance(tag, str):
            ##if tag.strip(): warning("SKIP HEAD tag %r", tag[:40])
            continue
          if tag.name != 'meta':
            continue
          tag_content = tag.get('content')
          if not tag_content:
            continue
          if tag_name := tag.get('name'):
            meta_tags[tag_name] = tag_content
          if prop_name := tag.get('property'):
            try:
              tag_content = datetime.fromisoformat(tag_content)
            except ValueError:
              try:
                tag_content = int(tag_content)
              except ValueError:
                pass
            current = meta_properties.get(prop_name)
            if current is None:
              meta_properties[prop_name] = tag_content
            elif isinstance(current, list):
              meta_properties[prop_name].append(tag_content)
            else:
              meta_properties[prop_name] = [current, tag_content]
          if http_equiv := tag.get('http-equiv'):
            meta_http_equiv[http_equiv] = tag['content']
    return NS(
        tags=meta_tags, properties=meta_properties, http_equiv=meta_http_equiv
    )

  @cached_property
  def links(self) -> Mapping[str, list[str]]:
    ''' A `defaultdict(list)` mapping `link` `rel=` values a list of `link` tags.
    '''
    links_by_rel = defaultdict(list)
    soup = self.soup
    if soup is not None:
      for link in soup.find_all('link'):
        for rel in link.attrs.get('rel', ('',)):
          links_by_rel[rel].append(link)
    return links_by_rel

  @cached_property
  def opengraph(self) -> dict:
    ''' The open graph properties as a dict, with their leading
        `"og:"` prefix removed.
        See https://ogp.me/
    '''
    # I have seen these misplaced into the META tags,
    # so get those then overwrite from the properties.
    return {
        k.removeprefix("og:"): v
        for k, v in (*self.meta.tags.items(), *self.meta.properties.items())
        if k.startswith("og:")
    }

  @cached_property
  def opengraph_tags(self) -> dict:
    ''' The open graph properties as a dict of tag names.
        Each tag name has the form `'opengraph.`*prop*`'`
        for the OpenGraph property named `og:`*prop*.
        See https://ogp.me/
    '''
    return {f'opengraph.{k}': v for k, v in self.opengraph.items()}

  def format_kwargs(self):
    ''' Return a `dict` for use with `FormatableMixin.format_as()`.
    '''
    url = self.url
    kwargs = dict(
        basename=url.basename or 'index.html',
        cleanpath=url.cleanpath,
        cleanrpath=url.cleanrpath,
        dirname=url.dirname,
        domain=url.domain,
        ext=url.ext,
        hostname=url.hostname,
        netloc=url.netloc,
        path=url.path,
        rpath=url.rpath,
        scheme=url.scheme,
        short=url.short,
        url=url.url_s,
    )
    if 'response' in self.__dict__:
      hdrs = self.response.headers
      kwargs.update(content_length=content_length(hdrs),)
    return kwargs

uses_flowstate = default_params(flowstate=FlowState.default)

class SiteEntity(HasTags):
  ''' A base class for entities associated with a `SiteMap`.

      This provides the following additional facilities:

      If the entity class has a *FOO*`_FORMAT` attribute
      then `self.`*foo*` will return that formatted against the entity
      A common example is to provide a `SITEPAGE_URL_FORMAT` class
      attribute to enable a `.sitepage_url` attribute which returns the
      primary web page for the entity.

      Entity update: support for `SiteMap.updated_entities(Iterabe[SiteEntity])`:
      - if the entity has a `.update_entity()` method, this will be
        called to update the entity
      - otherwise if the entity has a `.sitepage_url` then
        `entity.grok_sitepage(FlowState)` will be called to update
        the entity; often the `.sitepage_url` is derived from the `SITEPAGE_URL_FORMAT`
        format string

      Design note: there is no default `.update_entity()` based on
      eg `.sitepage_url` because that would prevent use of the concurrent
      prefetching queue in `SiteMap.updated_entities()`.
  '''

  # default staleness is 1 day
  STALE_LIFESPAN = 86400

  # keys which can be obtained by grokking a web page
  # a mapping of tag_name to page name eg "sitepage"
  DERIVED_KEYS = {"_request": "sitepage"}
  # properties which are obtained by defer()ing keys
  # a mapping of property name to tag_name
  DEREFFED_PROPERTIES = {}

  def __init_subclass__(cls, **kw):
    ''' `SiteEntity` subclass init - set `cls.url_re` from `cls.URL_RE` if present.
    '''
    super().__init_subclass__(**kw)
    # default TYPE_SUBNAME derived from the class name
    try:
      TYPE_SUBNAME = cls.__dict__['TYPE_SUBNAME']
    except KeyError:
      cls.TYPE_SUBNAME = cls.__name__.lower()
    # .url_re is the compiled form of .URL_RE if present and not an re.Pattern
    try:
      URL_RE = cls.URL_RE
    except AttributeError:
      pass
    else:
      cls.url_re = (
          URL_RE if isinstance(URL_RE, re.Pattern) else re.compile(URL_RE)
      )

  @classmethod
  def default_sitemap(cls) -> "SiteMap":
    ''' Return the default `SiteMap` instance for `cls.TYPE_ZONE`.
    '''
    return SiteMap.zone_sitemap(cls.TYPE_ZONE)

  @classmethod
  @pfx
  @promote
  def from_URL(
      cls,
      url: URL,
      sitemap: "SiteMap" = None,
      *,
      pattern_name="sitepage_url",
  ):
    ''' Return the `SiteEntity` from `sitemap` matching `url`.
        Raises `ValueError` if the `url.path` does not match via
        `cls.match_url(,url,pattern_name=pattern_name)`.
    '''
    if sitemap is None:
      sitemap = cls.default_sitemap()
    with Pfx("%s.from_URL(%s,%s)", cls.__name__, url, sitemap):
      match = cls.match_url(url, pattern_name=pattern_name)
      if match is None:
        raise ValueError(
            f'no match from {cls.__name__}.match_url({url},{pattern_name=})'
        )
      try:
        type_key = match["type_key"]
      except KeyError as e:
        raise ValueError(
            f'no type_key in match from {cls.__name__}.match_url({url},{pattern_name=}): {match=}'
        )
      # fill in from the match if not already set
      entity = sitemap[cls, type_key]
      for match_key, value in match.items():
        entity.setdefault(match_key, value)
      return entity

  def __getitem__(self, key):
    super_getitem = super().__getitem__
    try:
      return super_getitem(key)
    except KeyError as super_ee:
      # the key is not (yet) present, see if we can fetch it by
      # grokking some web page
      try:
        page_name = self.DERIVED_KEYS[key]
      except KeyError:
        raise super_ee
      if isinstance(page_name, str):
        # a string naming an instance attribute such as .sitepage_url
        # and a grokking method such as .grok_sitepage
        page_url = getattr(self, f'{page_name}_url')
        grok_method = getattr(self, f'grok_{page_name}')
        vprint(f'{self.name}[{key!r}] -> grok_{page_name}({page_url!r})')
        grok_method(page_url)
        # infill keys not obtained so as to not pointlessly refetch
        missing_expected_keys = sorted(
            k for k, kpage in self.DERIVED_KEYS.items()
            if kpage == page_name and k not in self.tags
        )
        if missing_expected_keys:
          vprint(
              f'{self.name}.tags missing {missing_expected_keys=}, (not set by {grok_method.__name__})'
          )
          for k in missing_expected_keys:
            self.tags[k] = None
        return super_getitem(key)
      raise TypeError(
          f'{self.__class__.__name__}.__getitem__({key=}): expected .{key} to be a string, got {r(page_name)}'
      )

  def get(self, key, default=None):
    ''' The `Mapping.get` method, to ensure that it goes through `__getitem__`.
    '''
    try:
      return self[key]
    except KeyError:
      return default

  @classmethod
  def pattern(
      cls,
      pattern_name="sitepage_url",
      *,
      sitemap: "SiteMap" = None
  ) -> URLPattern | None:
    ''' Return a `URLPattern` for the specified page name, default `"sitepage_url"`.
    '''
    if sitemap is None:
      sitemap = cls.default_sitemap()
    try:
      pattern_s = getattr(cls, f'{pattern_name.upper()}_PATTERN')
    except AttributeError as e:
      return None
    return URLPattern(pattern_s, sitemap.URL_DOMAIN)

  @classmethod
  def match_url(cls, url: URL, *, pattern_name="sitepage_url"):
    ''' Test whether this `SiteEntity` subclass matches `url`.
        Return `None` if there is no pattern for `pattern_name`
        (default `"sitepage_url"`), otherwise the result of the
        pattern's `.match(url)` method (`None` on no match, a mapping
        on a match).
        If the optional argument `match` is not `None` it should
        be a mapping, and will be updated by the mapping from a
        successful match.
    '''
    ptn = cls.pattern(pattern_name=pattern_name)
    if ptn is None:
      return None
    return ptn.match(url)

  @mapped_property
  def patterns(self, pattern_name: str):
    ''' A mapping of `pattern_name` to the `URLPattern`
        derived from `getattr(self,f'{pattern_name.upper()}_PATTERN')`.
    '''
    ptn = self.pattern(pattern_name)
    if ptn is None:
      raise KeyError(pattern_name)
    return ptn

  def __getattr__(self, attr):
    ''' A `SiteEntity` supports various automatic attributes.

        Formatted templates:
        If a lowercase attribute has a corresponding class attribute
        `{attr.upper()}_FORMAT` then `self.format_as()` is called
        with the format string to obtain the value.
        If `attr` ends with `_url` and the format result starts with a slash
        it is promoted to a URL by prepending the sitemap URL base
        (via `self.urlto()`).
        Example: `.sitepage_url` can be derived from `.SITEPAGE_URL_FORMAT`
        and the format string need only specify the path after the domain.

        Related entities:
        If the attribute name is present in `self.DEREFFED_PROPERTIES`,
        a mapping of attribute name to `(tag_name,direct)` 2-tuples,
        then the corresponding `tag_name` is obtained and returned
        directly if `direct`.  Otherwise `self.deref(tag_name)` is
        returned.
        This may also be just the `tag_name`, and `direct` will be set to `False`.

        Opengraph fallback:
        If `HasTags.__getattr__(attr)` raises `AttributeError` and
        the tag `opengraph.{attr}` exists, return that.
    '''
    if attr.replace('_', '').islower():
      # *_PATTERN derived attributes
      ptnattr_name = f'{attr.upper()}_PATTERN'
      try:
        pattern_s = getattr(self.__class__, ptnattr_name)
      except AttributeError:
        pass
      else:
        pattern = self.patterns[attr]
        fields = dict(self)
        fields.setdefault('type_key', self.type_key)
        return pattern.url_path_for(fields)
      # *_FORMAT derived attributes
      # .fmtname returns self.format_as(cls.FMTNAME_FORMAT)
      fmtattr_name = f'{attr.upper()}_FORMAT'
      try:
        format_s = getattr(self.__class__, fmtattr_name)
      except AttributeError:
        pass
      else:
        try:
          formatted = self.format_as(format_s)
        except FormatAsError as e:
          warning("%s.format_as %r: %s", self, format_s, e)
          raise AttributeError(
              f'format {self.__class__.__name__}.{fmtattr_name} {format_s!r}: {e.key}'
          ) from e
        else:
          # for attributes ending in _url, such as .sitepage_url_url
          # if the result commences with a / we consider it a subpath
          # of the site domain
          # TODO: maybe test for :// ?
          if attr.endswith('_url') and formatted.startswith('/'):
            formatted = self.urlto(formatted)
          return formatted
    # indirect derived attributes
    DEREFFED_PROPERTIES = self.__class__.DEREFFED_PROPERTIES
    try:
      deref_from = DEREFFED_PROPERTIES[attr]
    except KeyError:
      # not a DEREFFED_PROPERTIES, try the superclass
      try:
        return super().__getattr__(attr)
      except AttributeError as e:
        # try from the tags, may autofetch
        try:
          return self[attr]
        except KeyError:
          # no superclass attribute, try the opengraph property
          og_tag_name = f'opengraph.{attr}'
          try:
            return self[og_tag_name]
          except KeyError:
            raise e
    if isinstance(deref_from, str):
      tag_name, direct = deref_from, False
    else:
      tag_name, direct = deref_from
    # obtain the value; some tags will fetch info from the web if missing
    # NB: always access the tag, triggers page fetch if missing
    value = self[tag_name]
    if direct:
      return value
    else:
      value = self.deref(tag_name)
    return value

  @property
  def sitepage_url(self):
    ''' Allow `self["sitepage"]` to override `self.SITEPAGE_URL_FORMAT`.
    '''
    try:
      page_url = self.tags["sitepage"]
    except KeyError:
      page_url = self.__getattr__('sitepage_url')
    if page_url.startswith('/'):
      page_url = self.urlto(page_url)
    return page_url

  def format_kwargs(self):
    ''' The format keyword mapping for a `SiteEntity`.

        This includes:
        - the `HasTags.format_kwargs()`
        - the values for any names in `type(self.tags)` with an
          upper case leading letter such as `URL_DOMAIN`
    '''
    kwargs = super().format_kwargs()
    etags = {
        k: v
        for k, v in self.tags.__class__.__dict__.items()
        if k[:1].isupper()
    }
    return ChainMap(etags, kwargs) if etags else kwargs

  @property
  def sitemap(self):
    ''' The `SiteMap` is the `tags_db`.
    '''
    return self.tags_db

  def update_from_sitemap(self):
    ''' Run this entity through `self.updated_entities()`.
    '''
    for _ in self.sitemap.updated_entities((self,)):
      pass

  def updated(self):
    ''' Return this entity after updating via `self.update_from_sitemap()`.

        This supports idioms like:

            entity = sitemap[EntityType, 'entity_key'].update()

        for working with a known up to date entity.
    '''
    self.update_from_sitemap()
    return self

  @cached_property
  def url_root(self):
    ''' Proxy to `self.sitemap.url_root`.
    '''
    return self.sitemap.url_root

  def urlto(self, path):
    ''' Proxy to `self.sitemap.urlto()`.
    '''
    return self.sitemap.urlto(path)

  @pagemethod
  def grok_sitepage(self, flowstate: FlowState, match=None):
    ''' The basic sitepage grok: record the metadta.
    '''
    self._request_update(flowstate, page="sitepage")
    self.update_from_meta(flowstate)
    self.update(flowstate.opengraph_tags)

  def _request(self, *, page="sitepage", method="GET"):
    ''' Return the dict which caches the HTTP Response from the last request for `page`.

        Note that just updating this dict does not reflect in the database.
        Instead the `._request_update(flowstate)` method should be called.
    '''
    return self.setdefault("_request",
                           {}).setdefault(page, {}).setdefault(method, {})

  def _request_update(
      self, flowstate: FlowState, *, page="sitepage", method="GET"
  ):
    ''' Update the cached HTTP response information for `page` from `flowstate`.
    '''
    _request = self._request(page="sitepage", method=flowstate.method)
    _request.update(
        url=flowstate.url.url_s,
        request={
            hdr.lower(): value
            for hdr, value in sorted(flowstate.request.headers.items())
            if hdr.lower() != 'authorization'
        },
        response={
            hdr.lower(): value
            for hdr, value in sorted(flowstate.response.headers.items())
        },
    )
    # update the database
    self.tags.set('_request', self['_request'])

  def rq_timestamp(self, *, page="sitepage", method="GET"):
    ''' Return the cached HTTP Response `Date` field for `page` as a UNIX timestamp.
    '''
    http_date = self._request(
        page=page, method=method
    ).get("response", {}).get("date")
    if http_date:
      return datetime_from_http_date(http_date).timestamp()
    return 0

  def is_stale(
      self, lifespan=STALE_LIFESPAN, *, page="sitepage", method="GET"
  ):
    ''' Test if the sitepage response timestamp is more than `lifespan` seconds older than `time.time()`.
    '''
    is_stale = time.time() - self.rq_timestamp(
        page=page, method=method
    ) > lifespan
    if is_stale: breakpoint()
    return is_stale

  def refresh(self, *, force=False, lifespan=STALE_LIFESPAN):
    ''' Refetch and reparse `self.sitepage_url` via `self.grok_sitepage()`
        if the page is stale (or if `force` is true).
    '''
    if not force and not self.is_stale(lifespan):
      return
    self.grok_sitepage(self.sitepage_url)

  def update_from_meta(self, flowstate: FlowState, **update_kw):
    ''' Update this entity from the `flowstate.meta`.
    '''
    self.sitemap.update_mapping_from_meta(self, flowstate, **update_kw)

  def equivalents(self):
    ''' Return a list of equivalent `SiteEntity` instances from other type zones,
        derived from the keys in `self['equivalents']`.
        Unhandled keys elicit a warning and are discarded.
    '''
    with Pfx("%s.equivalents", self):
      equiv_keys = self.get('equivalents', [])
      equivs = []
      for eqk in equiv_keys:
        with Pfx(eqk):
          try:
            equiv = SiteMap.by_db_key(eqk)
          except KeyError as e:
            warning("no SiteEntity for key: %s", e)
            continue
          equivs.append(equiv)
    return equivs

  @classmethod
  def by_db_key(cls, db_key: str):
    ''' Return the `SiteEntity` for the database wide `key`.
    '''
    return SiteMap.by_db_key(db_key)

  def update_content_timestamp(self, purpose, content_signature) -> float:
    ''' Update the UNIX time recorded for `purpose` based on
        `content_signature`. Return the old time if there was one
        and its signature equals `content_signature`.  Otherwise
        update the timestamp and record it and the new signature.
    '''
    tag_name = f'timestamp.{purpose}'
    try:
      last_sig = self[tag_name]
    except KeyError:
      # no previous timestamp
      pass
    else:
      when, old_signature = last_sig
      if old_signature == content_signature:
        # unchanged
        return when
    when = time.time()
    self[tag_name] = [when, content_signature]
    return when

  @staticmethod
  @decorator
  def paginated(grok_page):
    ''' A decorator for methods to process a URL (such as `grok_sitepage`)
        where the information is spread out across multiple numbered pages.

        Classes whose methods utilise this decorator must implement:
        - `soup_count_pages(soup)->int` to compute how many pages
          there are from the soup of the first page
        - `page_url(base_url:str,page_num:int)->str` to compute the
          URL of the page numbered `pagenum` from a base URL
        Typically these would be implemented on the base `SiteEntity` subclass.

        The method being decorated should look like:

            def method(self, flowstate:FlowState, *a, pagenum:int, **kw):

        and the wrapped method which results looks like:

            def method(self,flowstate:FlowState, *a, pagenum:int|Ellipsis=Ellipsis, **kw):

        If `pagenum` is an `int`, to process a single page, the inner
        method will be called directly.

        If `pagenum` is `...` (`Ellipsis`, the default), the number
        of pages will be be obtained from the `flowstate.soup` via
        the `self.soup_count_pages` method and each page will be
        processed in turn. The URL of each page is computed with the
        `self.page_url(base_url,pagenum)` method.

        Note: the underlying method should expect to be accruing information
        to the information already in `self`. If the information
        should be reset, that should happen conditionally when
        `pagenum==1` so that the subsequent pages do not undo the
        work of previous pages.

        Example:

            @paginated
            def grok_sitepage(self,flowstate:FlowState,method=None,*,pagenum:int):
                # process the flowstate, expecting to be called once for each page
    '''

    @uses_runstate
    @promote
    def grok_pages_wrapper(
        self,
        flowstate: FlowState,
        *grok_a,
        runstate: RunState,
        pagenum: int | type(Ellipsis) = ...,
        **grok_kw,
    ):
      if pagenum is ...:
        base_url = str(flowstate.url)
        page_count = self.soup_count_pages(flowstate.soup)
        grok_page(self, flowstate, *grok_a, pagenum=1, **grok_kw)
        runstate.raiseif()
        pagenums = range(2, page_count + 1)
        pagenum_flowstate = progressbar(
            zip(
                pagenums,
                FlowState.iterable_flowstates(
                    (self.page_url(base_url, n) for n in pagenums),
                    unordered=False,
                ),
            ),
            "pages",
            total=len(pagenums)
        )
        for pagenum, flowstate in pagenum_flowstate:
          runstate.raiseif()
          grok_page(self, flowstate, *grok_a, pagenum=pagenum, **grok_kw)
      else:
        grok_page(self, flowstate, *grok_a, pagenum=pagenum, **grok_kw)

    return grok_pages_wrapper

  def download(self, save_filename: str = None, **fsdl_kw) -> str:
    ''' Download this entity to `save_filename` (default from the
        `self.download_url` basename).
    '''
    url = URL(self.download_url)
    if save_filename is None:
      save_filename = url.basename or 'index.html'
    save_filename = FlowState.download_url(url, save_filename, **fsdl_kw)
    self['download_fspath'] = abspath(save_filename)
    self.add('downloaded')
    return save_filename

paginated = SiteEntity.paginated

class SiteMapPatternMatch(namedtuple(
    "SiteMapPatternMatch", "sitemap pattern_test pattern_arg match mapping")):
  ''' A pattern match result:
      * `sitemap`: the source `SiteMap` instance
      * `pattern_test`: the pattern test object
      * `pattern_arg`: the argument to the pattern
      * `match`: the match result object from the pattern test
        such as an `re.Match` instance
      * `mapping`: a mapping of named values gleaned during the match
  '''

  def format_arg(self, extra: Mapping | None = None) -> str:
    ''' Treat `self.pattern_arg` as a format string and format it
        using `self.mapping` and `extra`.
    '''
    return self.pattern_arg.format_map(ChainMap(self.mapping, extra or {}))

@dataclass
class SiteMap(UsesTagSets, Promotable):
  ''' A base dataclass for site maps.

      A `SiteMap` data class embodies domain specific knowledge about a
      particular website or collection of websites.

      It subclasses `cs.tagset.UsesTagSets` and gets its `.tagsets`
      from `self.pilfer` if unspecified. Use as a `UsesTagSet`
      domain requires setting:
      - `TYPE_ZONE` to the domain prefix
      - `HasTagsClass` to the base `HasTags` entity class
      See `cs.tagset.UsesTagSets` for details.

      A `Pilfer` instance obtains its site maps from the `[sitemaps]`
      clause in the configuration file, see the `Pilfer.sitemaps`
      property for specifics.

      A pilferrc configuration example:

          [sitemaps]
          docs.python.org = docs:cs.app.pilfer.sitemap:DocSite
          docs.mitmproxy.org = docs
          *.readthedocs.io = docs

      This says that websites whose domain matches `docs.python.org`,
      `docs.mitmproxy.org` or the filename glob `*.readthedocs.io`
      are all associated with the `SiteMap` referred to as `docs`
      whose definition comes from the `DocSite` class from the module
      `cs.app.pilfer.sitemap`. The `DocSite` class will be a subclass
      of this `SiteMap` base class.

      `SiteMap`s have a few class attributes:
      * `TYPE_ZONE`: the `TagSetTyping.type_zone` value for entities
        associated with this class
      * `URL_KEY_PATTERNS`: this is a list of `(match,keyformat)`
        2-tuples specifying cache keys for caching URL contents; the
        `pilfer mitm ... cache` filter consults these to decide what
        URLs to cache.
        See the `SiteMap.url_key` method.
      * `PREFETCH_PATTERNS`: this is a list of `(match,keyformat)`
        2-tuples specifying prefetch URLs for a URL's contents; the
        `pilfer mitm ... prefetch` filter consults these to decide what
        URLs to queue for prefetching.
        See the `SiteMap.content_prefetch` method.
  '''

  name: str = None
  pilfer: object = None
  tagsets: BaseTagSets = None

  # a registry of SiteMap subclasses by their TYPE_ZONE
  sitemap_by_type_zone = {}

  URL_KEY_PATTERNS = ()

  @uses_pilfer
  def __post_init__(
      self,
      *,
      P: "Pilfer",  # noqa: F821
  ):
    ''' Initialise `.pilfer` if omitted`, and then `.tagsets` from `self.pilfer`.
    '''
    if self.name is None:
      self.name = self.TYPE_ZONE
    if self.pilfer is None:
      self.pilfer = P
    # Register this `SiteMap` by its `TYPE_ZONE`.
    sitemap_by_type_zone = self.__class__.sitemap_by_type_zone
    try:
      self.TYPE_ZONE
    except AttributeError:
      vprint(f'no .TYPE_ZONE for SiteMap instance {self}')
      pass
    else:
      try:
        other_map = sitemap_by_type_zone[self.TYPE_ZONE]
      except KeyError:
        # not already taken
        pass
      else:
        warning(f'replacing {self.TYPE_ZONE=} -> {other_map} with {self}')
      vprint(f'sitemap_by_type_zone[{self.TYPE_ZONE=}] = {self=}')
      sitemap_by_type_zone[self.TYPE_ZONE] = self
    super().__init__(tagsets=self.pilfer.sqltags)

  @classmethod
  def zone_sitemap(cls, type_zone: str):
    ''' The `SiteMap` associated with `type_zone`.
    '''
    return cls.sitemap_by_type_zone[type_zone]

  @classmethod
  def by_db_key(cls, db_key: str) -> SiteEntity:
    ''' Return the `SiteEntity` for the database wide `key`.
    '''
    try:
      zone, subname, key = TagSetTyping.type_parts_of(db_key)
    except ValueError as e:
      raise KeyError(f'{db_key=}: cannot parse into type parts: {e}') from e
    try:
      sitemap = cls.sitemap_by_type_zone[zone]
    except KeyError as e:
      raise KeyError(f'{db_key=}: no SiteMap registered for {zone=}') from e
    return sitemap[subname, key]

  @classmethod
  @uses_pilfer
  def from_str(
      cls,
      sitemap_name: str,
      *,
      P: "Pilfer",  # noqa: F821
  ) -> "SiteMap":
    ''' Return the `SiteMap` instance known as `sitemap_name` in the ambient `Pilfer` instance.
    '''
    for name, sitemap in P.sitemaps:
      if name == sitemap_name:
        return sitemap
    raise ValueError(
        f'{cls.__name__}.from_str({sitemap_name!r}): unknown sitemap name'
    )

  @property
  def name__(self):
    ''' The `SiteMap.name` with slashes replaced by double underscores.
    '''
    return self.name.replace("/", "__")

  def urlto(self, suburl, domain=None, *, scheme='https'):
    ''' Return the full URL for `suburl`.
    '''
    return f'{scheme}://{domain or self.URL_DOMAIN}/{suburl.lstrip("/")}'

  @cached_property
  def url_root(self) -> str:
    ''' The root URL for this site, derived from `self.URL_DOMAIN`.
        This includes the trailing slash, eg `https://example.com/`
    '''
    return self.urlto('')

  # TODO: some notion of staleness
  @classmethod
  @uses_pilfer
  def updated_entities(
      cls,
      entities: Iterable["SiteEntity"],
      *,
      P: "Pilfer",  # noqa: F821
      force=False,
  ):
    ''' A generator yielding updated `SiteEntity` instances
        from an iterable of `SiteEntity` instances.

        Note that the updated entities may not be yielded in the
        same order as `entities`, depending on delays in fetching
        their sitepages; in particular entities with missing
        `.sitepage_url` or `.grok_sitepage()` will appear ahead of
        entities requiring a fetch.

        Parameters:
        - `entities`: an iterable of `SiteEntity` instances to update
        - `force`: optional flag to force an update even if the
          entity does not appear stale

        This functions by considering each entity in `entities`;
        if an entity has a `.sitepage_url` attribute and if it stale
        or `force`, place it on a queue of entities to have their
        `sitepage` URL fetched and grokked. Other entities are
        passed through immediately.

        The entities with `sitepage` URLs have a `request.GET`
        dispatched, concurrently mediated via the ambient `Pilfer's`
        `Later` work queue, and are passed, with their `FlowState`,
        to the grokking worker as their requests become ready i.e.
        that the request response headers have been received.  The
        worker then called `entity.grok_sitepage(flowstate)` to
        parse the content.
    '''
    # Prepare queues for processing:
    #
    # entities -> process_entities -> ent_spQ -> process_entity_sitepages
    #                    |                              |
    #                    v                              |
    #                 ent_fsQ <-------------------------+
    #                    |
    #                    v
    #         call entity.grok_sitepage(flowstate)
    #                    |
    #                    v
    #             updated entities
    start_time = time.time()
    ent_spQ = IterableQueue()
    ent_sps = ClonedIterator(ent_spQ)
    ent_fsQ = IterableQueue()

    def process_entities():
      ''' Process the iterable of entities.
          Entities with a custom `.update_entity` method or with no
          `.sitepage_url` or which are not stale are put directly only
          `ent_fsQ` with `None` for the `flowstate`.  Other entities
          are put on `flowstateQ` to be fetched.
      '''
      try:
        for entity in entities:
          ##print(f'PROCESS_ENTITIES: entity {entity}')
          # TODO: a better staleness criterion
          if not force and "sitepage.last_update_unixtime" in entity:
            # do not update
            ent_fsQ.put((entity, None))
            continue
          # TODO: maybe a .prefetch_update_url property allowing use of
          # the prefetch queue in conjunction with .update_entity()?
          # That would (a) allow prefetch for this and (b) allow a
          # default .sitepage_url based .update_entity() method.
          update_entity = getattr(entity, 'update_entity', None)
          if callable(update_entity):
            # the entity has a custom update method
            ent_fsQ.put((entity, update_entity))
            continue
          # Why isn't there a default .update_entity() which uses
          # the .sitepage_url? Because it would prevent this prefetching
          # queue.
          # TODO: skip entities with no .grok_sitepage method
          sitepage = getattr(entity, "sitepage", None)
          if sitepage is None:
            # no sitepage, do not update
            ##print("  NO SITEPAGE")
            ent_fsQ.put((entity, None))
            continue
          if not hasattr(entity, 'grok_sitepage'):
            # sitepage but no grok-sitepage, do not update
            ent_fsQ.put((entity, None))
            continue
          # send the entity and sitepage to the prefetcher
          ent_spQ.put((entity, sitepage))
      finally:
        # close the (entity,sitepage) processor
        ent_spQ.close()
        # send one of the 2 end markers for the (entity,flowstate) queue
        ent_fsQ.put((None, None))

    def process_entity_sitepages():
      ''' Process the queue of `(entity,sitepage)` pairs from `ent_spQ`
          and put `(entity,flowstate)` 2-tuples onto `ent_fsQ`
          as the flowstates become ready (HTTP response received,
          content available in streaming mode).
      '''
      try:
        for (entity, sitepage), flowstate in zip(
            ent_sps,
            FlowState.iterable_flowstates(
                (ent_sp[1] for ent_sp in ent_sps),
                unordered=False,
            ),
        ):
          ##print(f'process_entity_sitepages: ent_fsQ <- ({entity=},flowstate)')
          ent_fsQ.put((entity, flowstate))
      finally:
        # send one of the 2 end markers
        ent_fsQ.put((None, None))

    # dispatch the workers
    process_entitiesT = Thread(target=with_(process_entities, P), daemon=True)
    process_entity_sitepagesT = Thread(
        target=with_(process_entity_sitepages, P), daemon=True
    )
    process_entitiesT.start()
    process_entity_sitepagesT.start()

    # Process the (entity,flowstate) queue.
    # Each 2-tuple received will be:
    # - (None,None): an end marker from one of the workers - we expect 2
    # - (entity,None): an entity whose sitepage is unknown or unobtainable
    # - (entity,flowstate): an entity and a ready flowstate whose content can be processed
    #
    # TODO: this also needs to be a worker running a Later.map()
    n_end_markers = 0
    for qitem in ent_fsQ:
      ##print("ent_fsQ ->", r(qitem))
      entity, update_from = qitem
      if entity is None:
        # should be a (None,None) end marker
        assert update_from is None
        n_end_markers += 1
        if n_end_markers == 2:
          break
        # we do not yield the marker
        continue
      if update_from is None:
        # no update action, do nothing
        pass
      else:
        try:
          with Pfx("update %s", entity):
            if isinstance(update_from, FlowState):
              # update from a ready FlowState
              flowstate = update_from
              # process the flowstate
              with Pfx("%s.grok_sitepage(FlowState:%s", entity,
                       flowstate.url.short):
                entity.grok_sitepage(flowstate)
            elif callable(update_from):
              # update from a callable, usually entity.update_entity()
              update_entity = update_from
              pfx_call(update_entity)
            else:
              raise ValueError(f'cannot honour update_from={r(update_from)}')
        except Exception as e:
          warning("exception for update_from=%s: %s", r(update_from), s(e))
        else:
          # mark the entity as up to date as of start_time
          entity["sitepage.last_update_unixtime"] = start_time
      yield entity

  @staticmethod
  @decorator
  @typechecked
  def on(method, *patterns, **tags_kw):
    ''' A decorator for handler methods which specifies conditions
        which must match for this handler to be called.
        This decorator may be applied multiple times
        if the handler method should match various flows.

        Its positional parameters indicate the conditions under
        which this method will be fired; all must be true.
        Each use of the decorator appends a `(conditions,tags_kw)`
        2-tuple to the method's `.on_conditions` attribute,
        where `conditions` is list storing the conjunction of conditions
        and `tags_kw` is any *tag*`=`*format*` supplied in
        the decorator keyword arguments.

        The positional parameters have the following meaning:
        - a string consisting entirely of uppercase letters;
          this matches an HTTP method name such as `GET` or `POST`
        - a string containing no slash character (`'/'`):
          a filename glob to match against the URL hostname
        - a string containing a slash:
          a regular expression to apply against the URL path;
          a leading slash anchors the regexp against the start of the path
          otherwise it may match anywhere in the path
        - a regular expression object to apply against the URL path
        - subclass of `SiteEntity`: use its `.match_url()` class method
        - a `URLPattern`: use its `.match()` method
        - a callable: a function accepting a `FlowState` and the
          current match `TagSet`;
          it may return `None` or `False` for no match,
          or `True` or a `Mapping[str,str]` for a match
        Note that to avoid confusing the decorator the first condition
        cannot be a callable. However, it's usually a domain glob anyway.

        The keyword parameters specify `Tag`s to set on the match `TagSet`
        if the conditions have matched. The parameter value may be:
        - a callable: a function accepting a `FlowState` and the
          current match `TagSet` which computes the tag value
        - otherwise it should be a string which will be formatted
          using `.format_map(match)`

        For example this decoration matches the URL hostname
        `docs.python.org` and the URL path
        `/3/library/`*module_name*`.html`.
        On a match the resulting match tags will contain:
        * `'module_name'`: from the regular expression
        * `'cache_key'`: from the `cache_key=` argument

            @on(
                'docs.python.org',
                r'/3/library/(?P<module_name>[^/]+).html$',
                cache_key='module/{module_name}',
            )
            def cache_key_pydoc(self, flowstate, match: TagSet) -> str:
                # here one might fill in match['cache_key']
                # aka match.cache_key
                ........

        You may notice that the cache key doesn't mention the
        hostname; the caching system qualifies cache keys with their
        `SiteMap` name and so the key here need only be unique
        within domains served with this `SiteMap`.
    '''
    conditions = []
    for pattern in patterns:
      with Pfx(f'pattern={r(pattern)}'):
        if isinstance(pattern, type) and issubclass(pattern, SiteEntity):

          def maketest(entity_class):
            test_name = f'{entity_class.__name__}.match_url(flowstate.url)'

            def test(flowstate, match):
              vprint(f'@on: {test_name}')
              m = entity_class.match_url(flowstate.url)
              if m is not None and match is not None:
                match.update(m)
              return m

            test.__name__ = test_name
            return test
        elif isinstance(pattern, type) and issubclass(pattern, URLPattern):

          def maketest(entity_class):
            test_name = f'{pattern}.match(flowstate.url)'

            def test(flowstate, match):
              vprint(f'@on: {test_name}')
              m = pattern.match(flowstate.url)
              if m is not None and match is not None:
                match.update(m)
              return m

            test.__name__ = test_name
            return test
        elif isinstance(pattern, str):
          if pattern.isupper():
            # a method name
            def maketest(method_name):
              test_name = f'flowstate.method == {method_name=}'

              def test(flowstate, match):
                vprint(f'@on: {method_name=} vs {method_name=}')
                return flowstate.method == method_name

              test.__name__ = test_name
              test.method_name = method_name  # for use in breakpoints
              return test

          elif '/' in pattern:
            # a path match
            def maketest(regexp_s):
              regexp = pfx_call(re.compile, regexp_s)
              if pattern.startswith('/'):
                # match at the start of the path
                test_name = f'flowstate.url.path ~ ^{regexp}'

                def test(flowstate, match):
                  vprint(f'@on: {flowstate.url.path=} ~ ^{regexp}')
                  m = pfx_call(regexp.match, flowstate.url.path)
                  if m is None:
                    return None
                  return m.groupdict()
              else:
                test_name = f'flowstate.url.path ~ {regexp}'

                def test(flowstate, match):
                  vprint(f'@on: {flowstate.url.path=} ~ {regexp}')
                  m = pfx_call(regexp.search, flowstate.url.path)
                  if m is None:
                    return None
                  return m.groupdict()

              test.__name__ = test_name
              return test
          else:
            # filename glob on the URL host
            def maketest(glob):

              def test(flowstate, match):
                vprint(f'@on: {flowstate.url.hostname=} ~ {glob=}')
                return pfx_call(fnmatch, flowstate.url.hostname, glob)

              test.__name__ = f'flowstate.url.hostname ~ {glob=}'
              test.glob = glob
              return test

        elif isinstance(pattern, re.Pattern):
          # a path match
          def maketest(regexp):
            test_name = f'flowstate.url.path ~ {regexp}'

            def test(flowstate, match):
              vprint(f'@on: {flowstate.url.path=} ~ {regexp}')
              m = pfx_call(regexp.search, flowstate.url.path)
              if m is None:
                return None
              return m.groupdict()

            test.__name__ = test_name
            return test

        elif callable(pattern):
          # it should be a callable accepting a FlowState and the match TagSet
          # TODO: can it be inspected?
          _: Callable[FlowState, TagSet] = pattern

          def maketest(condition_func):
            citation = funccite(condition_func)

            def test(flowstate, match):
              vprint(f'@on: {citation}(flowstate:{flowstate.url=})')
              return condition_func(flowstate)

            test.__name__ = f'{citation}(flowstate)'
            return test

        else:
          raise RuntimeError
        condition = maketest(pattern)
        assert condition is not None
        assert callable(condition)
        conditions.append(condition)
        del condition
    try:
      cond_attr = method.on_conditions
    except AttributeError:
      cond_attr = method.on_conditions = []
    cond_attr.append((conditions, tags_kw))
    return method

  @classmethod
  @pfx_method
  @uses_verbose
  @promote
  def on_matches(
      cls,
      flowstate: FlowState,
      methodglob: str | None = None,
      *,
      verbose: bool,
      **match_kw,
  ) -> Iterable[tuple[Callable, TagSet]]:
    ''' A generator yielding `(method,match)` 2-tuples for methods matched
        by `flowstate` and `match_kw`, being the matching method
        and a `TagSet` of values obtained during the match test.

        Parameters:
        * `flowstate`: the `FlowState` on which to match
        * `methodglob`: an optional filename glob constraining the chosen method names
        * `match_kw`: the `on_match` keyword arguments which must match

        The matching methods are identified by consulting the
        conditions in the method's `.on_conditions` attribute, a
        list of conjunctions normally defined by applying the `@on`
        decorator to the method.
        A `(method,match)` 2-tuple is yielded for each matching conjunction.

        Note that this means the same methods may be yielded multiple
        times if different conjunctions match (eg multiple matching
        `@on` decorators); this is because each condition may provide
        different `match` match results.
    '''
    for method_name in dir(cls):
      if methodglob is not None and not fnmatch(method_name, methodglob):
        continue
      try:
        method = getattr(cls, method_name)
      except AttributeError:
        continue
      try:
        conditions = method.on_conditions
      except AttributeError:
        # no conditions, skip
        continue
      # Prepare the final match result.
      # Start with the URL attributes.
      url = flowstate.url
      match = TagSet(
          {
              attr: getattr(url, attr)
              for attr in (
                  'basename',
                  'cleanpath',
                  'cleanrpath',
                  'dirname',
                  'domain',
                  'hostname',
                  'netloc',
                  'path',
                  'port',
                  'rpath',
                  'scheme',
              )
          }
      )
      # set various other things
      match.update(
          # url cleaned relative path
          _=url.cleanrpath,
          # hostname/cleanpath
          __=f'{url.hostname}/{url.cleanrpath}',
          # rq method eg GET
          method=flowstate.method,
      )
      # eg "tvdb" for thetvdb.com, for use with sqltags
      try:
        type_zone = cls.TYPE_ZONE
      except AttributeError:
        pass
      else:
        match["type_zone"] = type_zone
      for conjunction, tags_kw in conditions:
        with Pfx("match %r", conjunction):
          for condition in conjunction:
            cond_spec = getattr(condition, "__name__", str(condition))
            with Pfx("on_matches: test %r vs %s", method_name, cond_spec):
              if verbose:
                print('ON_MATCHES', f'{method_name} vs {cond_spec}')
                ##printt(*sorted(match.items()), indent='  ')
              try:
                test_result = pfx_call(condition, flowstate, match)
              except Exception as e:
                warning("exception in condition: %s", e)
                # TODO: just fail? print a traceback if we do this
                raise
              if verbose:
                print(f'  -> {test_result=}')
              # test ran, examine result
              if test_result is None or test_result is False:
                # failure
                break
              # success
              if test_result is True:
                # success, no side effects
                pass
              else:
                # should be a mapping, update the match TagSet
                # typical example: the result is a re.Match.groupdict()
                for k, v in test_result.items():
                  vprint(f'    set match[{k=}] = {v=}')
                  match[k] = v
          else:
            vprint("ALL CONDITIONS OK")
            # no test failed, this is a match
            # update the match with any format strings from @on
            for name, fmt in tags_kw.items():
              if callable(fmt):
                match[name] = fmt(flowstate, match)
              else:
                match[name] = fmt.format_map(match)
            yield method, match

  @pfx_method
  @promote
  def run_matches(
      self,
      flowstate: FlowState,
      flowattr: str | None = None,
      methodglob: str | None = None,
      **match_kw,
  ) -> Iterable[tuple[Callable, TagSet, Any]]:
    ''' Run all the methods in this `SiteMap` whose `.on_conditions`
        match `flowstate` and ``match_kw`, as matched by `SiteMap.on_matches`.
        Yield `(method,match,result)` 3-tuples from each method called.

        Parameters:
        * `flowstate`: the `FlowState` on which to match
        * `flowattr`: an optional attribute name of the `flowstate`
        * `methodglob`: an optional filename glob constraining the chosen method names
        * `match_kw`: the `on_match` keyword arguments which must match

        Each `method` is called as `method(self,flowstate,match)`
        where `method` and `match` were yielded from
        `on_matches(flowstate,**match_kw)`.

        If `flowattr` is not `None`, `getattr(flowstate,flowattr)`
        is passed as an additional positional parameter and if the
        method result is not `None` then the result is set as an
        updated value on `flowstate`.
    '''
    ent = self.url_entity(flowstate.url)
    if ent is not None:
      ent.grok_sitepage(flowstate)
    else:
      vprint(f'{self}.run_matches: no entity for {flowstate.url}')
    for method, match in self.on_matches(flowstate, methodglob, **match_kw):
      with Pfx("call %s", method.__qualname__):
        try:
          if flowattr is None:
            result = method(self, flowstate, match)
          else:
            attrvalue = pfx_call(getattr, flowstate, flowattr)
            result = method(self, flowstate, match, attrvalue)
        except Exception as e:
          warning("%s.%s: url=%s: %s", self, method.__name__, flowstate.url, e)
          raise
        else:
          if flowattr is not None and result is not None:
            pfx_call(setattr, flowstate, flowattr, result)
          yield method, match, result

  @pfx_method
  @promote
  def grok(
      self,
      flowstate: FlowState,
      flowattr: str | None = None,
      **run_match_kw,
  ) -> Iterable[tuple[Callable, TagSet, Any]]:
    ''' A generator to grok the fullness of this `flowstate`, deriving information.
        Usually this involves consulting the URL contents.
        This is a shim for `SiteMap.run_matches` calling any matching
        methods named `grok_*`.
        Yield `(method,match,result)` 3-tuples from each method called.
        Usually the `result` is a `TagSet`.
    '''
    if not (200 <= flowstate.response.status_code < 300):
      warning(
          f'{flowstate.url.short} {flowstate.response.status_code=} != 2xx, not grokking'
      )
      return
    yield from self.run_matches(flowstate, flowattr, 'grok_*', **run_match_kw)

  @staticmethod
  @decorator
  def grok_entity_page(func, *, ent_class, page='sitepage', type_key=None):
    ''' A decorator for web page `grok_*` methods which apply grokked
        information to a `SiteEntity`.
        This obtains the `entity` from `self[ent_class,match["type_key"]]`,
        calls `entity.grok_sitepage(flowstate)`,
        calls `func(self,flowstate,match,entity)`,
        returns the `entity`.

        Example:

            @on(
                URL_DOMAIN,
                r'/something/(?P<type_key>[^/+])/....',
            )
            @grok_entity_page(ent_class=FrogEntity)
            def grok_frog_sitepage(self, flowstate: FlowState, match, entity:FrogEntity):
                pass # this example does no additional work
    '''

    def _grok_sitepage_wrapper(
        self,
        flowstate: FlowState,
        match=None,
    ) -> SiteEntity:
      try:
        ent_type_key = match["type_key"]
      except KeyError as e:
        if type_key is None:
          raise KeyError(
              f'no "type_key" in {match=} and no type_key= in @grok_entity_page decorator'
          ) from e
        ent_type_key = type_key
      entity = self[ent_class, ent_type_key]
      if flowstate is None:
        url = getattr(entity, f'{page}_url')
        flowstate = FlowState.from_URL(url)
      grok_method = getattr(entity, f'grok_{page}')
      grok_method(flowstate)
      func(self, flowstate, match, entity)
      return entity

    return _grok_sitepage_wrapper

  @promote
  def url_entity(self, url: URL):
    ''' Return the `SiteEntity` associated with this URL, or `None`
        for an unrecognised URL.
        Raise `ValueError` if multiple entities match the URL.
    '''
    try:
      base_entity_class = self.HasTagsClass
    except AttributeError as e:
      vprint(
          f'{self.__class__.__name__}.url_entity: no .HasTagsClass, skipping'
      )
      return None
    entities = []
    for ent_class in public_subclasses(base_entity_class):
      try:
        entity = ent_class.from_URL(url, self)
      except ValueError as e:
        vprint(
            f'url_entity({url=}): SKIP {ent_class=}, does not match {ent_class=}: {e}'
        )
        continue
      entities.append(entity)
    if not entities:
      return None
    entity, = entities
    return entity

  def matches(
      self,
      url: URL,
      patterns: Iterable,  # [Tuple[Tuple[str, str], Any]],
      extra: Mapping | None = None,
  ) -> Iterable[SiteMapPatternMatch]:
    ''' A generator to match `url` against `patterns`, an iterable
        of `(match_to,arg)` 2-tuples which yields
        a `SiteMapPatternMatch` for each pattern which matches `url`.

        Parameters:
        * `url`: a `URL` to match
        * `patterns`: the iterable of `(match_to,arg)` 2-tuples
        * `extra`: an optional mapping to be passed to the match function

        Each yielded match is a `SiteMapPatternMatch` instance
        with the following atttributes:
        * `sitemap`: `self`
        * `pattern_test`: the pattern's first component, used for the test
        * `pattern_arg`: the pattern's second component, used by the caller to produce some result
        * `match`: the match object returned from the match function
        * `mapping`: a mapping of values gleaned during the match

        This implementation expects all the patterns to be
        `(match_to,arg)` 2-tuples, where `match_to` is either
        `URLMatcher` instance or a `(domain_glob,path_re)` 2-tuple
        which can be promoted to a `URLMatcher`.
        The match function is the `URLMatcher`'s `.match` method.

        The match is a mapping returned from the match function.

        The mapping is a `dict` initialised as follows:
        1: with the following attributes of the `url`:
           `basename`, `cleanpath`, 'cleanrpath', `dirname`, `domain`,
           `hostname`, `netloc`, `path`, `port`, `scheme`.
        2: with `_=url.cleanrpath` and `__=hostname/cleanrpath`
        3: with the entries from `url.query_dict()`
        4: with the contents of the `match` mapping
        Later items overwrite earlier items where they conflict.
    '''
    for match_to, arg in patterns:
      matcher = URLMatcher.promote(match_to)
      if (match := matcher.match(url, extra=extra)) is not None:
        # start with the URL attributes
        mapping = {
            attr: getattr(url, attr)
            for attr in (
                'basename',
                'cleanpath',
                'cleanrpath',
                'dirname',
                'domain',
                'hostname',
                'netloc',
                'path',
                'port',
                'scheme',
            )
        }
        # set _ to the url.path, __ to histname/path
        mapping.update(
            _=url.cleanrpath,
            __=f'{url.hostname}/{url.cleanrpath}',
        )
        # overlay any URL query terms
        mapping.update(url.query_dict())
        # overlay any match results
        mapping.update(match)
        yield SiteMapPatternMatch(self, match_to, arg, match, mapping)

  def match(
      self,
      url: URL,
      patterns: Iterable,
      extra: Mapping | None = None,
  ) -> SiteMapPatternMatch | None:
    ''' Scan `patterns` for a match to `url`, returning the first
        match `SiteMapPatternMatch` from `self.matches()`
        or `None` if no match is found.
    '''
    for matched in self.matches(url, patterns, extra=extra):
      return matched
    return None

  @OBSOLETE('SiteMap.default_cache_key or cache_key_*')
  @promote
  def url_key(
      self,
      url: URL,
      extra: Mapping | None = None,
  ) -> str | None:
    ''' Return a string which is a persistent cache key for the
        supplied `url` within the context of this sitemap, or `None`
        for URLs which do not have a key i.e. should not be cached persistently.

        A site with semantic URLs might have keys like
        *entity_type*`/`*id*`/`*aspect* where the *aspect* was
        something like `html` or `icon` etc for different URLs
        associated with the same entity.

        This base implementation matches the patterns in `URL_KEY_PATTERNS`
        class attribute which is `()` for the base class.
    '''
    match = self.match(url, self.URL_KEY_PATTERNS, extra=extra)
    if not match:
      return None
    return match.format_arg(extra=extra)

  @uses_pilfer
  ##@typechecked # we don't import Pilfer (circular)
  def content_prefetch(
      self,
      match: SiteMapPatternMatch,
      flow,
      content_bs: bs,
      *,
      P: "Pilfer",  # noqa: F821
  ):
    ''' The generic prefetch handler.

        This parses `content_bs` and queues URLs for prefetching
        based on the value of `match.pattern_arg`.

        The `match.pattern_arg` should be a list of strings (or a single string).
        The supported strings are:
        - `"hrefs"`: all the anchor `href` values
        - `"srcs"`: all the anchor `src` values
    '''
    rq = flow.request
    rsp = flow.response
    url = rq.url
    print("PREFETCH from", url)
    ct = content_type(rsp.headers)
    with Pfx("content_prefetch: %s: %s", ct.content_type, url):
      if ct is None:
        warning('no content-type')
        return
      # parse the content
      if ct.content_type == 'text/html':
        encoding = ct.params.get('charset') or 'utf8'
        soup = BeautifulSoup(content_bs, 'html.parser', from_encoding=encoding)
      # TODO: text/xml, for RSS etc
      else:
        soup = None
      url = URL(url, soup=soup)
      to_fetch = match.pattern_arg
      prefetcher = P.state.prefetcher
      # promote bare string to list
      if isinstance(to_fetch, str):
        to_fetch = [to_fetch]
      with P:
        for pre in to_fetch:
          with Pfx(pre):
            match pre:
              case 'hrefs' | 'srcs':
                print("PREFETCH", pre, "...")
                if soup is None:
                  warning("unoparsed")
                  return
                a_attr = pre[:-1]  # href or src
                for a in soup.find_all('a'):
                  ref = a.get(a_attr)
                  if not ref:
                    continue
                  absurl = url.urlto(ref)
                  print("PREFETCH, put", absurl)
                  prefetcher.put(
                      absurl, get_kw=dict(headers={'x-prefetch': 'no'})
                  )
              case _:
                warning("unhandled prefetch arg")

  def update_mapping_from_meta(
      self,
      te: str | tuple | Mapping[str, Any],
      flowstate: FlowState,
      **update_kw,
  ):
    ''' Update a mapping from `flowstate.meta`.
        Return the mapping.

        If `te` is a string or tuple, obtain the mapping from `self[te]`.

        This applies the following updates:
        - `meta`: `flowstate.meta.tags`
        - `properties`: `flowstate.meta.properties`
        - `opengraph.*`: from properties commencing with `og:`
        - *type*`.*`: from properties commencing with *type*`:`
          where *type* comes from the `og:type` property
    '''
    # promote a SiteMap index to a SiteEntity
    if isinstance(te, (str, tuple)):
      te = self[te]
    # stash the raw meta and properties
    te["meta"] = flowstate.meta.tags.as_dict()
    te["properties"] = flowstate.meta.properties.as_dict()
    og = flowstate.opengraph_tags
    te.update(**og)
    og_type = og.get('opengraph.type')
    if og_type:
      # if there's a type, add the type:* properties also
      type_prefix = f'{og_type}:'
      te.update(
          **{
              f'{og_type}.{cutprefix(k,type_prefix)}': v
              for k, v in te.properties.items()
              if k.startswith(type_prefix)
          }
      )
    # apply whatever update_kw were supplied
    te.update(**update_kw)
    return te

  @classmethod
  @OBSOLETE
  @promote
  def entity_key(cls, flowstate: FlowState, **match) -> str | None:
    ''' Given a URL or `FlowState`, return the name of its primary `TagSet`.
        Return `None` if there is none.
    '''
    return None

  @on
  @promote
  def patch_soup_toolbar(
      self,
      flowstate: FlowState,
      match: Mapping[str, Any] | None = None,
      soup=None,
  ):
    # a list of tags for the toolbar
    tags = []
    for link, link_tags in flowstate.links.items():
      for tag in link_tags:
        if tag.attrs.get('type') == "application/rss+xml":
          try:
            href = tag.attrs['href']
          except KeyError:
            warning("no href in %s", tag)
          else:
            widget = BS4Tag(name='a', attrs=dict(href=href))
            widget.string = "RSS"
            tags.append(widget)
    if tags:
      # place a toolbar above the body content
      body = soup.body
      toolbar = BS4Tag(name='div')
      toolbar.append("Toolbar: ")
      for i, widget in enumerate(tags):
        if i > 0:
          toolbar.append(", ")
        toolbar.append(widget)
      toolbar.append('.')
      body.insert(0, BS4Tag(name='br'))
      body.insert(0, toolbar)
    return soup

  def find(self, *criteria, **crit_kw):
    ''' Find `SiteEntity` instances matching criteria.
        Promote a `SiteEntity` subclass in `criteria` into a `name~`
        criterion.
    '''
    # convert a SiteEntity class into a name~ criterion
    criteria = [
        f'name~{self.TYPE_ZONE}.{criterion.TYPE_SUBNAME}.*'
        if isinstance(criterion, type) and issubclass(criterion, SiteEntity)
        else criterion for criterion in criteria
    ]
    return super().find(*criteria, **crit_kw)

  @popopts(
      f=('force', 'Force refresh of entities even if not stale.'),
      l=('long_mode', 'Long Mode.'),
      r=('recurse', 'Recurse into related entities.'),
  )
  def cmd_ls(self, argv):
    ''' Usage: {cmd} subname [type [key...]]
          List entities for this SiteMap.
    '''
    options = self.options
    force = options.force
    long_mode = options.long_mode
    recurse = options.recurse
    runstate = options.runstate
    if not argv:
      # list subnames - entity types
      for subname in sorted(set(subname for subname, type_key in self.keys())):
        print(subname)
        if long_mode:
          printt(
              *(
                  [
                      entity.type_key,
                      entity.get('fullname') or entity.get('title', ''),
                  ] for entity in map(
                      lambda key: self[key],
                      sorted(self.keys(subname=subname))
                  )
              ),
              indent='  '
          )
      return 0
    subname = argv.pop(0)
    if argv:
      ok = True
      for type_key in argv:
        if '.' in type_key:
          warning("invalid dot in type_key %r", type_key)
          ok = False
      if not ok:
        raise GetoptError('invalid type keys')
      keys = [(subname, key) for key in argv]
    else:
      # list all entities of this type
      keys = sorted(self.keys(subname=subname))
      print("self.keys ->", *map(r, keys))
    Q = ListQueue((self[key] for key in keys), unique=lambda ent: ent.name)
    for ent in self.updated_entities(Q, force=force):
      runstate.raiseif()
      print(ent.name)
      if long_mode:
        printt(*sorted(ent.items()), indent='  ')
      if recurse:
        for (attr, subents) in ent.related():
          print(attr, '->', [subent.name for subent in subents])

# expose the @on and @grok_entity_page decorators globally
on = SiteMap.on
grok_entity_page = SiteMap.grok_entity_page

# Some presupplied site maps.

@dataclass
class DocSite(SiteMap):
  ''' A general purpose doc site map with keys for `.html` and `.js` URLs
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

@dataclass
class Wikipedia(SiteMap):
  ''' The SiteMap for `wikipedia.org'.
  '''

  TYPE_ZONE = 'wikipedia'

  URL_KEY_PATTERNS = [
      # https://en.wikipedia.org/wiki/Braille
      (
          (
              '*.wikipedia.org',
              r'/wiki/(?P<title>[^:/]+)$',
          ),
          'wiki/{title}',
      ),
      # https://upload.wikimedia.org/wikipedia/commons/thumb/3/35/Carbonate-outcrops_world.jpg/620px-Carbonate-outcrops_world.jpg
      (
          (
              'upload.wikipedia.org',
              r'/wikipedia/commons/(?<subpath>.*\.(jpg|gif|png))$',
          ),
          'wiki/commons/{subpath}',
      ),
  ]

  @promote
  def url_key(self, url: URL, extra: Mapping | None = None) -> str | None:
    ''' Include the domain name language in the URL key.
    '''
    key = super().url_key(url, extra=extra)
    if key is not None:
      key = f'{cutsuffix(url.hostname, ".wikipedia.org")}/{key}'
    return key

@dataclass
class DockerIO(SiteMap):

  TYPE_ZONE = 'dockerio'

  # https://registry-1.docker.io/v2/linuxserver/ffmpeg/blobs/sha256:6e04116828ac8a3a5f3297238a6f2d0246440a95c9827d87cafe43067e9ccc5d
  @on(
      'registry-*.docker.io',
      r'/v2/.*/blobs/[^/]+:[^/]+$',
      cache_key='blobs/{__}',
  )
  def cache_key_image_blob(self, flowstate: FlowState, match: TagSet) -> str:
    return match['cache_key']
