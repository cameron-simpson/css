#!/usr/bin/env python3
#
# URL related utility functions and classes.
# - Cameron Simpson <cs@cskk.id.au> 26dec2011
#

from collections import namedtuple
from contextlib import contextmanager
from functools import cached_property
from heapq import heappush, heappop
import os
import os.path
import re
import sys
from typing import Iterable, Union

from netrc import netrc
from string import whitespace
from threading import RLock
from urllib.request import (
    HTTPError, URLError, HTTPPasswordMgrWithDefaultRealm
)
from urllib.parse import parse_qs, urlparse, urljoin as up_urljoin

from bs4 import BeautifulSoup
try:
  try:
    from lxml import etree
  except ImportError:
    import xml.etree.ElementTree as etree
except ImportError:
  try:
    if sys.stderr.isatty():
      print(
          "%s: warning: cannot import lxml for use with bs4" % (__file__,),
          file=sys.stderr
      )
  except AttributeError:
    pass
import requests
from typeguard import typechecked

from cs.deco import promote, Promotable
from cs.excutils import unattributable
from cs.lex import FormatableMixin, parseUC_sAttr, r
from cs.logutils import debug, error, warning, exception
from cs.pfx import Pfx, pfx_call
from cs.rfc2616 import datetime_from_http_date
from cs.seq import skip_map
from cs.threads import locked, ThreadState, HasThreadState

__version__ = '20231129-post'

DISTINFO = {
    'description':
    "convenience functions for working with URLs",
    'keywords': ["python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'html5lib',
        'lxml',
        'beautifulsoup4',
        'cs.lex',
        'cs.logutils',
        'cs.rfc2616',
        'cs.threads',
        'cs.obj',
    ],
}

##from http.client import HTTPConnection
##putheader0 = HTTPConnection.putheader
##def my_putheader(self, header, *values):
##  for v in values:
##    X("HTTPConnection.putheader(%r): value=%r", header, v)
##  return putheader0(self, header, *values)
##HTTPConnection.putheader = my_putheader

def urljoin(url, other_url):
  ''' This is `urllib.parse.urljoin` after coercing both arguments to `str`.
  '''
  return up_urljoin(str(url), str(other_url))

class URL(HasThreadState, FormatableMixin, Promotable):
  ''' Utility class to do simple stuff to URLs, subclasses `str`.
  '''

  # Thread local stackable class state
  context = ThreadState(
      referer=None,
      user_agent=None,
      opener=None,
      retry_delay=3,
  )

  @typechecked
  def __init__(self, url_s: str, referer=None, soup=None, text=None):
    ''' Initialise the `URL` from the URL string `url_s`.
    '''
    self.url_s = str(url_s)
    self._lock = RLock()
    self._parts = None
    self.flush()

  def __str__(self):
    return self.url_s

  def __repr__(self):
    return f'{self.__class__.__name__}:{self.url_s!r}'

  def isabs(self):
    ''' Test whether this `URL` is absolute, having a hostname and
        a path commencing with `'/'`.
    '''
    return bool(self.hostname) and self.path.startswith('/')

  @property
  def short(self):
    ''' A shortened form of the URL for use in messages.
    '''
    path = self.path
    if len(path) <= 32:
      shortpath = path
    else:
      shortpath = f'{path[:15]}...{path[-14:]}'
    return f'{self.hostname}{shortpath}'

  def flush(self):
    ''' Forget all cached content.
    '''
    for attr in 'GET_response', 'HEAD_response', 'parsed':
      try:
        delattr(self, attr)
      except AttributeError:
        pass

  def __getattr__(self, attr):
    ''' Ad hoc attributes.
        Upper case attributes named "FOO" parse the text and find
        the (sole) node named "foo".
        Upper case attributes named "FOOs" parse the text and find
        all the nodes named "foo".
    '''
    k, plural = parseUC_sAttr(attr)
    if k:
      soup = self.soup
      nodes = soup.find_all(k.lower())
      if plural:
        return nodes
      node, = nodes
      return node
    # look up method on equivalent Unicode string
    raise AttributeError(f'{self.__class__.__name__}.{attr}')

  def format_kwargs(self):
    ''' Return a dict for use with `FormatableMixin.format_as()`.
    '''
    return dict(
        basename=self.basename or 'index.html',
        cleanpath=self.cleanpath,
        cleanrpath=self.cleanrpath,
        dirname=self.dirname,
        domain=self.domain,
        ext=self.ext,
        hostname=self.hostname,
        netloc=self.netloc,
        path=self.path,
        rpath=self.rpath,
        scheme=self.scheme,
        short=self.short,
        url=self.url_s,
    )

  @contextmanager
  def session(self, session=None):
    ''' Context manager yielding a `requests.Session`.
    '''
    if session is None:
      with requests.Session() as session:
        with self.session(session=session):
          yield session
    else:
      with self.context(session=session):
        yield session

  @cached_property
  @locked
  def GET_response(self):
    ''' The `requests.Response` object from a `GET` of this URL.
    '''
    return requests.get(self.url_s)

  @cached_property
  @locked
  def HEAD_response(self):
    ''' The `requests.Response` object from a `HEAD` of this URL.
    '''
    return requests.head(self.url_s)

  def exists(self) -> bool:
    ''' Test if this URL exists via a `HEAD` request.
    '''
    try:
      self.HEAD_response
    except HTTPError as e:
      if e.code == 404:
        return False
      raise
    return True

  @property
  @unattributable
  def content(self) -> bytes:
    ''' The decoded URL content as a `bytes`.
    '''
    return self.GET_response.content

  @cached_property
  @unattributable
  def text(self) -> str:
    ''' The URL decoded content as a string.
    '''
    return self.GET_response.text

  @property
  @unattributable
  def headers(self):
    ''' A `requests.Response` headers mapping.
    '''
    return self.HEAD_response.headers

  # TODO: use functions from cs.rfc2616? they do return an email.BaseHeader :-(
  @cached_property
  @unattributable
  def content_type_full(self):
    ''' The URL content MIME type from the `Content-Type` header.
    '''
    try:
      return self.headers['content-type']
    except KeyError as e:
      warning("%s.content_type_full", self)

  @cached_property
  @unattributable
  def content_type(self):
    ''' The base URL content MIME type from the `Content-Type` header.
        Example: `'text/html'`
    '''
    return self.content_type_full.split(';')[0].strip().lower()

  @property
  @unattributable
  def content_length(self):
    ''' The value of the Content-Length: header or `None`.
    '''
    try:
      length_s = self.headers['content-length']
    except KeyError:
      return None
    return int(length_s)

  @property
  @unattributable
  def last_modified(self):
    ''' The value of the Last-Modified: header as a UNIX timestamp, or None.
    '''
    value = self.headers.get('Last-Modified')
    if value is not None:
      # parse HTTP-date into datetime object
      dt_last_modified = datetime_from_http_date(value.strip())
      value = dt_last_modified.timestamp()
    return value

  @property
  @unattributable
  def content_transfer_encoding(self):
    ''' The URL content tranfer encoding.
    '''
    return self.headers.getencoding()

  @property
  @unattributable
  def domain(self):
    ''' The URL domain - the hostname with the first dotted component removed.
    '''
    hostname = self.hostname
    if not hostname or '.' not in hostname:
      warning("%s: no domain in hostname: %s", self, hostname)
      return ''
    return hostname.split('.', 1)[1]

  @cached_property
  @unattributable
  def soup(self):
    ''' The URL content parsed as HTML by BeautifulSoup.
    '''
    if self.content_type == 'text/html':
      parser_names = ('html5lib', 'html.parser', 'lxml', 'xml')
    else:
      parser_names = ('lxml', 'xml')
    soup = pfx_call(BeautifulSoup, self.text, 'lxml')  ## list(parser_names))
    return soup

  def feedparsed(self):
    ''' A parse of the content via the feedparser module.
    '''
    import feedparser
    return feedparser.parse(self.content)

  @cached_property
  @unattributable
  def xml(self):
    ''' An `ElementTree` of the URL content.
    '''
    return etree.XML(self.content.decode('utf-8', 'replace'))

  @cached_property
  def url_parsed(self):
    ''' The URL parsed by `urlparse.urlparse`.
        This is a `(scheme,netloc,path,params,query,fragment)` namedtuple.
    '''
    return urlparse(self.url_s)

  @property
  @unattributable
  def scheme(self):
    ''' The URL scheme as returned by `urlparse.urlparse`.
    '''
    return self.url_parsed.scheme

  @property
  @unattributable
  def netloc(self):
    ''' The URL netloc as returned by `urlparse.urlparse`.
    '''
    return self.url_parsed.netloc

  @property
  @unattributable
  def path(self):
    ''' The URL path as returned by `urlparse.urlparse`.
    '''
    return self.url_parsed.path

  @property
  @unattributable
  def rpath(self):
    ''' The URL path as returned by `urlparse.urlparse`, after any leading slashes.
    '''
    return self.path.lstrip('/')

  @cached_property
  @unattributable
  def cleanpath(self):
    ''' The URL path as returned by `urlparse.urlparse`,
        with multiple slashes (`/`) reduced to a single slash.
        Technically this can change the meaning of the URL path,
        but usually these are an artifact of sloppy path construction.
    '''
    path = self.path
    if '///' in path:
      path = re.sub('//+', '/', path)  # the thorough thing
    elif '//' in path:
      path = path.replace('//', '/')  # the fast thing
    return path

  @property
  def cleanrpath(self):
    ''' The `cleanpath` with its leading slash stripped.
    '''
    return self.cleanpath.lstrip('/')

  @property
  @unattributable
  def path_elements(self):
    ''' Return the non-empty path components; NB: a new list every time.
    '''
    return [w for w in self.path.strip('/').split('/') if w]

  @property
  @unattributable
  def params(self):
    ''' The URL params as returned by `urlparse.urlparse`.
    '''
    return self.url_parsed.params

  @property
  @unattributable
  def query(self):
    ''' The URL query as returned by `urlparse.urlparse`.
    '''
    return self.url_parsed.query

  @unattributable
  def query_dict(self):
    ''' Return a new `dict` containing the parsed param=value pairs from `self.query`.
    '''
    return parse_qs(self.query)

  @property
  @unattributable
  def fragment(self):
    ''' The URL fragment as returned by `urlparse.urlparse`.
    '''
    return self.url_parsed.fragment

  @property
  @unattributable
  def username(self):
    ''' The URL username as returned by `urlparse.urlparse`.
    '''
    return self.url_parsed.username

  @property
  @unattributable
  def password(self):
    ''' The URL password as returned by `urlparse.urlparse`.
    '''
    return self.url_parsed.password

  @property
  @unattributable
  def hostname(self):
    ''' The URL hostname as returned by `urlparse.urlparse`.
    '''
    return self.url_parsed.hostname

  @property
  @unattributable
  def port(self):
    ''' The URL port as returned by `urlparse.urlparse`.
    '''
    return self.url_parsed.port

  @property
  @unattributable
  def dirname(self):
    return os.path.dirname(self.path)

  @property
  @unattributable
  def parent(self):
    return URL(urljoin(self, self.dirname), referer=self)

  @property
  @unattributable
  def basename(self):
    ''' The URL basename.
    '''
    return os.path.basename(self.path)

  @property
  @unattributable
  def ext(self):
    ''' The URL basename file extension, as from `os.path.splitext`.
    '''
    return os.path.splitext(self.basename)[1]

  def find_all(self, *a, **kw):
    ''' Convenience routine to call BeautifulSoup's .find_all() method.
    '''
    parsed = self.parsed
    if not parsed:
      error("%s: parse fails", self)
      return ()
    return parsed.find_all(*a, **kw)

  def xml_find_all(self, match):
    ''' Convenience routine to call ElementTree.XML's .findall() method.
    '''
    return self.xml.findall(match)

  @property
  @unattributable
  def baseurl(self):
    for B in self.BASEs:
      try:
        base = strip_whitespace(B['href'])
      except KeyError:
        pass
      else:
        if base:
          return URL(base, referer=self)
    return self

  @property
  @unattributable
  def page_title(self):
    t = self.parsed.title
    if t is None:
      return ''
    return t.string

  def resolve(self, base):
    ''' Resolve this URL with respect to a base URL.
    '''
    return URL(urljoin(base, self), referer=base)

  def urlto(self, other: Union["URL", str]) -> "URL":
    ''' Return `other` resolved against `self.baseurl`.
        If `other` is an abolute URL it will not be changed.
    '''
    return URL(urljoin(self.baseurl, other), referer=self)

  def normalised(self):
    ''' Return a normalised URL where "." and ".." components have been processed.
    '''
    slashed = self.path.endswith('/')
    elems = self.path_elements
    i = 0
    while i < len(elems):
      elem = elems[i]
      if elem == '' or elem == '.':
        elems.pop(i)
      elif elem == '..':
        elems.pop(i)
        if i > 0:
          i -= 1
          elems.pop(i)
      else:
        i += 1
    normpath = '/' + '/'.join(elems)
    if slashed and not normpath.endswith('/'):
      normpath += '/'
    if normpath == self.path:
      U = self
    else:
      normURL = self.scheme + '://' + self.netloc + normpath
      if self.params:
        normURL += ';' + self.paras
      if self.fragment:
        normURL += '#' + self.fragment
      U = URL(normURL, referer=self.referer)
    return U

  def hrefs(self, absolute=False) -> Iterable["URL"]:
    ''' All 'href=' values from the content HTML 'A' tags.
        If `absolute`, resolve the sources with respect to our URL.
    '''
    for A in self.As:
      try:
        href = strip_whitespace(A['href'])
      except KeyError:
        debug("no href, skip %r", A)
        continue
      yield self.urlto(href) if absolute else URL(href, referer=self)

  def srcs(self, *a, **kw):
    ''' All 'src=' values from the content HTML.
        If `absolute`, resolve the sources with respect to our URL.
    '''
    absolute = False
    if 'absolute' in kw:
      absolute = kw['absolute']
      del kw['absolute']
    for A in self.find_all(*a, **kw):
      try:
        src = strip_whitespace(A['src'])
      except KeyError:
        debug("no src, skip %r", A)
        continue
      yield self.urlto(src) if absolute else URL(src, referer=self)

  def savepath(self, rootdir):
    ''' Compute a local filesystem save pathname for this URL.
        This scheme is designed to accomodate the fact that 'a',
        'a/' and 'a/b' can all coexist.
        Extend any component ending in '.' with another '.'.
        Extend directory components with '.d.'.
    '''
    elems = []
    Uelems = self.path_elements
    if self.endswith('/'):
      base = None
    else:
      base = Uelems.pop()
      if base.endswith('.'):
        base += '.'
    for elem in Uelems:
      if elem.endswith('.'):
        elem += '.'
      elem += '.d.'
      elems.append(elem)
    if base is not None:
      elems.append(base)
    path = '/'.join(elems)
    if not path:
      path = '.d.'
    revpath = '/' + self.unsavepath(path)
    if revpath != self.path:
      raise RuntimeError(
          "savepath: MISMATCH %r => %r => %r (expected %r)" %
          (self, path, revpath, self.path)
      )
    return path

  @classmethod
  def unsavepath(cls, savepath):
    ''' Compute URL path component from a savepath as returned by URL.savepath.
        This should always round trip with URL.savepath.
    '''
    with Pfx("unsavepath(%r)", savepath):
      elems = [elem for elem in savepath.split('/') if elem]
      base = elems.pop()
      with Pfx(base):
        if base == '.d.':
          base = ''
        elif base.endswith('.d.'):
          raise ValueError('basename may not end with ".d."')
      for i, elem in enumerate(elems):
        with Pfx(elem):
          if elem.endswith('.d.'):
            elem = elem[:-3]
          else:
            raise ValueError('dir elements must end in ".d."')
          elems[i] = elem
      elems.append(base)
      for elem in elems:
        with Pfx(elem):
          if elem.endswith('.'):
            elem = elem[:-1]
            if not elem.endswith('.'):
              raise ValueError(
                  'post "." trimming elem should end in ".", but does not'
              )
      return '/'.join(elems)

  def walk(self, limit=None, seen=None, follow_redirects=False):
    ''' Walk a website from this URL yielding this and all descendent URLs.
        `limit`: an object with a contraint test method "ok".
                 If not supplied, limit URLs to the same host and port.
        `seen`: a setlike object with a "__contains__" method and an "add" method.
                 URLs already in the set will not be yielded or visited.
        `follow_redirects`: whether to follow URL redirects
    '''
    with Pfx("walk(%r)", self):
      if limit is None:
        limit = self.default_limit()
      if seen is None:
        seen = set()
      todo = [self]
      while todo:
        U = heappop(todo)
        with Pfx(U):
          if U in seen:
            continue
          seen.add(U)
          if not limit.ok(U):
            warning("walk: reject %r, does not match limit %s", U, limit)
            continue
          yield U
          subURLs = []
          try:
            # TODO: also parse CSS, XML?
            if U.content_type == 'text/html':
              subURLs.extend(U.srcs())
              subURLs.extend(U.hrefs())
          except HTTPError as e:
            if e.code != 404:
              warning("%s", e)
          for subU in sorted(subURLs):
            subU0 = subU
            subU = subU.resolve(U)
            subU = subU.normalised()
            if limit.ok(subU):
              # strip fragment if present - not relevant
              try:
                subU, frag = subU.rsplit('#', 1)
              except ValueError:
                pass
              else:
                subU = URL(subU, referer=U)
              heappush(todo, subU)

  def default_limit(self):
    ''' Default URLLimit for this URL: same host:port, any subpath.
    '''
    return URLLimit(self.scheme, self.hostname, self.port, '/')

  @classmethod
  def promote(cls, obj):
    ''' Promote `obj` to an instance of `cls`.
        Instances of `cls` are passed through unchanged.
        `str` is promoted directly to `cls(obj)`.
        `(url,referer)` is promoted to `cls(url,referer=referer)`.
    '''
    # TODO: can the None check comes from Promotable.promote?
    if obj is None or isinstance(obj, cls):
      return obj
    if isinstance(obj, str):
      return cls(obj)
    try:
      url, referer = obj
    except (ValueError, TypeError):
      raise TypeError(
          "%s.promote: cannot convert to URL: %s" % (cls.__name__, r(obj))
      )
    if isinstance(url, cls):
      obj = url if referer is None else cls(url, referer=referer)
    else:
      obj = cls.promote(url) if referer is None else cls(url, referer=referer)
    return obj

class URLLimit(namedtuple('URLLimit', 'scheme hostname port subpath')):

  @promote
  def ok(self, U: URL):
    return (
        U.scheme == self.scheme and U.hostname == self.hostname
        and U.port == self.port and U.path.startswith(self.subpath)
    )

def strip_whitespace(s):
  ''' Strip whitespace characters from a string, per HTML 4.01 section 1.6 and appendix E.
  '''
  return ''.join([ch for ch in s if ch not in whitespace])

def skip_url_errs(func, *iterables, **skip_map_kw):
  ''' A version of `cs.seq.skip_map` which skips `URLError` and `HTTPError`.
  '''
  return skip_map(
      func, *iterables, except_types=(URLError, HTTPError), **skip_map_kw
  )

class URLs(object):

  MODE_RAISE = 0
  MODE_SKIP = 1

  def __init__(self, urls, context=None, mode=None):
    ''' Set up a `URLs` object with the iterable `urls` and the `context`
        object, which implements the mapping interface to store key value
        pairs.
        The iterable `urls` is kept as is, making this object a single use
        iterable unless the .multi property is accessed.
    '''
    if context is None:
      context = {}
    if mode is None:
      mode = self.MODE_RAISE
    self.urls = urls
    self.context = context
    self.mode = mode

  def __iter__(self):
    return iter(self.urls)

  def __getitem__(self, key):
    return self.context[key]

  def __setitem__(self, key, value):
    self.context[key] = value

  @property
  @unattributable
  def multi(self):
    ''' Prepare this URLs object for reuse by converting its urls
        iterable to a list if not already a list or tuple.
        Returns self.
    '''
    if not isinstance(self.urls, (list, tuple)):
      self.urls = list(self.urls)
    return self

  def map(self, func, mode=None) -> "URLs":
    return URLs(skip_url_errs(func, self.urls), self.context, mode)

  def hrefs(self, absolute=True, mode=None):
    return URLs(
        skip_url_errs(
            lambda url: URL(url).hrefs(absolute=absolute),
            self.urls,
        ), self.context, mode
    )

  def srcs(self, absolute=True, mode=None):
    ''' Return an iterable of the `src=` URLs from the content.
    '''
    return URLs(
        skip_url_errs(
            lambda url: URL(url).srcs(absolute=absolute),
            self.urls,
        ), self.context, mode
    )

class NetrcHTTPPasswordMgr(HTTPPasswordMgrWithDefaultRealm):
  ''' A subclass of `HTTPPasswordMgrWithDefaultRealm` that consults
      the `.netrc` file if no overriding credentials have been stored.
  '''

  def __init__(self, netrcfile=None):
    HTTPPasswordMgrWithDefaultRealm.__init__(self)
    self._netrc = netrc(netrcfile)

  def find_user_password(self, realm, authuri):
    user, password = HTTPPasswordMgrWithDefaultRealm.find_user_password(
        self, realm, authuri
    )
    if user is None:
      U = URL(authuri, None)
      netauth = self._netrc.authenticators(U.hostname)
      if netauth is not None:
        user, account, password = netauth
        debug(
            "find_user_password(%r, %r): netrc: user=%r password=%r", realm,
            authuri, user, password
        )
    return user, password

if __name__ == '__main__':
  import cs.logutils
  cs.logutils.setup_logging()
  UU = URLs(['http://www.mirror.aarnet.edu.au/'], mode=URLs.MODE_SKIP)
  print(list(UU.hrefs().hrefs()))
