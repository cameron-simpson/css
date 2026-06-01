#!/usr/bin/env python3

from contextlib import contextmanager
from dataclasses import dataclass
import dbm.sqlite3
from functools import partial
import json
import mimetypes
import os
from os.path import (
    basename,
    exists as existspath,
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
from cs.lex import r
from cs.logutils import warning
from cs.pfx import pfx_call
from cs.progress import progressbar
from cs.queues import IterableQueue
from cs.resources import MultiOpenMixin, RunState, uses_runstate
from cs.rfc2616 import content_length, content_type
from cs.urlutils import URL

from typeguard import typechecked

from .sitemap import FlowState, SiteMap

MIN_DURATION = 1.0  # minimum duration for a cache entry

@dataclass
class ContentCache(HasFSPath, MultiOpenMixin):
  ''' A cache for URL content.
  '''

  # present a progress bar for objects of 200KiB or more
  PROGRESS_THRESHOLD = 204800

  # query key for deletion, the actual key will be the payload
  _DELETE = object()

  fspath: str
  hashname: str = 'blake3'

  @typechecked
  def __post_init__(self):
    self.hashclass = BaseHashCode.hashclass(self.hashname)
    self._query_lock = Lock()

  def _sqlite_worker(self, dbmpath, inq, outq):
    ''' Worker thread to do db access, since SQLite requires all
        this to happen in the same thread.
    '''
    DELETE = self._DELETE
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
            if rq[0] is DELETE:
              # delete an entry
              _, key = rq
              dbkey = self.dbmkey(key)
              try:
                md_bs = cache_map[dbkey]
              except KeyError as e:
                warning("DELETE %r: %s", dbkey, e)
                result = None
              else:
                # also remove the associated cache content file
                md = json.loads(md_bs.decode('utf-8'))
                # return the deleted cache entry
                result = md
                content_rpath = md.get('content_rpath', None)
                if content_rpath is not None:
                  fspath = self.cached_pathto(content_rpath)
                  try:
                    pfx_call(os.remove, fspath)
                  except FileNotFoundError:
                    pass
                  except OSError as e:
                    warning("remove fails: %s", e)
                del cache_map[dbkey]
            else:
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
          name=f'{self} SQLite worker',
          target=self._sqlite_worker,
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

  def findall(self, keys: str | Iterable[str]) -> Iterable[Mapping]:
    ''' Search the cache for entries from `keys`,
        yield `(k,md)` for each key found where `k` is the
        matching key and `md` is the metadata entry.
        Expired entries are removed as found, and not yielded.
    '''
    if isinstance(keys, str):
      keys = keys,
    for k in keys:
      md = self._query(k)
      if md is None:
        continue
      expiry = md.get('expiry', None)
      if expiry is not None and expiry < time.time():
        del self[k]
        continue
      yield k, md

  def find(self, keys: str | Iterable[str]) -> Mapping | None:
    ''' Search the cache for entries from `keys`.
        Return `(k,md)` for the first key found where `k` is the
        matching key and `md` is the metadata entry and `md` is not expired.
        Return `(None,None)` if no key matches.
    '''
    for k, md in self.findall(keys):
      return k, md
    return None, None

  def __contains__(self, keys: str | Iterable[str] | str):
    ''' Return `True` if any key from `keys` is known to the cache.
    '''
    k, _ = self.find(keys)
    return k is not None

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

  def get(self, key: str, default=None):
    ''' Get the metadata for for `key`, or `default` if it is missing or not valid.
    '''
    try:
      return self[key]
    except KeyError:
      return default

  def find_content(
      self,
      keys: str | Iterable[str],
  ) -> Tuple[str, Mapping, bytes]:
    ''' Look for the cached content for `keys`,
        return a `(key,md,content_bs)` 3-tuple being the found `key`,
        its associated metadata `md` and the cached `content_bs` as a `bytes`.
        Raise `KeyError` if no cache files are can be read.
    '''
    for key, md in self.findall(keys):
      content_rpath = md.get('content_rpath', None)
      if content_rpath is None:
        warning("find_content: %r: no content_rpath", key)
        continue
      fspath = self.cached_pathto(content_rpath)
      try:
        with pfx_call(open, fspath, 'rb') as f:
          content_bs = f.read()
      except OSError as e:
        ##warning("find_content: %r->%r: %s", key, fspath, e)
        continue
      return key, md, content_bs
    raise KeyError(keys)

  def find_cache_fspath(
      self,
      keys: str | Iterable[str],
  ) -> Tuple[str, Mapping, str]:
    ''' Look for a cache file for `keys`,
        return a `(key,md,fspath)` 3-tuple being the found `key`,
        its associated metadata `md` and filesystem path of the cache file.
        Raise `KeyError` if no cache files are found.
    '''
    for key, md in self.findall(keys):
      content_rpath = md.get('content_rpath', None)
      if content_rpath is None:
        warning("find_content: %r: no content_rpath", key)
        continue
      fspath = self.cached_pathto(content_rpath)
      if not existspath(fspath):
        continue
      return key, md, fspath
    raise KeyError(keys)

  @typechecked
  def __setitem__(self, key: str, md: dict):
    ''' Set the entry for `key` to `md`.
    '''
    self._query((key, md))

  def __delitem__(self, key: str):
    ''' Delete the entry for key `key`.
    '''
    self._query((self._DELETE, key))

  @staticmethod
  def cache_key_for(sitemap: SiteMap, url_key: str):
    ''' Compute the cache key from a `SiteMap` and a URL key.
        This is essentially the URL key prefixed with the sitemap name.
    '''
    site_prefix = sitemap.name__
    cache_key = f'{site_prefix}/{url_key.lstrip("/")}' if url_key else site_prefix
    validate_rpath(cache_key.rstrip('/'))
    return cache_key

  # TODO: if-modified-since mode of some kind?
  @promote
  @require(lambda mode: mode in ('missing', 'modified', 'force'))
  @typechecked
  def cache_url(
      self,
      flowstate: FlowState,
      cache_keys: Iterable[str],
      mode='missing',
      duration: Optional[float] = None,
  ) -> Mapping[str, dict]:
    ''' Cache the contents of `flowstate` against `cache_keys`.
        Return a mapping of each cache key to the cached metadata.

        Parameters:
        * `flowstate`: the `FlowState` representing the URL,
          may be supplied as a URL
        * `cache_keys`: an iterable of cache keys to associate with the response
        * `mode`: the cache mode, as for `ContentCache.cache_stream`
        * `duration`: optional duration for the cache entry
    '''
    md_map = {}
    cache_keys = set(cache_keys)
    if not cache_keys:
      # early return with an empty map if there are no keys
      return md_map
    # check the validity of any keys already present
    missing_keys = []
    latest_md = None
    for cache_key in cache_keys:
      old_md = self.get(cache_key, {})
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
    return self.cache_stream(
        flowstate.iterable_content,
        cache_keys,
        url=flowstate.url,
        rq_headers=flowstate.request.headers,
        rsp_headers=flowstate.response.headers,
        mode=mode,
        duration=duration,
    )

  @promote
  @uses_runstate
  @typechecked
  def cache_stream(
      self,
      bss: Iterable[bytes],
      cache_keys: str | Iterable[str],
      *,
      url: URL,
      rq_headers: Mapping[str, str],
      rsp_headers: Mapping[str, str],
      mode: str = 'modified',
      duration: Optional[float] = None,
      progress_name: Optional[str] = None,
      runstate: RunState = None,
  ) -> Mapping[str, dict]:
    ''' Cache `bss`, the decoded content of a URL, against `cache_keys`.
        Return a mapping of each cache key to the cached metadata.

        Required parameters:
        * `bss`: an iterable of `bytes` containing the decoded content
        * `cache_keys`: an iterable of cache keys to associate with the response
        * `url`: the URL being cached
        * `rq_headers`: the request headers
        * `rsp_headers`: the response headers

        Optional parameters:
        * `mode`: cache mode, default `'modified'`, as for `ContentCache.cache_response`
        * `duration`: duration for the cache entry
        * `progress_name`: progress bar name
    '''
    PR = partial(print, "CACHE_STREAM", url.short)
    if isinstance(cache_keys, str):
      cache_keys = set((cache_keys,))
    else:
      cache_keys = set(cache_keys)
    if not cache_keys:
      raise ValueError("at least one cache key is required")
    if duration is not None and duration < MIN_DURATION:
      raise ValueError(f'invalid {duration=}, should be >={MIN_DURATION=}')
    ##PR("cache_keys =", sorted(cache_keys))
    # we're saving the decoded content, strip the Content-Encoding header
    if 'content-encoding' in rsp_headers:
      PR("strip Content-Encoding:", rsp_headers['content-encoding'])
      rsp_headers = dict(rsp_headers)
      del rsp_headers['content-encoding']
    if progress_name is not None:
      # present a progress bar if progress_name was supplied
      bss = progressbar(
          bss,
          progress_name,
          itemlenfunc=len,
          total=content_length(rsp_headers),
          report_print=True,
      )
    # create a temp file holding the content to cache
    urlbase, urlext = splitext(basename(url.path))
    ct = content_type(rsp_headers)
    if ct:
      ctext = mimetypes.guess_extension(ct.content_type) or ''
    else:
      warning("no response Content-Type")
      ctext = ''
    ext = urlext or ctext
    now = time.time()
    base_md = {
        'url': str(url),
        'unixtime': now,
        'request_headers': dict(rq_headers),
        'response_headers': dict(rsp_headers),
    }
    if duration is not None:
      base_md['expiry'] = now + duration
    md_map = {}
    # We are not using atomic_filename here
    # because we link the cached file to multiple locations.
    with NamedTemporaryFile(
        dir=self.cached_path,
        prefix='.cache_stream--',
        suffix=ext,
    ) as T:
      hasher = self.hashclass.hashfunc()
      for bs in bss:
        runstate.raiseif()
        T.write(bs)
        hasher.update(bs)
      T.flush()
      runstate.raiseif()
      h = self.hashclass(hasher.digest())
      base_md.update(content_hash=str(h))
      # link the saved content to the various cache locations
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
        try:
          pfx_call(os.remove, content_path)
        except FileNotFoundError:
          pass
        pfx_call(os.link, T.name, content_path)
        # update the metadata
        old_md = self.get(cache_key, {})
        md = dict(**base_md, content_rpath=content_rpath)
        self[cache_key] = md  # cache mapping
        md_map[cache_key] = md  # return mapping
        # remove the old content file if different
        old_content_rpath = old_md.get('content_rpath', '')
        if old_content_rpath and old_content_rpath != content_rpath:
          try:
            pfx_call(os.remove, self.cached_pathto(old_content_rpath))
          except FileNotFoundError as e:
            # this is fine
            ##warning("file not present in cache: %s", e)
            pass
    return md_map

if __name__ == '__main__':
  sitemap = SiteMap("demo")
  sitemap.url_key = lambda self, url: url.replace('/', '_')
  cache = ContentCache(fspath='content')
  cache.cache_response('foo', sitemap)
