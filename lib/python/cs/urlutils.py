#!/usr/bin/python
#
# URL related utility functions and classes.
#       - Cameron Simpson <cs@zip.com.au> 26dec2011
#

from __future__ import with_statement
import sys
from BeautifulSoup import BeautifulSoup, Tag, BeautifulStoneSoup
from urllib2 import urlopen, Request
from urlparse import urlparse, urljoin
from HTMLParser import HTMLParseError
from cs.logutils import Pfx, debug, error, warning

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
    self._content = None
    self._content_type = None
    self._parsed = None

  def _fetch(self):
    ''' Fetch the URL content.
    '''
    with Pfx("_fetch(%s)" % (self,)):
      hdrs = {}
      if self.referer:
        debug("referer = %s", self.referer)
        hdrs['Referer'] = self.referer
      hdrs['User-Agent'] = self.user_agent if self.user_agent else 'css'
      rq = Request(self, None, hdrs)
      debug("urlopen(%s[%s])", self, hdrs)
      rsp = urlopen(rq)
      H = rsp.info()
      self._content_type = H.gettype()
      self._content = rsp.read()
      self._parsed = None

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
      try:
        self._parsed = BeautifulSoup(self.content)
      except HTMLParseError, e:
        with open("BS.html", "w") as bs:
          bs.write(self.content)
        raise
    return self._parsed

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

  def findAll(self, *a, **kw):
    ''' Convenience routine to call BeautifulSoup's .findAll() method.
    '''
    return self.parsed.findAll(*a, **kw)

  def hrefs(self, absolute=False):
    ''' All 'href=' values from the content HTML 'A' tags.
        If `absolute`, resolve the sources with respect to our URL.
    '''
    P = self.parsed
    for A in self.findAll('a'):
      try:
        href = A['href']
      except KeyError:
        debug("no href, skip %s", A)
        continue
      yield URL( (urljoin(self, href) if absolute else href), self )

  def srcs(self, *a, **kw):
    ''' All 'src=' values from the content HTML.
        If `absolute`, resolve the sources with respect to our URL.
    '''
    absolute = False
    if 'absolute' in kw:
      absolute = kw['absolute']
      del kw['absolute']
    P = self.parsed
    for A in self.findAll(*a, **kw):
      try:
        src = A['src']
      except KeyError:
        debug("no src, skip %s", A)
        continue
      yield URL( (urljoin(self, src) if absolute else src), self )
