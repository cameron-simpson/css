#!/usr/bin/env python3
#
# URL related utility functions and classes.
# - Cameron Simpson <cs@cskk.id.au> 26dec2011
#

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
        'cs.excutils',
        'cs.lex',
        'cs.logutils',
        'cs.rfc2616',
        'cs.threads',
        'cs.obj',
        'cs.xml',
    ],
}

from collections import namedtuple
from contextlib import contextmanager
from heapq import heappush, heappop
from itertools import chain
import errno
import os
import os.path
import sys
import time

from netrc import netrc
import socket
from string import whitespace
from threading import RLock
from urllib.request import Request, HTTPError, URLError, \
            HTTPPasswordMgrWithDefaultRealm, HTTPBasicAuthHandler, \
            build_opener
from urllib.parse import urlparse, urljoin, quote as urlquote

from bs4 import BeautifulSoup, Tag, BeautifulStoneSoup
import lxml
import requests
from typeguard import typechecked

from cs.deco import cachedmethod, promote, Promotable
from cs.excutils import logexc, safe_property
from cs.lex import parseUC_sAttr
from cs.logutils import debug, error, warning, exception
from cs.obj import SingletonMixin
from cs.pfx import Pfx, pfx_iter
from cs.rfc2616 import datetime_from_http_date
from cs.threads import locked
from cs.xml import etree  # ElementTree
from cs.threads import locked_property, State as ThreadState

##from http.client import HTTPConnection
##putheader0 = HTTPConnection.putheader
##def my_putheader(self, header, *values):
##  for v in values:
##    X("HTTPConnection.putheader(%r): value=%r", header, v)
##  return putheader0(self, header, *values)
##HTTPConnection.putheader = my_putheader

class URL(SingletonMixin, HasThreadState, Promotable):
  ''' Utility class to do simple stuff to URLs, subclasses `str`.
  '''

  # Thread local stackable class state
  context = ThreadState(
      referer=None,
      user_agent=None,
      opener=None,
      retry_delay=3,
  )

  @classmethod
  def _singleton_key(cls, url_s: str):
    return url_s

  @promote
  @typechecked
  def __init__(self, url_s: str):
    ''' Initialise the `URL` from the URL string `url_s`.
    '''
    if hasattr(self, 'url_s'):
      assert url_s == self.url_s
      return
    self.url_s = url_s
    self._lock = trace_func(RLock)()
    self._parts = None
    self._info = None
    self.flush()

  def __str__(self):
    return f'{self.__class__.__name__}({self.url_s})'

  def flush(self):
    ''' Forget all cached content.
    '''
    for val_attr in ['_' + attr for attr in 'GET HEAD parsed'.split()]:
      try:
        delattr(self, val_attr)
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
      P = self.parsed
      nodes = P.find_all(k.lower())
      if plural:
        return nodes
      node, = nodes
      return node
    # look up method on equivalent Unicode string
    raise AttributeError(f'{self.__class__.__name__}.{attr}')

  @contextmanager
  def session(session=None):
    ''' Context manager yielding a `requests.Session`.
    '''
    if session is None:
      with requests.Session() as session:
        with self.session(session=session):
          yield session
    else:
      with self.context(session=session):
        yield session

  @locked
  @cachedmethod
  def GET(self):
    ''' Return the `requests.Response` object from a `GET` of this URL.
        This may be a cached response.
    '''
    return requests.get(self.url_s)

  @locked
  @cachedmethod
  def HEAD(self):
    ''' Return the `requests.Response` object from a `HEAD` of this URL.
        This may be a cached response.
    '''
    return requests.get(self.url_s)

  def exists(self) -> bool:
    ''' Test if this URL exists.
    '''
    try:
      self.HEAD()
    except HTTPError as e:
      if e.code == 404:
        return False
      raise
    return True

  @safe_property
  def content(self):
    ''' The URL content as a string.
    '''
    return self.GET().content

  @safe_property
  def text(self):
    ''' The URL content as a string.
    '''
    return self.GET().text

  @safe_property
  @locked
  def headers(self):
    ''' A `requests.Response` headers mapping.
    '''
    r = self.HEAD()
    return r.headers

  @safe_property
  def content_type(self):
    ''' The URL content MIME type.
    '''
    return self.headers['content-type']

  @safe_property
  def content_length(self):
    ''' The value of the Content-Length: header or `None`.
    '''
    try:
      length_s = self.headers['content-length']
    except KeyError:
      return None
    return int(length_s)

  @safe_property
  def last_modified(self):
    ''' The value of the Last-Modified: header as a UNIX timestamp, or None.
    '''
    if self._info is None:
      self.HEAD()
    value = self._info['Last-Modified']
    if value is not None:
      # parse HTTP-date into datetime object
      dt_last_modified = datetime_from_http_date(value.strip())
      value = dt_last_modified.timestamp()
    return value

  @safe_property
  @locked
  def content_transfer_encoding(self):
    ''' The URL content tranfer encoding.
    '''
    if self._content is None:
      self.HEAD()
    return self._info.getencoding()

  @safe_property
  def domain(self):
    ''' The URL domain - the hostname with the first dotted component removed.
    '''
    hostname = self.hostname
    if not hostname or '.' not in hostname:
      warning("%s: no domain in hostname: %s", self, hostname)
      return ''
    return hostname.split('.', 1)[1]

  @safe_property
  @locked
  @cachedmethod
  def parsed(self):
    ''' The URL content parsed as HTML by BeautifulSoup.
    '''
    try:
      text = self.text
      if self.content_type == 'text/html':
        parser_names = ('html5lib', 'html.parser', 'lxml', 'xml')
      else:
        parser_names = ('lxml', 'xml')
      try:
        P = BeautifulSoup(text, 'html5lib')
        ##P = BeautifulSoup(content.decode('utf-8', 'replace'), list(parser_names))
      except Exception as e:
        exception(
            "%s: .parsed: BeautifulSoup(text,html5lib) fails: %s", self, e
        )
        with open("cs.urlutils-unparsed.html", "wb") as bs:
          bs.write(self.content)
        raise
      return P
    except:
      raise

  def feedparsed(self):
    ''' A parse of the content via the feedparser module.
    '''
    import feedparser
    return feedparser.parse(self.content)

  @safe_property
  @locked
  def xml(self):
    ''' An `ElementTree` of the URL content.
    '''
    return etree.XML(self.content.decode('utf-8', 'replace'))

  @safe_property
  def parts(self):
    ''' The URL parsed into parts by urlparse.urlparse.
    '''
    if self._parts is None:
      self._parts = urlparse(self)
    return self._parts

  @safe_property
  def scheme(self):
    ''' The URL scheme as returned by urlparse.urlparse.
    '''
    return self.parts.scheme

  @safe_property
  def netloc(self):
    ''' The URL netloc as returned by urlparse.urlparse.
    '''
    return self.parts.netloc

  @safe_property
  def path(self):
    ''' The URL path as returned by urlparse.urlparse.
    '''
    return self.parts.path

  @safe_property
  def path_elements(self):
    ''' Return the non-empty path components; NB: a new list every time.
    '''
    return [w for w in self.path.strip('/').split('/') if w]

  @safe_property
  def params(self):
    ''' The URL params as returned by urlparse.urlparse.
    '''
    return self.parts.params

  @safe_property
  def query(self):
    ''' The URL query as returned by urlparse.urlparse.
    '''
    return self.parts.query

  @safe_property
  def fragment(self):
    ''' The URL fragment as returned by urlparse.urlparse.
    '''
    return self.parts.fragment

  @safe_property
  def username(self):
    ''' The URL username as returned by urlparse.urlparse.
    '''
    return self.parts.username

  @safe_property
  def password(self):
    ''' The URL password as returned by urlparse.urlparse.
    '''
    return self.parts.password

  @safe_property
  def hostname(self):
    ''' The URL hostname as returned by urlparse.urlparse.
    '''
    return self.parts.hostname

  @safe_property
  def port(self):
    ''' The URL port as returned by urlparse.urlparse.
    '''
    return self.parts.port

  @safe_property
  def dirname(self, absolute=False):
    return os.path.dirname(self.path)

  @safe_property
  def parent(self):
    return URL(urljoin(self, self.dirname), referer=self)

  @safe_property
  def basename(self):
    return os.path.basename(self.path)

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

  @safe_property
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

  @safe_property
  def page_title(self):
    t = self.parsed.title
    if t is None:
      return ''
    return t.string

  def resolve(self, base):
    ''' Resolve this URL with respect to a base URL.
    '''
    return URL(urljoin(base, self), referer=base)

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

  def hrefs(self, absolute=False):
    ''' All 'href=' values from the content HTML 'A' tags.
        If `absolute`, resolve the sources with respect to our URL.
    '''
    for A in self.As:
      try:
        href = strip_whitespace(A['href'])
      except KeyError:
        debug("no href, skip %r", A)
        continue
      yield URL(
          (urljoin(self.baseurl, href) if absolute else href), referer=self
      )

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
      yield URL(
          (urljoin(self.baseurl, src) if absolute else src), referer=self
      )

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
    if isinstance(obj, cls):
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

def skip_errs(iterable):
  ''' Iterate over `iterable` and yield its values.
      If it raises URLError or HTTPError, report the error and skip the result.
  '''
  debug("skip_errs...")
  it = iter(iterable)
  while True:
    try:
      i = next(it)
    except StopIteration:
      break
    except (URLError, HTTPError) as e:
      warning("%s", e)
    else:
      debug("skip_errs: yield %r", i)
      yield i

def can_skip_url_errs(func):

  def wrapped(self, *args, **kwargs):
    mode = kwargs.pop('mode', self.mode)
    if mode == URLs.MODE_SKIP:
      return URLs(
          skip_errs(func(self, *args, mode=URLs.MODE_RAISE, **kwargs)),
          self.context, self.mode
      )
    return func(self, *args, mode=mode, **kwargs)

  return wrapped

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

  @safe_property
  def multi(self):
    ''' Prepare this URLs object for reuse by converting its urls
        iterable to a list if not already a list or tuple.
        Returns self.
    '''
    if not isinstance(self.urls, (list, tuple)):
      self.urls = list(self.urls)
    return self

  @can_skip_url_errs
  def map(self, func, mode=None):
    return URLs([func(url) for url in self.urls], self.context, mode)

  @can_skip_url_errs
  def hrefs(self, absolute=True, mode=None):
    ''' Return an iterable of the `hrefs=` URLs from the content.
    '''
    return URLs(
        chain(
            *[
                pfx_iter(url,
                         URL(url).hrefs(absolute=absolute))
                for url in self.urls
            ]
        ), self.context, mode
    )

  @can_skip_url_errs
  def srcs(self, absolute=True, mode=None):
    ''' Return an iterable of the `src=` URLs from the content.
    '''
    return URLs(
        chain(
            *[
                pfx_iter(url,
                         URL(url).srcs(absolute=absolute)) for url in self.urls
            ]
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
