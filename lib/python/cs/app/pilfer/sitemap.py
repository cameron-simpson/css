#!/usr/bin/env python3

''' Base class for site maps.
'''

from collections import ChainMap, namedtuple
from dataclasses import dataclass
from fnmatch import fnmatch
from functools import cached_property
import re
from types import SimpleNamespace
from typing import Iterable, Mapping, Optional

from cs.binary import bs
from cs.deco import decorator, promote, Promotable
from cs.lex import cutsuffix, r
from cs.logutils import warning
from cs.pfx import Pfx, pfx_call, pfx_method
from cs.rfc2616 import content_type
from cs.tagset import TagSet
from cs.urlutils import URL

from bs4 import BeautifulSoup
from mitmproxy.flow import Flow
from typeguard import typechecked

def default_Pilfer():
  from .pilfer import Pilfer
  return Pilfer.default()

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

@dataclass
class OnState(SimpleNamespace, Promotable):

  ##@trace
  @promote
  def __init__(self, **ns_kw):
    ''' Initialise `self` from the keyword parameters.

        The end result is that we have `.flow`, `.request`,
        `.response` and `.url` attributes, which may be `None`
        if omitted.
        Some of these have computed defaults if omitted:
        - `.request` and `.response` are obtained from `.flow`
        - `.url` is obtained from `.request.url`
    '''
    super().__init__(**ns_kw)
    extra_attrs = self.__dict__.keys() - ('flow', 'request', 'response', 'url')
    if extra_attrs:
      raise ValueError(f'unexpected attributes supplied: {extra_attrs}')

  def __str__(self):
    attr_listing = ",".join(
        f'{attr}={value}' for attr, value in self.__dict__.items()
    )
    return f'{self.__class__.__name__}({attr_listing})'

  __repr__ = __str__

  def __getattr__(self, attr):
    if attr[0].isalpha():
      return getattr(self.url, attr)
    raise AttributeError(f'{self.__class__.__name__}.{attr}')

  @classmethod
  def from_str(cls, url_s: str):
    ''' Promote a `str` URL to a `OnState`.
    '''
    return cls(url=URL(url_s))

  @classmethod
  def from_URL(cls, url: URL):
    ''' Promote a `URL` URL to a `OnState`.
    '''
    return cls(url=url)

  @classmethod
  def from_Flow(cls, flow: Flow):
    ''' Promote a `Flow` to a `OnState`.
    '''
    return cls(flow=flow)

  @cached_property
  def url(self) -> URL:
    X("%s: .url()...", self)
    return URL(self.response.url)

  @cached_property
  def response_headers(self):
    ''' Cached response headers.
        Does a `HEAD` of the URL if there is no `self.response`;
        this sets `self.response` as a side effect.
    '''
    if not self.response:
      self.response = self.url.HEAD()
    return self.response.headers

  @cached_property
  def content_type(self) -> str:
    ''' The base `Content-Type`, eg `'text/html'`.
        Does a `HEAD` of the URL if there is no `self.response`.
    '''
    return content_type(self.repsonse_headers)

  @cached_property
  @typechecked
  def content(self) -> str:
    ''' The text content of the URL.
        Does a `GET` of the URL if there is no `self.response.content`.
    '''
    content = self.response and self.response.content
    if content is None:
      content = self.url.GET().content
    return content

  @cached_property
  def soup(self):
    ''' A `BeautifulSoup` of `self.content`.
    '''
    return BeautifulSoup(self.content, 'html.parser')

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
  ''' A base class for site maps.

      A `Pilfer` instance obtains its site maps from the `[sitemaps]`
      clause in the configuration file, see the `Pilfer.sitemaps`
      property for specifics.

      Example:

          docs.python.org = docs:cs.app.pilfer.sitemap:DocSite
          docs.mitmproxy.org = docs
          *.readthedocs.io = docs
  '''

  name: str

  URL_KEY_PATTERNS = ()

  @classmethod
  def from_str(
      cls, sitemap_name: str, *, P: Optional["Pilfer"] = None
  ) -> "SiteMap":
    ''' Return the `SiteMap` instance known as `sitemap_name` in the ambient `Pilfer` instance.
    '''
    P = P or default_Pilfer()
    for name, sitemap in P.sitemaps:
      if name == sitemap_name:
        return sitemap
    raise ValueError(
        f'{cls.__name__}.from_str({sitemap_name!r}): unknown sitemap name'
    )

  @staticmethod
  @decorator
  def on(method, *patterns, **patterns_kw):
    ''' A decorator for handler methods.

        Its parameters indicate the conditions under which this method
        will be fired; all must be true.
        Each use of the decorator appends its combination of
        conditions on the method's `.on_conditions` attribute.

        Other parameters have the following meanings:
        - the values in `patterns` may be strings or callables;
          strings are considered globs to match against the hostname
          if they contain no slashes or regular expressions to match
          against the URL path otherwise - a leading slash anchors
          the regexp against the start of the path;
          callables are called with the `flow` and may make any test against it
        - the `patterns_kw` name various attributes of the `flow` or
          the `flow.response` or `flow.request` (when there's no response
          yet); their values may be strings or callables

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
    assert not patterns_kw, "not yet implemented"
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
                      lambda on_state:
                      (m := pfx_call(regexp.match, on_state.url.path)
                       ) and m.groupdict()
                  )
              )(
                  regexp
              )
            else:
              # match anywhere
              condition = (
                  lambda regexp: (
                      lambda on_state:
                      (m := pfx_call(regexp.search, on_state.url.path)
                       ) and m.groupdict()
                  )
              )(
                  regexp
              )
          else:
            # filename glob on the URL host
            condition = (
                lambda pattern: (
                    lambda on_state:
                    pfx_call(fnmatch, on_state.url.hostname, pattern)
                )
            )(
                pattern
            )
        else:
          raise RuntimeError
        assert condition is not None
        conditions.append(condition)
    try:
      cond_attr = method.on_conditions
    except AttributeError:
      cond_attr = method.on_conditions = []
    cond_attr.append(conditions)
    return method

  @classmethod
  @promote
  def on_matches(cls, on_state: OnState):
    ''' A generator yielding methods matched by `on_state`.
    '''
    for method_name in dir(cls):
      try:
        method = getattr(cls, method_name)
      except AttributeError:
        continue
      try:
        conditions = method.on_conditions
      except AttributeError:
        continue
      matched = TagSet()
      for condition in conditions:
        for test in condition:
          with Pfx("on_matches: test %r vs %s", method_name, test):
            try:
              test_result = test(on_state)
            except Exception as e:
              warning("exception in test: %s", e)
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
        # no test failed
        yield method, matched
        continue

  @pfx_method
  @promote
  def grok(self, on_state: OnState, P: "Pilfer" = None):
    ''' Call each method matching `on_state` with the `on_state`.
        Return a list of `(method,match_tags,grokked)` 3-tuples being:
        - a reference to the fired grok method
        - the match `TagSet` derived from matching that method
        - the result of calling the method, often a `TagSet`
        Exceptions are gathered and, if any, an `ExceptionGroup` is raised
        (unless there's just one, it which case it is raised directly).
    '''
    P = P or default_Pilfer()
    results = []
    excs = []
    for method, match_tags in self.on_matches(on_state):
      try:
        grokked = method(self, on_state, match_tags)
      except Exception as e:
        warning(f'failed call to {method=}: {e}')
        excs.append(e)
      else:
        results.append((method, match_tags, grokked))
    if excs:
      if len(excs) == 1:
        raise excs[0]
      else:
        raise ExceptionGroup(f'exceptions grokking {on_state=}', excs)
    return results

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
        mapping = dict(
            (
                (attr, getattr(url, attr)) for attr in (
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
            )
        )
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

  @typechecked
  def content_prefetch(
      self,
      match: SiteMapPatternMatch,
      flow,
      content_bs: bs,
      *,
      P: Optional = None,
  ):
    ''' The generic prefetch handler.

        This parses `content_bs` and queues URLs for prefetching
        based on the value of `match.pattern_arg`.

        The `match.pattern_arg` should be a list of strings (or a single string).
        The supported strings are:
        - `"hrefs"`: all the anchor `href` values
        - `"srcs"`: all the anchor `src` values
    '''
    from .pilfer import Pilfer
    P = P or default_Pilfer()
    if not isinstance(P, Pilfer):
      print("NO PILFER")
      breakpoint()
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
      '/ .css .gif .html .ico .jpg .js .png .svg .webp .woff2'.split()
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
