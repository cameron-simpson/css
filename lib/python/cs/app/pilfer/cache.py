#!/usr/bin/env python3

from contextlib import contextmanager
from dataclasses import dataclass
import dbm.sqlite3
import json
import mimetypes
import os
from os.path import (
    basename,
    isfile as isfilepath,
    join as joinpath,
    splitext,
)
import time
from typing import Optional

from icontract import require

from cs.cmdutils import vprint
from cs.context import stackattrs
from cs.deco import promote
from cs.fs import HasFSPath, needdir, shortpath, validate_rpath
from cs.fileutils import atomic_filename
from cs.hashutils import BaseHashCode
from cs.logutils import warning
from cs.pfx import pfx_call
from cs.resources import MultiOpenMixin
from cs.urlutils import URL

from typeguard import typechecked

from .sitemap import SiteMap

@dataclass
class ContentCache(HasFSPath, MultiOpenMixin):

  fspath: str
  hashname: str = 'blake3'

  @typechecked
  def __post_init__(self):
    self.hashclass = BaseHashCode.hashclass(self.hashname)

  @contextmanager
  def startup_shutdown(self):
    needdir(self.fspath) and vprint("made", self.shortpath)
    needdir(self.cached_path) and vprint("made", shortpath(self.cached_path))
    with dbm.sqlite3.open(self.dbmpath, 'c') as cache_map:
      with stackattrs(self, cache_map=cache_map):
        yield

  @property
  def cached_path(self):
    ''' The filesystem path of the subdirectory containing cached content.
    '''
    return self.pathto('cached')

  def cached_pathto(self, cache_rpath):
    ''' Return the filesystem path of `cache_rpath` within the `self.cached_path`.
    '''
    validate_rpath(cache_rpath)
    return joinpath(self.cached_path, cache_rpath)

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

  def keys(self):
    ''' Get the keys by iteration.
    '''
    return iter(self)

  @typechecked
  def __getitem__(self, key: str) -> dict:
    return json.loads(self.cache_map[self.dbmkey(key)].decode('utf-8'))

  def get(self, key: str, default=None, *, mode='metadata'):
    ''' Get the metadata for for `key`, or `default` if it is missing or not valid.
        If `mode=="metadata"` then it is enough for the metadata to be present
        Otherwise the `contentrpath` must also resolve to a regular file.
    '''
    try:
      md = self[key]
    except KeyError:
      return default
    if mode == 'metadata':
      return md
    content_rpath = md.get('content_rpath', '')
    if not content_rpath or not isfilepath(self.cached_pathto(content_rpath)):
      return default
    return md

  def get_content(self, cache_key: str) -> bytes:
    md = self[cache_key]
    content_rpath = md.get('content_rpath', '')
    if not content_rpath:
      raise KeyError(f'{cache_key!r}: no content file, {md=}')
    with pfx_call(open, self.cached_pathto(content_rpath), 'rb') as f:
      return f.read()

  @typechecked
  def __setitem__(self, key: str, metadata: dict):
    self.cache_map[self.dbmkey(key)] = json.dumps(metadata).encode('utf-8')

  @staticmethod
  def cache_key_for(sitemap: SiteMap, url_key: str):
    ''' Compute the cache key from a `SiteMap` and a URL key.
    '''
    site_prefix = sitemap.name.replace("/", "__")
    cache_key = f'{site_prefix}/{url_key}' if url_key else site_prefix
    validate_rpath(cache_key)
    return cache_key

  # TODO: if-modified-since mode of some kind
  @promote
  @require(lambda mode: mode in ('missing', 'modified', 'force'))
  @typechecked
  def cache_url(self, url: URL, sitemap: SiteMap, mode='missing') -> dict:
    ''' Cache the contents of `flow.response` if the request URL cache key is not `None`.
        Return the resulting cache metadata for the URL.
    '''
    url_key = sitemap.url_key(url)
    if url_key is None:
      warning("no URL cache key for %r", url)
      return None
    cache_key = self.cache_key_for(sitemap, url_key)
    old_md = self.get(cache_key, {}, mode=mode)
    if old_md:
      # perform checks against the previous state
      # since mode!="metadata", this implies the old content_rpath exists
      if mode == 'missing':
        return old_md
      if mode == 'modified':
        # check the etag if we have the old one
        etag = old_md.get('response_headers', {}).get('etag', '').strip()
        if etag:
          head_rsp = URL.HEAD_response
          if etag == head_rsp.headers.get('etag', '').strip():
            return old_md
    # fetch the current content
    rsp = url.GET_response
    return self.cache_response(
        url,
        cache_key,
        rsp.content,
        rsp.request.headers,
        rsp.headers,
        old_md=old_md,
        mode=mode,
    )

  @promote
  @typechecked
  def cache_response(
      self,
      url: URL,
      cache_key: str,
      content: bytes,
      rq_headers,
      rsp_headers,
      *,
      old_md: Optional[dict] = None,
      mode: str = 'modified',
  ) -> dict:
    ''' Cache the contents of the response `rsp` against `cache_key`.
        Return the resulting cache metadata for the response.
    '''
    # we're saving the decoded content, strip this header
    # (also, it makes mitmproxy unwantedly encode a cached response)
    if 'content-encoding' in rsp_headers:
      rsp_headers = dict(rsp_headers)
      del rsp_headers['content-encoding']
    if old_md is None:
      old_md = self.get(cache_key, {}, mode=mode)
    assert isinstance(content, bytes)
    h = self.hashclass.from_data(content)
    # new content file path
    urlbase, urlext = splitext(basename(url.path))
    content_type = rsp_headers.get('content-type').split(';')[0].strip()
    if content_type:
      ctext = mimetypes.guess_extension(content_type) or ''
    else:
      warning("no request Content-Type")
      ctext = ''
    base_rpath = cache_key
    content_rpath = f'{base_rpath}/{urlbase or "index"}--{h}{urlext or ctext}'
    contentpath = self.cached_pathto(content_rpath)
    # the new metadata
    md = {
        'url': str(url),
        'unixtime': time.time(),
        'content_hash': str(h),
        'content_rpath': content_rpath,
        'request_headers': dict(rq_headers),
        'response_headers': dict(rsp_headers),
    }
    if mode == 'modified':
      hs = str(h)
      old_hs = old_md.get('content_hash', '')
      if hs == old_hs:
        vprint("CACHE: same checksum", hs)
        # update the metadata but do not replace the content file
        self[cache_key] = md
        return old_md
    old_content_rpath = old_md.get('content_rpath', '')
    if mode == 'force' or content_rpath != old_content_rpath:
      # write the content file
      basedir = joinpath(self.cached_path, base_rpath)
      # only make filesystem items if all the required compute succeeds
      needdir(
          basedir, use_makedirs=True
      ) and vprint("made", shortpath(basedir))
      with atomic_filename(
          contentpath,
          mode='xb',
          exists_ok=(content_rpath == old_content_rpath),
      ) as cf:
        cf.write(content)
    self[cache_key] = md
    if old_content_rpath and old_content_rpath != content_rpath:
      pfx_call(os.remove, self.cached_pathto(old_content_rpath))
    os.system(f'ls -ld {contentpath!r}')
    return md

if __name__ == '__main__':
  sitemap = SiteMap()
  sitemap.url_key = lambda self, url: url.replace('/', '_')
  cache = ContentCache(fspath='content')
  cache.cache_response('foo', sitemap)
