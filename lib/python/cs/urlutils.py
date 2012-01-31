#!/usr/bin/python
#
# URL related utility functions and classes.
#       - Cameron Simpson <cs@zip.com.au> 26dec2011
#

from __future__ import with_statement
import os.path
import sys
from itertools import chain
from BeautifulSoup import BeautifulSoup, Tag, BeautifulStoneSoup
from StringIO import StringIO
import socket
from urllib2 import urlopen, Request, HTTPError, URLError
from urlparse import urlparse, urljoin
from HTMLParser import HTMLParseError
try:
  import xml.etree.cElementTree as ElementTree
except ImportError:
  import xml.etree.ElementTree as ElementTree
from cs.logutils import Pfx, pfx_iter, debug, error, warning, exception

def URL(U, referer, user_agent=None):
  ''' Factory function to return a _URL object from a URL string.
      Handing it a _URL object returns the object.
  '''
  t = type(U)
  if t is not _URL:
    U = _URL(U)
  if user_agent is None:
    if referer and isinstance(referer, _URL):
      user_agent = referer.user_agent
  if user_agent:
    U.user_agent = user_agent
  if referer:
    U.referer = URL(referer, None, user_agent=user_agent)
  return U

class _URL(unicode):
  ''' Utility class to do simple stuff to URLs.
      Subclasses str.
  '''

  def __init__(self, s, referer=None, user_agent=None):
    self.referer = URL(referer) if referer else referer
    self.user_agent = user_agent if user_agent else self.referer.user_agent if self.referer else None
    self._parts = None
    self.flush()

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

  def _fetch(self):
    ''' Fetch the URL content.
    '''
    with Pfx("_fetch(%s)" % (self,)):
      hdrs = {}
      if self.referer:
        debug("referer = %s", self.referer)
        hdrs['Referer'] = self.referer
      hdrs['User-Agent'] = self.user_agent if self.user_agent else 'css'
      url = 'file://'+self if self.startswith('/') else self
      rq = Request(url, None, hdrs)
      debug("urlopen(%s[%r])", url, hdrs)
      rsp = urlopen(rq)
      H = rsp.info()
      self._content_type = H.gettype()
      self._content = rsp.read()
      self._parsed = None

  def get_content(self, onerror=None):
    ''' Probe URL for content to avoid exceptions later.
        Use, and save as .content, `onerror` in the case of HTTPError.
    '''
    try:
      content = self.content
    except (HTTPError, URLError, socket.error), e:
      error("%s.get_content: %s", self, e)
      content = onerror
    self._content = content
    return content

  @property
  def content(self):
    ''' The URL content as a string.
    '''
    if self._content is None:
      self._fetch()
    return self._content

  @property
  def content_type(self):
    ''' The URL content MIME type.
    '''
    if self._content is None:
      self._fetch()
    return self._content_type

  @property
  def domain(self):
    ''' The URL domain - the hostname with the first dotted component removed.
    '''
    hostname = self.hostname
    if not hostname or '.' not in hostname:
      warning("%s: no domain in hostname: %s", self, hostname)
      return ''
    return hostname.split('.', 1)[1]

  @property
  def parsed(self):
    ''' The URL content parsed as HTML by BeautifulSoup.
    '''
    if self._parsed is None:
      content = self.content
      try:
        self._parsed = BeautifulSoup(content.decode('utf-8', 'replace'))
      except Exception, e:
        exception("%s: .parsed: BeautifulSoup(unicode(content)) fails: %s", self, e)
        with open("cs.urlutils-unparsed.html", "wb") as bs:
          bs.write(self.content)
        self._parsed = None
    return self._parsed

  @property
  def xml(self):
    if self._xml is None:
      content = self.content
      self._xml = ElementTree.XML(content.decode('utf-8', 'replace'))
    return self._xml

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

  def findAll(self, *a, **kw):
    ''' Convenience routine to call BeautifulSoup's .findAll() method.
    '''
    parsed = self.parsed
    if not parsed:
      error("%s: parse fails", self)
      return ()
    return parsed.findAll(*a, **kw)

  def xmlFindall(self, match):
    ''' Convenience routine to call ElementTree.XML's .findall() method.
    '''
    print >>sys.stderr, "xmlFindAll(match=%r)\n" % (match,)
    print >>sys.stderr, "xmlFindAll: xml=%r\n" % (self.xml,)
    print >>sys.stderr, "xmlFindAll: xml=%s\n" % (self.xml,)
    for found in self.xml.findall(match):
      print >>sys.stderr, "xmlFindAll: found=%r\n" % (found,)
      yield ElementTree.tostring(found, encoding='utf-8')

  @property
  def baseurl(self):
    for B in self.findAll('base'):
      try:
        base = B['href']
      except KeyError:
        pass
      else:
        if base:
          return URL(base, self)
    return self

  @property
  def title(self):
    Ts = self.findAll('title')
    if not Ts:
      return ""
    s = Ts[0].string
    if s is None:
      return ""
    return s

  def hrefs(self, absolute=False):
    ''' All 'href=' values from the content HTML 'A' tags.
        If `absolute`, resolve the sources with respect to our URL.
    '''
    for A in self.findAll('a'):
      try:
        href = A['href']
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
    for A in self.findAll(*a, **kw):
      try:
        src = A['src']
      except KeyError:
        debug("no src, skip %r", A)
        continue
      yield URL( (urljoin(self.baseurl, src) if absolute else src), self )

def skip_errs(iterable):
  ''' Iterate over `iterable` and yield its values.
      If it raises URLError or HTTPError, report the error and skip the result.
  '''
  debug("skip_errs...")
  I = iter(iterable)
  while True:
    try:
      i = I.next()
    except StopIteration:
      break
    except (URLError, HTTPError), e:
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

if __name__ == '__main__':
  import cs.logutils
  cs.logutils.setup_logging()
  UU = URLs( [ 'http://www.mirror.aarnet.edu.au/' ], mode=URLs.MODE_SKIP )
  print list(UU.hrefs().hrefs())
