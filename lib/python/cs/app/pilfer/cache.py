#!/usr/bin/env python3

from contextlib import contextmanager
from dataclasses import dataclass
import dbm.sqlite3
import json
import mimetypes
import os
from os.path import basename, join as joinpath, splitext
import time

from cs.context import stackattrs
from cs.fs import HasFSPath, needdir, validate_rpath
from cs.fileutils import atomic_filename
from cs.hashutils import BaseHashCode
from cs.logutils import warning
from cs.resources import MultiOpenMixin
from cs.urlutils import URL

from typeguard import typechecked

from .sitemap import SiteMap

from cs.debug import trace, X, r, s

@dataclass
class ContentCache(HasFSPath, MultiOpenMixin):

  fspath: str
  hashname: str = 'blake3'

  @typechecked
  def __post_init__(self):
    self.hashclass = BaseHashCode.hashclass(self.hashname)

  @contextmanager
  def startup_shutdown(self):
    needdir(self.fspath)
    needdir(self.cached_path)
    with dbm.sqlite3.open(self.dbmpath, 'c') as cache_map:
      with stackattrs(self, cache_map=cache_map):
        yield

  @property
  def cached_path(self):
    ''' The filesystem path of the subdirectory containing cached content.
    '''
    return self.pathto('cached')

  @property
  def dbmpath(self):
    ''' The filesystem path of the SQLite DBM file mapping URL cache keys to cache locations.
    '''
    return self.pathto('cache_map.sqlite')

  def dbmkey(self, key: str) -> bytes:
    ''' The binary key for the string `key`; just the UTF-8 encoding.
    '''
    return key.encode('utf-8')

  def __iter__(self):
    ''' Return an iterator of the cache keys.
    '''
    for dbmkey in self.cache_map:
      yield dbmkey.decode('utf-8')

  @typechecked
  def __getitem__(self, key: str) -> dict:
    return json.loads(self.cache_map[self.dbmkey(key)].decode('utf-8'))

  def get(self, key: str, default=None):
    try:
      return self[key]
    except KeyError:
      return default

  @typechecked
  def __setitem__(self, key: str, metadata: dict):
    self.cache_map[self.dbmkey(key)] = json.dumps(metadata).encode('utf-8')

  def cache_response(self, flow, sitemap: SiteMap):
    ''' Cache the contents of `flow.response` if the request URL cache key is not `None`.
    '''
    rq = flow.request
    url = rq.url
    cache_key = sitemap.url_key(url)
    if cache_key is None:
      warning("no URL cache key for %r", url)
      return None
    site_prefix = sitemap.name.replace("/", "__")
    cache_key = f'{site_prefix}/{cache_key}' if cache_key else site_prefix
    validate_rpath(cache_key)
    rsp = flow.response
    content = rsp.content
    assert isinstance(content, bytes)
    h = self.hashclass.from_data(content)
    U = URL(url)
    urlbase, urlext = splitext(basename(U.path))
    content_type = rsp.headers.get('content-type').split(';')[0].strip()
    if content_type:
      ctext = mimetypes.guess_extension(content_type) or ''
    else:
      warning("no request Content-Type")
      ctext = ''
    baserpath = cache_key
    contentrpath = f'{baserpath}/{urlbase or "index"}--{h}{urlext or ctext}'
    md = {
        'url': url,
        'unixtime': time.time(),
        'contentpath': contentrpath,
        'request_headers': dict(rq.headers),
        'response_headers': dict(rsp.headers),
    }
    basedir = joinpath(self.cached_path, baserpath)
    contentpath = joinpath(self.cached_path, contentrpath)
    # only make filesystem items if all the required compute succeeds
    needdir(basedir, use_makedirs=True)
    with atomic_filename(contentpath, mode='xb') as cf:
      cf.write(content)
    self[cache_key] = md
    os.system(f'ls -ld {contentpath!r}')

if __name__ == '__main__':
  sitemap = SiteMap()
  sitemap.url_key = trace(
      lambda self, url: url.replace('/', '_'),
      retval=True,
  )
  cache = ContentCache(fspath='content')
  cache.cache_response('foo', sitemap)
