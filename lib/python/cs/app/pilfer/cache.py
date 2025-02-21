#!/usr/bin/env python3

from contextlib import contextmanager
from dataclasses import dataclass
import dbm.sqlite3
import json
import mimetypes
import os
from os.path import (
    basename,
    exists as existspath,
    isfile as isfilepath,
    join as joinpath,
    splitext,
)
from queue import Queue
from tempfile import NamedTemporaryFile
from threading import Lock, Thread
import time
from typing import Iterable, Mapping, Optional, Tuple

from icontract import require

from cs.cmdutils import vprint
from cs.context import stackattrs
from cs.deco import promote
from cs.fs import HasFSPath, needdir, shortpath, validate_rpath
from cs.hashutils import BaseHashCode
from cs.logutils import warning
from cs.pfx import pfx_call
from cs.progress import progressbar
from cs.queues import IterableQueue
from cs.resources import MultiOpenMixin, RunState, uses_runstate
from cs.urlutils import URL

from typeguard import typechecked

from .sitemap import SiteMap

@dataclass
class ContentCache(HasFSPath, MultiOpenMixin):

  # present a progress bar for objects of 200KiB or more
  PROGRESS_THRESHOLD = 204800

  fspath: str
  hashname: str = 'blake3'

  @typechecked
  def __post_init__(self):
    self.hashclass = BaseHashCode.hashclass(self.hashname)
    self._query_lock = Lock()

  def _worker(self, dbmpath, inq, outq):
    ''' Worker thread to do db access, since SQLite requires all
        this to happen in the same thread.
    '''
    with dbm.sqlite3.open(dbmpath, 'c') as cache_map:
      for token, rq in inq:
        result = None
        try:
          if rq is None:
            # look up the current keys
            # TODO: decode in an undbmkey() method?
            result = tuple((self.undbmkey(k) for k in cache_map.keys()))
          elif isinstance(rq, str):
            # query a key
            key = rq
            assert isinstance(key, str)
            result = cache_map.get(self.dbmkey(key), None)
            if result is not None:
              result = json.loads(result.decode('utf-8'))
          elif isinstance(rq, tuple):
            # set the value for a key
            key, value = rq
            assert isinstance(key, str)
            assert isinstance(value, dict)
            cache_map[self.dbmkey(key)] = json.dumps(value).encode('utf-8')
          else:
            warning("%s: discarding unhandled request %s", self, r(rq))
        finally:
          outq.put((token, result))

  @typechecked
  def _query(self, rq: Tuple[str, dict] | str | None):
    token = object()
    with self._query_lock:
      self._to_worker.put((token, rq))
      token2, result = self._from_worker.get()
    assert token2 is token
    return result

  @contextmanager
  def startup_shutdown(self):
    needdir(self.fspath) and vprint("made", self.shortpath)
    needdir(self.cached_path) and vprint("made", shortpath(self.cached_path))
    to_worker = IterableQueue()
    try:
      from_worker = Queue()
      worker = Thread(
          name=f'{self}.worker',
          target=self._worker,
          args=(self.dbmpath, to_worker, from_worker),
      )
      worker.start()
      with stackattrs(
          self,
          _to_worker=to_worker,
          _from_worker=from_worker,
      ):
        yield
    finally:
      to_worker.close()

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

  def undbmkey(self, key_bs: bytes) -> str:
    ''' The `str` form of a binary key: decode using UTF-8.
    '''
    return key_bs.decode('utf-8')

  def __iter__(self):
    ''' Return an iterator of the cache keys.
    '''
    return iter(self._query(None))

  def keys(self):
    ''' Get the keys by iteration.
    '''
    return iter(self)

  @trace
  def find(self, keys: Iterable[str]) -> Mapping | None:
    ''' Search the cache for entries from `keys`.
        Return `(k,md)` for the first key found where `k` is the
        matching key and `md` is the metadata entry.
        Return `(None,None)` if no key matches.
    '''
    for k in keys:
      value = self._query(k)
      if value is not None:
        return k, value
    return None, None

  @typechecked
  def __getitem__(self, key: str | Iterable[str]) -> dict:
    ''' Return the first existing metadata entry from `key`.
    '''
    if isinstance(key, str):
      keys = key,
    else:
      keys = tuple(key)
    k, md = self.find(keys)
    if k is None:
      raise KeyError(keys)
    return md

  def get(self, key: str, default=None, *, mode='metadata'):
    ''' Get the metadata for for `key`, or `default` if it is missing or not valid.
        If `mode=="metadata"` then it is enough for the metadata to be present
        Otherwise the `content_rpath` must also resolve to a regular file.
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
    ''' Return the entire cached content as a single `bytes` instance.
    '''
    md = self[cache_key]
    content_rpath = md.get('content_rpath', '')
    if not content_rpath:
      raise KeyError(f'{cache_key!r}: no content file, {md=}')
    with pfx_call(open, self.cached_pathto(content_rpath), 'rb') as f:
      return f.read()

  @typechecked
  def __setitem__(self, key: str, metadata: dict):
    self._query((key, metadata))

  @staticmethod
  def cache_key_for(sitemap: SiteMap, url_key: str):
    ''' Compute the cache key from a `SiteMap` and a URL key.
        This is essentially the URL key prefixed with the sitemap name.
    '''
    site_prefix = sitemap.name.replace("/", "__")
    cache_key = f'{site_prefix}/{url_key.lstrip("/")}' if url_key else site_prefix
    validate_rpath(cache_key)
    return cache_key

  # TODO: if-modified-since mode of some kind
  @promote
  @require(lambda mode: mode in ('missing', 'modified', 'force'))
  @typechecked
  def cache_url(self,
                url: URL,
                cache_keys: Iterable[str],
                mode='missing') -> Mapping[str, dict]:
    ''' Cache the contents of `url` against `cache_keys`.
        Return a mapping of each cache key to the cached metadata.
    '''
    cache_keys = tuple(cache_keys)
    if not cache_keys:
      return
    md_map = {}
    missing_keys = []
    for cache_key in cache_keys:
      old_md = self.get(cache_key, {}, mode=mode)
      if old_md:
        # perform checks against the previous state
        # since mode!="metadata", this implies the old content_rpath exists
        if mode == 'missing':
          md_map[cache_key] = old_md
          continue
        if mode == 'modified':
          # check the etag if we have the old one
          etag = old_md.get('response_headers', {}).get('etag', '').strip()
          if etag:
            head_rsp = URL.HEAD_response
            if etag == head_rsp.headers.get('etag', '').strip():
              md_map[cache_key] = old_md
              continue
      missing_keys.append(cache_key)
    # if the cache is up to date, return immediately
    if not missing_keys:
      return md_map
    # fetch the current content
    rsp = url.GET_response
    return self.cache_response(
        url,
        cache_keys,
        rsp.content,
        rsp.request.headers,
        rsp.headers,
        mode=mode,
    )

  @promote
  def cache_response(
      self,
      url: URL,
      cache_keys: str | Iterable[str],
      content: bytes | Iterable[bytes],
      rq_headers,
      rsp_headers,
      *,
      mode: str = 'modified',
      decoded=False,
      progress_name=None,
      runstate: Optional[RunState] = None,
  ) -> Mapping[str, dict]:
    ''' Cache the `content` of a URL against `cache_keys`.
        Return a mapping of each cache key to the cached metadata.
    '''
    if isinstance(cache_keys, str):
      cache_keys = (cache_keys,)
    else:
      cache_keys = tuple(cache_keys)
    md_map = {}
    if not cache_keys:
      return md_map
    if isinstance(content, bytes):
      content = [content]
    content = iter(content)
    if progress_name is not None:
      # present a progress bar if progress_name was supplied
      content_length_s = rsp_headers.get('content-length')
      content_length = None if content_length_s is None else int(
          content_length_s
      )
      content = progressbar(
          content,
          progress_name,
          total=content_length,
          report_print=True,
      )
    if decoded:
      # we're saving the decoded content, strip this header
      if 'content-encoding' in rsp_headers:
        rsp_headers = dict(rsp_headers)
        del rsp_headers['content-encoding']
    with self:
      # new content file path
      urlbase, urlext = splitext(basename(url.path))
      content_type = rsp_headers.get('content-type', '').split(';')[0].strip()
      if content_type:
        ctext = mimetypes.guess_extension(content_type) or ''
      else:
        warning("no response Content-Type")
        ctext = ''
      # write the content file
      # only make filesystem items if all the required compute succeeds
      ext = urlext or ctext
      md_map = {}
      with NamedTemporaryFile(
          dir=self.cached_path,
          prefix='.cache_response--',
          suffix=ext,
      ) as T:
        hasher = self.hashclass.hashfunc()
        for bs in content:
          runstate.raiseif()
          T.write(bs)
          hasher.update(bs)
        T.flush()
        runstate.raiseif()
        h = self.hashclass(hasher.digest())
        # link the content to the various cache locations
        for cache_key in cache_keys:
          # partition the key into a directory part and the final component
          # used as the basis for the cache filename
          try:
            ckdir, ckbase = cache_key.rsplit('/', 1)
          except ValueError:
            ckdir = None
            ckbase = cache_key
            contentdir = self.cached_path
          else:
            contentdir = self.cached_pathto(ckdir)
            needdir(
                contentdir, use_makedirs=True
            ) and vprint("made", shortpath(contentdir))
          content_base = (
              f'{ckbase}--{urlbase or "index"}'[:128] + f'--{h}{ext}'
          )
          content_path = joinpath(contentdir, content_base)
          content_rpath = (
              content_base if ckdir is None else joinpath(ckdir, content_base)
          )
          # link the temp file to the final name
          if existspath(content_path):
            pfx_call(os.rename, T.name, content_path)
            with open(T.name, 'xb'):
              pass
          else:
            pfx_call(os.link, T.name, content_path)
          # update the metadata
          old_md = self.get(cache_key, {}, mode=mode)
          md = {
              'url': str(url),
              'unixtime': time.time(),
              'content_hash': str(h),
              'content_rpath': content_rpath,
              'request_headers': dict(rq_headers),
              'response_headers': dict(rsp_headers),
          }
          self[cache_key] = md  # cache mapping
          md_map[cache_key] = md  # return mapping
          # remove the old content file if different
          old_content_rpath = old_md.get('content_rpath', '')
          if old_content_rpath and old_content_rpath != content_rpath:
            pfx_call(os.remove, self.cached_pathto(old_content_rpath))
      return md_map

if __name__ == '__main__':
  sitemap = SiteMap()
  sitemap.url_key = lambda self, url: url.replace('/', '_')
  cache = ContentCache(fspath='content')
  cache.cache_response('foo', sitemap)
