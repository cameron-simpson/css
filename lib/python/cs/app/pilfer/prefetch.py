#!/usr/bin/env python3

''' The URL prefetching worker.
'''

import asyncio
from contextlib import contextmanager
from dataclasses import dataclass, field
from queue import Queue
from threading import Lock
from typing import Iterable, Mapping, Optional, Tuple, Union

from cs.context import stackattrs
from cs.logutils import warning
from cs.naysync import amap, aqiter
from cs.pfx import pfx_method
from cs.resources import MultiOpenMixin
from cs.result import Result
from cs.upd import print
from cs.urlutils import URL

from .pilfer import Pilfer, uses_pilfer


@dataclass
class URLFetcher(MultiOpenMixin):
  ''' A class for fetching URLs and caching their content.

      This expects to be used in an asynchronous function.

      Example:

          with URLFetcher() as worker_task:
            ... do stuff
          await worker_task
  '''

  # the Pilfer context, which contains the content cache
  pilfer: Pilfer = field(default_factory=Pilfer.default)
  _lock: Optional = field(default_factory=Lock)
  # a mapping of active fetches
  fetching: Mapping = field(default_factory=dict)

  @pfx_method
  def _fetch_url(self, url_params: Tuple[str | URL, Mapping]):
    ''' Fetch `url` in streaming mode, discarding its content.

        Because the purpose of the prefetch is to populate the cache,
        URLs with no cache keys or which are already cached
        are discarded.
    '''
    url, get_params = url_params
    url = URL.promote(url)
    PR = lambda *args: print(f'FETCH {url}', *args)
    P = self.pilfer
    cache = P.content_cache
    cache_keys = self.pilfer.cache_keys_for_url(url)
    if not cache_keys:
      PR("no cache keys")
      return
    R = Result(f'_fetch_url({url})')
    new_keys = []
    try:
      with self._lock:
        for cache_key in cache_keys:
          if (cache_key not in self.fetching and cache_key not in cache):
            new_keys.append(cache_key)
            self.fetching[cache_key] = R
      if not new_keys:
        ##PR("all cache keys currently cached or being fetched")
        return
      PR(f'cache -> {new_keys}')
      # TODO: if there are old keys, link their content to the new keys and skip the fetch?
      try:
        R.run_func(
            cache.cache_url,
            url,
            new_keys,
            **get_params,
        )
      except Exception as e:
        warning("_fetch_url(%s): %s", url, e)
    finally:
      # clean out the fetching table
      with self._lock:
        for cache_key in new_keys:
          try:
            FR = self.fetching[cache_key]
          except KeyError:
            warning("_fetch_url(%s):no %r in self.fetching", url, cache_key)
          else:
            if FR is R:
              del self.fetching[cache_key]
            else:
              warning(
                  "_fetch_url(%s): self.fetching[%r] is not our Result", url,
                  cache_key
              )

  @uses_pilfer
  async def prefetch_worker(
      self, urls: Iterable[Union[URL, str]], *, P: Pilfer
  ):
    ''' Worker to fetch URLs from `urlQ` via the mitmproxy.
    '''
    async for _ in amap(self._fetch_url, urls, concurrent=True,
                        unordered=True):
      pass

  @contextmanager
  def startup_shutdown(self):
    ''' Open the fetcher, yield the worker task.
    '''
    with self.pilfer:
      q = Queue()
      eoq = object()
      try:
        with stackattrs(self, _q=q):
          t = asyncio.create_task(
              self.prefetch_worker(aqiter(q, sentinel=eoq))
          )
          with stackattrs(self.pilfer.state, prefetcher=self):
            yield t
      finally:
        q.put(eoq)

  def put(self, url: Union[URL, str], get_kw: Optional[Mapping] = None):
    ''' Put `url` on the fetch queue.
    '''
    self._q.put((url, get_kw or {}))
