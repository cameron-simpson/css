#!/usr/bin/env python3

''' Base class for site maps.
'''

from collections import ChainMap, defaultdict, namedtuple
from dataclasses import dataclass
from datetime import datetime
from fnmatch import fnmatch
from functools import cached_property
from itertools import zip_longest
import re
from types import SimpleNamespace as NS
from typing import Any, Callable, Iterable, List, Mapping, Optional, Tuple

from cs.binary import bs
from cs.deco import decorator, default_params, fmtdoc, promote, Promotable
from cs.lex import cutsuffix, get_nonwhite, r, skipwhite
from cs.logutils import warning
from cs.pfx import Pfx, pfx_call, pfx_method
from cs.resources import MultiOpenMixin
from cs.rfc2616 import content_encodings, content_type
from cs.tagset import TagSet
from cs.seq import ClonedIterator
from cs.threads import HasThreadState, ThreadState
from cs.urlutils import URL

from bs4 import BeautifulSoup
from bs4.element import Tag as BS4Tag
from mitmproxy.flow import Flow
import requests

# The default HTML parser chosen by BeautifulSoup.
BS4_PARSER_DEFAULT = 'lxml'  # vs eg 'html5lib'

def default_Pilfer():
  ''' Obtain the ambient `Pilfer` instance via a late import.
  '''
  from .pilfer import Pilfer
  return Pilfer.default()

@decorator
def uses_pilfer(func):
  ''' Set the optional `P:Pilfer` parameter via a late import.
  '''

  def func_with_Pilfer(*a, P: "Pilfer" = None, **kw):
    if P is None:
      P = default_Pilfer()
    return func(*a, P=P, **kw)

  return func_with_Pilfer

def parse_img_srcset(srcset, offset=0) -> Mapping[str, List[str]]:
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
      extra: Optional[Mapping] = None,
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

class FlowState(NS, MultiOpenMixin, HasThreadState, Promotable):
  ''' An object with some resemblance to a `mitmproxy` `Flow` object
      with various utility properties and methods.
      It may be initialised from lesser objects such as just a URL.

      This is intented as a common basis for working in a `mitmproxy`
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

  # NB: no __getattr__, it preemptys @cached_property

  @classmethod
  def from_str(cls, url_s: str):
    ''' Promote a `str` URL to a `FlowState`.
    '''
    return cls(url=URL(url_s))

  @classmethod
  def from_URL(cls, url: URL):
    ''' Promote a `URL` URL to a `FlowState`.
    '''
    return cls(url=url)

  @classmethod
  def from_Flow(cls, flow: Flow):
    ''' Promote a `Flow` to a `FlowState`.
    '''
    return cls(
        flow=flow,
        request=flow.request,
        response=flow.response,
        url=flow.request.url,
    )

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
  def content_encodings(self) -> List[str]:
    ''' A list of the transforming encodings named in the `Content-Encoding` response header.
          The encoding `'identity'` is discarded.
      '''
    return [
        enc for enc in content_encodings(self.response.headers)
        if enc != 'identity'
    ]

  @uses_pilfer
  @promote
  def GET(self, P: "Pilfer", url: URL = None, **rq_kw) -> requests.Response:
    ''' Do a `Pilfer.GET` of `self.url` and return the `requests.Response`.
        This also updates `self.request` and `self.response`, sets
        `self.iterable_content`, and clears `self.content` and
        `self.soup` (meaning they will be rederived on next access).
    '''
    if url is None:
      url = self.url
    else:
      self.url = url
    rsp = self.response = P.GET(url, stream=True, **rq_kw)
    # forget any cached derived values
    self.clear('content', 'text', 'soup')
    self.request = rsp.request
    self.url = url
    # TODO: find out if the requests.response has been decoded/uncompressed
    self.iterable_content = ClonedIterator(rsp.iter_content(chunk_size=None))
    return rsp

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
    # TODO: need to decode?
    return [self.flow.response.content]

  @cached_property
  def content(self) -> bytes:
    ''' The response content, concatenated as a single `bytes` instance
        from `self.iterable_content`.
    '''
    content = b''.join(self.iterable_content)
    self.set_content(content)  # to clear the derived attributes
    return content

  def set_content(self, content: bytes):
    ''' Set `self.content` and forget the `text` and `soup` attributes.
    '''
    self.content = content
    self.clear('iterable_content', 'text', 'soup')

  @cached_property
  def text(self) -> str:
    ''' The text content of the URL.
    '''
    # assume UTF-8 if not specified
    charset = self.content_charset or 'utf-8'
    assert not self.content_encodings
    text = self.content.decode(charset)
    self.url.text = text
    return text

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
        - `.tags`: the `meta` tags with `name` attributes
        - `.properties`: the `meta` tags with `property` attributes
        - `.http_equiv`: the `meta` tags with `http-equiv` attributes
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
  def links(self):
    ''' A `defaultdict(list)` mapping `link` `rel=` values a list of `link` tags.
    '''
    links_by_rel = defaultdict(list)
    soup = self.soup
    if soup is not None:
      for link in soup.find_all('link'):
        for rel in link.attrs.get('rel', ('',)):
          links_by_rel[rel].append(link)
    return links_by_rel

uses_flowstate = default_params(flowstate=FlowState.default)

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

  def format_arg(self, extra: Optional[Mapping] = None) -> str:
    ''' Treat `self.pattern_arg` as a format string and format it
        using `self.mapping` and `extra`.
    '''
    return self.pattern_arg.format_map(ChainMap(self.mapping, extra or {}))

@dataclass
class SiteMap(Promotable):
  ''' A base dataclass for site maps.

      A `SiteMap` data class embodies domain specific knowledge about a
      particular website or collection of websites.

      A `Pilfer` instance obtains its site maps from the `[sitemaps]`
      clause in the configuration file, see the `Pilfer.sitemaps`
      property for specifics.

      A pilferrc configuration example:

          [sitemaps]
          docs.python.org = docs:cs.app.pilfer.sitemap:DocSite
          docs.mitmproxy.org = docs
          *.readthedocs.io = docs

      This says that websites whose domain matches `docs.python.org`,
      `docs.mitmproxy.org` or the filename glob `*readthedocs.io`
      are all associated with the `SiteMap` referred to as `docs`
      whose definition comes from the `DocSite` class from the module
      `cs.app.pilfer.sitemap`. The `DocSite` class will be a subclass
      of this `SiteMap` base class.

      `SiteMap`s have a few class attributes:
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

  name: str

  URL_KEY_PATTERNS = ()

  @classmethod
  @uses_pilfer
  def from_str(cls, sitemap_name: str, *, P: "Pilfer") -> "SiteMap":
    ''' Return the `SiteMap` instance known as `sitemap_name` in the ambient `Pilfer` instance.
    '''
    for name, sitemap in P.sitemaps:
      if name == sitemap_name:
        return sitemap
    raise ValueError(
        f'{cls.__name__}.from_str({sitemap_name!r}): unknown sitemap name'
    )

  @staticmethod
  @decorator
  def on(method, *patterns, **patterns_kw):
    ''' A decorator for handler methods which specifies conditions
        which must match for this handler to be called.
        This decorator may be applied multiple times
        if the handler method should match various flows.

        Its parameters indicate the conditions under which this method
        will be fired; all must be true.
        Each use of the decorator appends its conjunction of
        conditions on the method's `.on_conditions` attribute.

        Other parameters have the following meanings:
        - the values in `patterns` may be strings or callables;
          strings are considered globs to match against the hostname
          if they contain no slashes or regular expressions to match
          against the URL path otherwise - a leading slash anchors
          the regexp against the start of the path;
          callables are called with the `flow` and may make any test against it
        - the `patterns_kw` is a mapping of `match_kw` key or `flowstate` attribute
          to either the required value or a callable to test that value


        Example:

            @on('docs.pytho.org', '/3/library/(?P<module_name>[^/]+).html$')
            def cache_module_html(
                self,     # the SiteMap instance
                url,      # the URL
                flow,     # the mitmproxy Flow
                P:Pilfer, # the current Pilfer context
            ):
                P.cache(flow, '{flow.requs
    '''
    conditions = []
    for pattern in patterns:
      with Pfx(f'pattern={r(pattern)}'):
        condition = None
        if isinstance(pattern, str):
          if '/' in pattern:
            # a path match
            regexp = pfx_call(re.compile, pattern)
            if pattern.startswith('/'):
              # match at the start of the path
              condition = (
                  lambda regexp: (
                      lambda flowstate:
                      (m := pfx_call(regexp.match, flowstate.url.path)
                       ) and m.groupdict()
                  )
              )(
                  regexp
              )
            else:
              # match anywhere
              condition = (
                  lambda regexp: (
                      lambda flowstate:
                      (m := pfx_call(regexp.search, flowstate.url.path)
                       ) and m.groupdict()
                  )
              )(
                  regexp
              )
          else:
            # filename glob on the URL host
            # this indirection is to avoid the lambda pattern binding to the closure
            condition = (
                lambda pattern: (
                    lambda flowstate:
                    pfx_call(fnmatch, flowstate.url.hostname, pattern)
                )
            )(
                pattern
            )
        else:
          raise RuntimeError
        assert condition is not None
        conditions.append(condition)
    conditions.extend(patterns_kw.items())
    try:
      cond_attr = method.on_conditions
    except AttributeError:
      cond_attr = method.on_conditions = []
    cond_attr.append(conditions)
    return method

  @classmethod
  @pfx_method
  @promote
  def on_matches(
      cls,
      flowstate: FlowState,
      **match_kw,
  ) -> Iterable[Tuple[Callable, TagSet]]:
    ''' A generator yielding `(method,matched)` 2-tuples for  matched
        by `flowstate` and `match_kw`, being the matching method
        and a `TagSet` of values obtained during the match test.

        The matching methods are identified by consulting the
        conditions in the method's `.on_conditions` attribute, a
        list of conjunctions normally defined by applying the `@on`
        decorator to the method.
        A `(method,matched)` 2-tuple is yielded for each matching conjunction.

        Note that this means the same methods may be yielded multiple
        times if different conjunctions match (eg multiple matching
        `@on` decorators); this is because each condition may provide
        different `matched` match results.
    '''
    for method_name in dir(cls):
      try:
        method = getattr(cls, method_name)
      except AttributeError:
        continue
      try:
        conditions = method.on_conditions
      except AttributeError:
        # no conditions, skip
        continue
      matched = TagSet()
      for conjunction in conditions:
        with Pfx("match %r", conjunction):
          for condition in conjunction:
            with Pfx("test %r", condition):
              try:
                # a 2-tuple of name and value/value_test()?
                test_name, test_value = condition
              except (TypeError, ValueError):
                with Pfx("on_matches: test %r vs %s", method_name, condition):
                  try:
                    test_result = condition(flowstate)
                  except Exception as e:
                    warning("exception in condition: %s", e)
                    raise
                  # test ran, examine result
                  if test_result is None or test_result is False:
                    # failure
                    break
                  # success
                  if test_result is not True:
                    # should be a mapping, update the matched TagSet
                    for k, v in test_result.items():
                      matched[k] = v
              else:
                # a 2-tuple of name and value/value_test()?
                try:
                  value = match_kw[test_name]
                except KeyError:
                  try:
                    value = getattr(flowstate, test_name)
                  except AttributeError as e:
                    warning(
                        "no %s.%s attribute: %s", flowstate.__class__.__name__,
                        test_name, e
                    )
                    # consider the test failed
                    break
                  if callable(value):
                    if not value(flowstate):
                      break
                  elif value != test_value:
                    break
          else:
            # no test failed, this is a match
            yield method, matched

  @pfx_method
  @promote
  def run_matches(
      self,
      flowstate: FlowState,
      flowattr: Optional[str] = None,
      methodglob: Optional[str] = None,
      **match_kw,
  ) -> Iterable[Tuple[Callable, TagSet, Any]]:
    ''' Run all the methods in this `SiteMap` whose `.on_conditions`
        match `flowstate` and ``match_kw`, as matched by `SiteMap.on_matches`.
        Yield `(method,match_tags,result)` 3-tuples from each method called.

        Parameters:
        * `flowstate`: the `FlowState` on which to match
        * `flowattr`: an optional attribute name of the `flowstate`
        * `methodglob`: an optional filename glob constraining the chosen method names
        * `match_kw`: the `on_match` keyword arguments which must match

        Each `method` is called as `method(self,flowstate,match_tags)`
        where `method` and `match_tags` were yielded from
        `on_matches(flowstate,**match_kw)`.

        If `flowattr` is not `None`, `getattr(flowstate,flowattr)`
        is passed as an additional positional parameter and if the
        method result is not `None` then the result is set as an
        updated value on `flowstate`.
    '''
    for method, match_tags in self.on_matches(flowstate, **match_kw):
      if methodglob is not None and not fnmatch(method.__name__, methodglob):
        continue
      with Pfx("call %s", method.__qualname__):
        try:
          if flowattr is None:
            result = method(self, flowstate, match_tags)
          else:
            attrvalue = pfx_call(getattr, flowstate, flowattr)
            result = method(self, flowstate, match_tags, attrvalue)
        except Exception as e:
          warning("%s.%s: url=%s: %s", self, method.__name__, flowstate.url, e)
        else:
          if flowattr is not None and result is not None:
            pfx_call(setattr, flowstate, flowattr, result)
          yield method, match_tags, result

  @pfx_method
  @promote
  def grok(
      self,
      flowstate: FlowState,
      flowattr: Optional[str] = None,
      **run_match_kw,
  ) -> Iterable[Tuple[Callable, TagSet, Any]]:
    ''' A generator to grok the fullness of this `flowstate`, deriving information.
        Usually this involves consulting the URL contents.
        This is a shim for `SiteMap.run_matches` calling any matching
        methods named `grok_*`.
        Yield `(method,match_tags,result)` 3-tuples from each method called.
        Usually the `result` is a `TagSet`.
    '''
    yield from self.run_matches(flowstate, flowattr, 'grok_*', **run_match_kw)

  def matches(
      self,
      url: URL,
      patterns: Iterable,  # [Tuple[Tuple[str, str], Any]],
      extra: Optional[Mapping] = None,
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
        mapping.update(url.query_dict())
        mapping.update(match)
        yield SiteMapPatternMatch(self, match_to, arg, match, mapping)

  def match(
      self,
      url: URL,
      patterns: Iterable,
      extra: Optional[Mapping] = None,
  ) -> SiteMapPatternMatch | None:
    ''' Scan `patterns` for a match to `url`, returning the first
        match `SiteMapPatternMatch` from `self.matches()`
        or `None` if no match is found.
    '''
    for matched in self.matches(url, patterns, extra=extra):
      return matched
    return None

  @promote
  def url_key(
      self,
      url: URL,
      extra: Optional[Mapping] = None,
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
      P: "Pilfer",
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

  @staticmethod
  @uses_pilfer
  def update_tagset_from_meta(
      te: str | TagSet,
      flowstate: FlowState,
      *,
      P: "Pilfer",
      **update_kw,
  ):
    ''' Update a `TagSet` from `flowstate.meta`.
        Return the `TagSet`.

        If `te` is a string, obtain the `TagSet` from `P.sqltags[te]`,
        thus the need to return the `TagSet`.

        This sets the `TagSet`'s `.properties` to
        `flowstate.meta.properties` and the `.meta` to
        `flowstate.meta.tags`.
    '''
    # promote a tagset name to an SQLTagSet from P.sqltags
    if isinstance(te, str):
      te = P.sqltags[te]
    te.meta = flowstate.meta.tags
    te.properties = flowstate.meta.properties
    te.update(**update_kw)
    return te

  @classmethod
  @promote
  def entity_key(cls, flowstate: FlowState, **match_tags) -> str | None:
    ''' Given a URL or `FlowState`, return the name of its primary `TagSet`.
        Return `None` if there is none.
    '''
    return None

  @on
  @promote
  def grok_default(
      self,
      flowstate: FlowState,
      match_tags: Optional[Mapping[str, Any]] = None,
  ) -> TagSet:
    ''' A default low level grok function
        which stores a page's meta tags and properties
        on the page's primary entity.
        Returns the entity, a `TagSet`.
    '''
    te_key = self.entity_key(flowstate, **(match_tags or {}))
    if te_key is None:
      # just return the metadata
      return TagSet(
          meta=flowstate.meta.tags,
          properties=flowstate.meta.properties,
      )
    te = self.update_tagset_from_meta(
        te_key,
        flowstate,
    )
    return te

  @on
  @promote
  def patch_soup_toolbar(
      self,
      flowstate: FlowState,
      match_tags: Optional[Mapping[str, Any]] = None,
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

# expose the @on decorator globally
on = SiteMap.on

# Some presupplied site maps.

@dataclass
class DocSite(SiteMap):
  ''' A general purpose doc site map with keys for `.html` and `.js` URLs
      along with several other common extensions.
  '''

  # the URL path suffixes which will be cached
  CACHE_SUFFIXES = tuple(
      [
          '/', '.css', '.gif', '.html', '.ico', '.jpg', '.js', '.png', '.svg',
          '.webp', '.woff2'
      ]
  )

  URL_KEY_PATTERNS = [
      (
          # https://www.crummy.com/software/BeautifulSoup/bs4/doc/
          (
              None,
              r'.*(/|\\' + '|\\'.join(CACHE_SUFFIXES) + ')',
          ),
          '{__}',
      ),
  ]

@dataclass
class MiscDocsSite(DocSite):
  ''' A general purpose doc site map with keys for `.html` and `.js` URLs
      along with several other common extensions.
  '''

  URL_KEY_PATTERNS = [
      (
          # https://www.crummy.com/software/BeautifulSoup/bs4/doc/
          (
              'www.crummy.com',
              r'/software/BeautifulSoup/bs4/doc/',
          ),
          '{__}',
      ),
  ]

@dataclass
class Wikipedia(SiteMap):

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
  def url_key(self, url: URL, extra: Optional[Mapping] = None) -> str | None:
    ''' Include the domain name language in the URL key.
    '''
    key = super().url_key(url, extra=extra)
    if key is not None:
      key = f'{cutsuffix(url.hostname, ".wikipedia.org")}/{key}'
    return key

@dataclass
class Docker(SiteMap):

  URL_KEY_PATTERNS = [
      # https://registry-1.docker.io/v2/linuxserver/ffmpeg/blobs/sha256:6e04116828ac8a3a5f3297238a6f2d0246440a95c9827d87cafe43067e9ccc5d
      (
          (
              'registry-*.docker.io',
              r'/v2/.*/blobs/[^/]+:[^/]+$',
          ),
          'blobs/{__}',
      ),
  ]
