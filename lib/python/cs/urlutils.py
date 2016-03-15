#!/usr/bin/python
#
# URL related utility functions and classes.
#       - Cameron Simpson <cs@zip.com.au> 26dec2011
#

from __future__ import with_statement, print_function

DISTINFO = {
    'description': "convenience functions for working with URLs",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
        ],
    'requires': ['lxml', 'beautifulsoup4', 'cs.excutils', 'cs.lex', 'cs.logutils', 'cs.threads', 'cs.py3', 'cs.obj'],
}

import os
import os.path
import sys
import errno
import time
from itertools import chain
from bs4 import BeautifulSoup, Tag, BeautifulStoneSoup
try:
  import lxml
except ImportError:
  try:
    if sys.stderr.isatty():
      print("%s: warning: cannot import lxml for use with bs4" % (__file__,), file=sys.stderr)
  except AttributeError:
    pass
from netrc import netrc
import socket
try:
  from urllib.request import Request, HTTPError, URLError, \
            HTTPPasswordMgrWithDefaultRealm, HTTPBasicAuthHandler, \
            build_opener
  from urllib.parse import urlparse, urljoin
except ImportError as e:
  from urllib2 import Request, HTTPError, URLError, \
		    HTTPPasswordMgrWithDefaultRealm, HTTPBasicAuthHandler, \
		    build_opener
  from urlparse import urlparse, urljoin
try:
  import xml.etree.cElementTree as ElementTree
except ImportError:
  import xml.etree.ElementTree as ElementTree
from string import whitespace
from threading import RLock
from cs.excutils import logexc
from cs.lex import parseUC_sAttr
from cs.logutils import Pfx, pfx_iter, debug, error, warning, exception, D, X
from cs.threads import locked_property
from cs.py3 import ustr, unicode
from cs.obj import O

def isURL(U):
  ''' Test if an object `U` is an URL instance.
  '''
  return isinstance(U, _URL)

def URL(U, referer, **kw):
  ''' Factory function to return a _URL object from a URL string.
      Handing it a _URL object returns the object.
  '''
  if not isURL(U):
    ##D("new U %r (ref=%r)", U, referer)
    U = _URL(ustr(U))
    U._init(referer=referer, **kw)
  else:
    if U.referer is None and referer is not None:
      ##D("old U %r, updating referer to %r", U, referer)
      U.referer = referer
    else:
      ##D("old U %r (ignoring ref=%r)", U, referer)
      pass
  return U

class _URL(unicode):
  ''' Utility class to do simple stuff to URLs.
      Subclasses unicode.
  '''

  def _init(self, referer=None, user_agent=None, opener=None):
    ''' Initialise the _URL.
        `s`: the string defining the URL.
        `referer`: the referring URL.
        `user_agent`: User-Agent string, inherited from `referer` if unspecified,
                  "css" if no referer.
        `opener`: urllib2 opener object, inherited from `referer` if unspecified,
                  made at need if no referer.
    '''
    self.referer = URL(referer, None) if referer else referer
    self.user_agent = user_agent if user_agent else self.referer.user_agent if self.referer else None
    self._opener = opener
    self._parts = None
    self.flush()
    self._lock = RLock()
    self.flush()
    self.retry_timeout = 3

  def __getattr__(self, attr):
    ''' Ad hoc attributes.
        Upper case attributes named "FOO" parse the text and find the (sole) node named "foo".
        Upper case attributes named "FOOs" parse the text and find all the nodes named "foo".
    '''
    k, plural = parseUC_sAttr(attr)
    if k:
      P = self.parsed
      nodes = P.find_all(k.lower())
      if plural:
        return nodes
      return the(nodes)
    raise AttributeError(attr)

  def flush(self):
    ''' Forget all cached content.
    '''
    # Note: _content is the raw bytes returns from the URL read().
    #       _parsed is a BeautifulSoup parse of the _content decoded as utf-8.
    #       _xml is an Elementtree parse of the _content decoded as utf-8.
    self._content = None
    self._content_type = None
    self._parsed = None
    self._xml = None
    self._fetch_exception = None

  @property
  def opener(self):
    if self._opener is None:
      if self.referer is not None and self.referer._opener is not None:
        self._opener = self.referer._opener
      else:
        o = build_opener()
        o.add_handler(HTTPBasicAuthHandler(NetrcHTTPPasswordMgr()))
        self._opener = o
    return self._opener

  def _request(self, method):
    class MyRequest(Request):
      def get_method(self):
        return method
    hdrs = {}
    if self.referer:
      hdrs['Referer'] = self.referer
    hdrs['User-Agent'] = self.user_agent if self.user_agent else os.environ.get('USER_AGENT', 'css')
    url = 'file://'+self if self.startswith('/') else self
    rq = MyRequest(url, None, hdrs)
    return rq

  def _response(self, method):
    rq = self._request(method)
    opener = self.opener
    retries = self.retry_timeout
    with Pfx("open(%s)", rq):
      while retries > 0:
        now = time.time()
        try:
          opened_url = opener.open(rq)
        except OSError as e:
          if e.errno == errno.ETIMEDOUT:
            elapsed = time.time() - now
            warning("open %s: %s; elapsed=%gs", self, e, elapsed)
            if retries > 0:
              retries -= 1
            else:
              raise
        except HTTPError as e:
          warning("open %s: %s", self, e)
          raise
        else:
          # success, exit retry loop
          break
    return opened_url

  def _fetch(self):
    ''' Fetch the URL content.
        If there is an HTTPError, report the error, flush the
        content, set self._fetch_exception.
        This means that that accessing the self.content property
        will always attempt a fetch, but return None on error.
    '''
    with Pfx("_fetch(%s)", self):
      try:
        with self._response('GET') as opened_url:
          opened_url = self._response('GET')
          self.opened_url = opened_url
          # URL post redirection
          self.final_url = URL(opened_url.geturl(), self)
          self._info = opened_url.info()
          self._content = opened_url.read()
          self._parsed = None
      except HTTPError as e:
        error("error with GET: %s", e)
        self.flush()
        self._fetch_exception = e

  # present GET action publicly
  GET = _fetch

  def HEAD(self):
    opened_url = self._response('HEAD')
    opened_url.read()
    return opened_url

  @logexc
  def get_content(self, onerror=None):
    ''' Probe URL for content to avoid exceptions later.
        Use, and save as .content, `onerror` in the case of HTTPError.
    '''
    try:
      content = self.content
    except (HTTPError, URLError, socket.error) as e:
      error("%s.get_content: %s", self, e)
      content = onerror
    self._content = content
    return content

  @locked_property
  def content(self):
    ''' The URL content as a string.
    '''
    self._fetch()
    return self._content

  @property
  def content_type(self):
    ''' The URL content MIME type.
    '''
    if self._content is None:
      self._fetch()
    try:
      ctype = self._info.get_content_type()
    except AttributeError as e:
      warning("%r.content_type: self._info.get_content_type() raises %s", self, e)
      ctype = None
    return ctype

  @locked_property
  def content_transfer_encoding(self):
    ''' The URL content tranfer encoding.
    '''
    if self._content is None:
      self._fetch()
    return self._info.getencoding()

  @property
  def domain(self):
    ''' The URL domain - the hostname with the first dotted component removed.
    '''
    hostname = self.hostname
    if not hostname or '.' not in hostname:
      warning("%s: no domain in hostname: %s", self, hostname)
      return ''
    return hostname.split('.', 1)[1]

  @locked_property
  def parsed(self):
    ''' The URL content parsed as HTML by BeautifulSoup.
    '''
    content = self.content
    if self.content_type == 'text/html':
      parser_names = ('html5lib', 'html.parser', 'lxml', 'xml')
    else:
      parser_names = ('lxml', 'xml')
    try:
      P = BeautifulSoup(content.decode('utf-8', 'replace'), 'lxml')
      ##P = BeautifulSoup(content.decode('utf-8', 'replace'), list(parser_names))
    except Exception as e:
      exception("%s: .parsed: BeautifulSoup(unicode(content)) fails: %s", self, e)
      with open("cs.urlutils-unparsed.html", "wb") as bs:
        bs.write(self.content)
      raise
    return P

  def feedparsed(self):
    ''' A parse of the content via the feedparser module.
    '''
    import feedparser
    return feedparser.parse(self.content)

  @locked_property
  def xml(self):
    return ElementTree.XML(self.content.decode('utf-8', 'replace'))

  @property
  def parts(self):
    ''' The URL parsed into parts by urlparse.urlparse.
    '''
    if self._parts is None:
      self._parts = urlparse(self)
    return self._parts

  @property
  def scheme(self):
    ''' The URL scheme as returned by urlparse.urlparse.
    '''
    return self.parts.scheme

  @property
  def netloc(self):
    ''' The URL netloc as returned by urlparse.urlparse.
    '''
    return self.parts.netloc

  @property
  def path(self):
    ''' The URL path as returned by urlparse.urlparse.
    '''
    return self.parts.path

  @property
  def path_elements(self):
    ''' Return the non-empty path components; NB: a new list every time.
    '''
    return [ w for w in self.path.strip('/').split('/') if w ]

  @property
  def params(self):
    ''' The URL params as returned by urlparse.urlparse.
    '''
    return self.parts.params

  @property
  def query(self):
    ''' The URL query as returned by urlparse.urlparse.
    '''
    return self.parts.query

  @property
  def fragment(self):
    ''' The URL fragment as returned by urlparse.urlparse.
    '''
    return self.parts.fragment

  @property
  def username(self):
    ''' The URL username as returned by urlparse.urlparse.
    '''
    return self.parts.username

  @property
  def password(self):
    ''' The URL password as returned by urlparse.urlparse.
    '''
    return self.parts.password

  @property
  def hostname(self):
    ''' The URL hostname as returned by urlparse.urlparse.
    '''
    return self.parts.hostname

  @property
  def port(self):
    ''' The URL port as returned by urlparse.urlparse.
    '''
    return self.parts.port

  @property
  def dirname(self, absolute=False):
    return os.path.dirname(self.path)

  @property
  def parent(self):
    return URL(urljoin(self, self.dirname), self)

  @property
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

  @property
  def baseurl(self):
    for B in self.BASEs:
      try:
        base = strip_whitespace(B['href'])
      except KeyError:
        pass
      else:
        if base:
          return URL(base, self)
    return self

  @property
  def page_title(self):
    t = self.parsed.title
    if t is None:
      return ''
    return t.string

  def resolve(self, base):
    ''' Resolve this URL with respect to a base URL.
    '''
    return URL(urljoin(base, self), base)

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
      yield URL( (urljoin(self.baseurl, href) if absolute else href), self )

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
      yield URL( (urljoin(self.baseurl, src) if absolute else src), self )

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
      raise RuntimeError("savepath: MISMATCH %r => %r => %r (expected %r)" % (self, path, revpath, self.path))
      raise RuntimeError("BANG")
    return path

  @classmethod
  def unsavepath(cls, savepath):
    ''' Compute URL path component from a savepath as returned by URL.savepath.
        This should always round trip with URL.savepath.
    '''
    with Pfx("unsavepath(%r)", savepath):
      elems = [ elem for elem in savepath.split('/') if elem ]
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
              raise ValueError('post "." trimming elem should end in ".", but does not')
      return '/'.join(elems)

def strip_whitespace(s):
  ''' Strip whitespace characters from a string, per HTML 4.01 section 1.6 and appendix E.
  '''
  return ''.join([ ch for ch in s if ch not in whitespace ])

def skip_errs(iterable):
  ''' Iterate over `iterable` and yield its values.
      If it raises URLError or HTTPError, report the error and skip the result.
  '''
  debug("skip_errs...")
  I = iter(iterable)
  while True:
    try:
      i = next(I)
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
      return URLs( skip_errs(func(self, *args, mode=URLs.MODE_RAISE, **kwargs)),
                   self.context,
                   self.mode
                 )
    return func(self, *args, mode=mode, **kwargs)
  return wrapped

class URLs(object):

  MODE_RAISE = 0
  MODE_SKIP = 1

  def __init__(self, urls, context=None, mode=None):
    ''' Set up a URLs object with the iterable `urls` and the `context`
        object, which implements the mapping interface to store key value
        pairs.
        The iterable `urls` is kept as is, making this object a single use
        iterable unless the .multi property is accessed.
    '''
    if context is None:
      context = {}
    if mode is None:
      mode = URLs.MODE_RAISE
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
    return URLS( [ func(url) for url in self.urls ],
                 self.context,
                 mode
               )

  @can_skip_url_errs
  def hrefs(self, absolute=True, mode=None):
    return URLs( chain( *[ pfx_iter( url,
                                     URL(url, None).hrefs(absolute=absolute)
                                   )
                           for url in self.urls
                         ]),
                 self.context,
                 mode)

  @can_skip_url_errs
  def srcs(self, absolute=True, mode=None):
    return URLs( chain( *[ pfx_iter( url,
                                     URL(url, None).srcs(absolute=absolute)
                                   )
                           for url in self.urls
                         ]),
                 self.context,
                 mode)

class NetrcHTTPPasswordMgr(HTTPPasswordMgrWithDefaultRealm):
  ''' A subclass of HTTPPasswordMgrWithDefaultRealm that consults
      the .netrc file if no overriding credentials have been stored.
  '''

  def __init__(self, netrcfile=None):
    HTTPPasswordMgrWithDefaultRealm.__init__(self)
    self._netrc = netrc(netrcfile)

  def find_user_password(self, realm, authuri):
    user, password = HTTPPasswordMgrWithDefaultRealm.find_user_password(self, realm, authuri)
    if user is None:
      U = _URL(authuri)
      netauth = self._netrc.authenticators(U.hostname)
      if netauth is not None:
        user, account, password = netauth
        debug("find_user_password(%r, %r): netrc: user=%r password=%r", realm, authuri, user, password)
    return user, password

if __name__ == '__main__':
  import cs.logutils
  cs.logutils.setup_logging()
  UU = URLs( [ 'http://www.mirror.aarnet.edu.au/' ], mode=URLs.MODE_SKIP )
  print(list(UU.hrefs().hrefs()))
