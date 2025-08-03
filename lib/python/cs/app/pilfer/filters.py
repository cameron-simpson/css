#!/usr/bin/env python3

''' Filter functions and mappings of names to such functions.
'''

import os
import os.path
from time import sleep
from typing import Iterable
from urllib.parse import quote, unquote
from urllib.error import HTTPError, URLError
try:
  import xml.etree.cElementTree as ElementTree
except ImportError:
  from xml.etree import ElementTree

from typeguard import typechecked

from cs.deco import promote
from cs.logutils import (debug, warning)
from cs.py.func import funcname
from cs.resources import uses_runstate
from cs.urlutils import URL

from .pilfer import Pilfer

def notNone(v, name="value"):
  ''' Test whether `v` is `None`, raise a `ValueError` if so, return `True` if not.
  '''
  if v is None:
    raise ValueError("%s is None" % (name,))
  return True

@promote
def url_xml_find(U: URL, match):
  for found in url_io(U.xml_find_all, (), match):
    yield ElementTree.tostring(found, encoding='utf-8')

def has_exts(U, suffixes, case_sensitive=False):
  ''' Test if the .path component of a URL ends in one of a list of suffixes.
      Note that the .path component does not include the query_string.
  '''
  ok = False
  path = U.path
  if not path.endswith('/'):
    base = os.path.basename(path)
    if not case_sensitive:
      base = base.lower()
      suffixes = [sfx.lower() for sfx in suffixes]
    for sfx in suffixes:
      if base.endswith('.' + sfx):
        ok = True
        break
  return ok

def with_exts(urls, suffixes, case_sensitive=False):
  for U in urls:
    ok = False
    path = U.path
    if not path.endswith('/'):
      base = os.path.basename(path)
      if not case_sensitive:
        base = base.lower()
        suffixes = [sfx.lower() for sfx in suffixes]
      for sfx in suffixes:
        if base.endswith('.' + sfx):
          ok = True
          break
    if ok:
      yield U
    else:
      debug("with_exts: discard %s", U)

@uses_runstate
def url_io_iter(it, *, runstate):
  ''' Generator that calls `it.next()` until `StopIteration`, yielding
      its values.
      If the call raises URLError or HTTPError, report the error
      instead of aborting.
  '''
  while True:
    runstate.raiseif("url_io_iter(it=%s): cancelled", it)
    try:
      item = next(it)
    except StopIteration:
      break
    except (URLError, HTTPError) as e:
      warning("%s", e)
    else:
      yield item

@promote
@typechecked
def url_hrefs(U: URL) -> Iterable[URL]:
  ''' Yield the HREFs referenced by a URL.
      Conceals URLError, HTTPError.
  '''
  return url_io_iter(U.hrefs(absolute=True))

@promote
@typechecked
def url_srcs(U: URL) -> Iterable[URL]:
  ''' Yield the SRCs referenced by a URL.
      Conceals URLError, HTTPError.
  '''
  return url_io_iter(U.srcs(absolute=True))

# actions that work on the whole list of in-play URLs
# these return Pilfers
many_to_many = {
    'sort':
    lambda Ps, key=lambda P: P._, reverse=False:
    sorted(Ps, key=key, reverse=reverse),
    'last':
    lambda Ps: Ps[-1:],
}

# actions that work on individual Pilfer instances, returning multiple strings
one_to_many = {
    'hrefs': lambda P: url_hrefs(P._),
    'srcs': lambda P: url_srcs(P._),
    'xml': lambda P, match: url_xml_find(P._, match),
    'xmltext': lambda P, match: ElementTree.XML(P._).findall(match),
}

# actions that work on individual Pilfer instances, returning single strings
one_to_one = {
    '..':
    lambda P: URL(P._, None).parent,
    'delay':
    lambda P, delay: (P._, sleep(float(delay)))[0],
    'domain':
    lambda P: URL(P._, None).domain,
    'hostname':
    lambda P: URL(P._, None).hostname,
    'print':
    lambda P, **kw: (P._, P.print_url_string(P._, **kw))[0],
    'query':
    lambda P, *a: url_query(P._, *a),
    'quote':
    lambda P: quote(P._),
    'unquote':
    lambda P: unquote(P._),
    'save':
    lambda P, *a, **kw: (P._, P.save_url(P._, *a, **kw))[0],
    'title':
    lambda P: P._.page_title,
    'type':
    lambda P: url_io(P._.content_type, ""),
    'xmlattr':
    lambda P, attr:
    [A for A in (ElementTree.XML(P._).get(attr),) if A is not None],
}

one_test = {
    'has_title':
    lambda P: P._.page_title is not None,
    'reject_re':
    lambda P, regexp: not regexp.search(P._),
    'same_domain':
    lambda P: notNone(P._.referer, "%r.referer" %
                      (P._,)) and P._.domain == P._.referer.domain,
    'same_hostname':
    lambda P: notNone(P._.referer, "%r.referer" %
                      (P._,)) and P._.hostname == P._.referer.hostname,
    'same_scheme':
    lambda P: notNone(P._.referer, "%r.referer" %
                      (P._,)) and P._.scheme == P._.referer.scheme,
    'select_re':
    lambda P, regexp: regexp.search(P._),
}

def pilferify11(func):
  ''' Decorator for 1-to-1 Pilfer=>nonPilfer functions to return a Pilfer.
  '''

  def pf(P, *a, **kw):
    return P.copy_with_vars(_=func(P, *a, **kw))

  pf.__name__ = "@pilferify11(%s)" % funcname(func)
  return pf

def pilferify1m(func):
  ''' Decorator for 1-to-many Pilfer=>nonPilfers functions to yield Pilfers.
  '''

  @promote
  def pf(P: Pilfer, *a, **kw):
    for value in func(P, *a, **kw):
      yield P.copy_with_vars(_=value)

  pf.__name__ = "@pilferify1m(%s)" % funcname(func)
  return pf

def pilferifymm(func):
  ''' Decorator for 1-to-many Pilfer=>nonPilfers functions to yield Pilfers.
  '''

  def pf(Ps, *a, **kw):
    if not isinstance(Ps, list):
      Ps = list(Ps)
    if Ps:
      P0 = Ps[0]
      for value in func(Ps, *a, **kw):
        yield P0.copy_with_vars(_=value)

  pf.__name__ = "@pilferifymm(%s)" % funcname(func)
  return pf

def pilferifysel(func):
  ''' Decorator for selector Pilfer=>bool functions to yield Pilfers.
  '''

  def pf(Ps, *a, **kw):
    for P in Ps:
      if func(P, *a, **kw):
        yield P

  pf.__name__ = "@pilferifysel(%s)" % funcname(func)
  return pf
