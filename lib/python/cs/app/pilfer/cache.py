#!/usr/bin/env python3

from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
from os.path import splitext
import time

from cs.fs import HasFSPath, needdir, validate_rpath
from cs.fileutils import atomic_filename
from cs.hashutils import BaseHashCode
from cs.logutils import warning
from cs.resources import MultiOpenMixin

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

    cache_key = sitemap.url_cache_key(url)
    if cache_key is None:
      warning("no URL cache key for %r", url)
      return None
    validate_rpath(cache_key)
    content = flow.response.content
    assert isinstance(content, bytes)
    h = self.hashclass.from_data(content)
    _, ext = splitext(url)
    basedir = f'{self.cached_path}/{cache_key}'
    needdir(basedir, use_makedirs=True)
    mdpath = f'{basedir}/{h}--metadata.json'
    contentpath = f'{basedir}/{h}--contents.{ext}'
    md = {
        'url': url,
        'unixtime': time.time(),
        'request_headers': dict(flow.request.headers),
        'response_headers': dict(flow.response.headers),
    }
    with atomic_filename(mdpath, mode='xt', encoding='utf-8') as f:
      json.dump(md, f, indent=2)
      f.flush()
      with atomic_filename(contentpath, mode='xb') as cf:
        cf.write(content)
    os.system(f"ls -la '{basedir}'/")

if __name__ == '__main__':
  sitemap = SiteMap()
  sitemap.url_cache_key = trace(
      lambda self, url: url.replace('/', '_'),
      retval=True,
  )
  cache = ContentCache(fspath='content')
  cache.cache_response('foo', sitemap)
