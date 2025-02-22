#!/usr/bin/env python3

''' The URL prefetching worker.
'''

import asyncio
from dataclasses import dataclass, field
from threading import Lock
from typing import Iterable, Mapping, Optional

from cs.deco import promote
from cs.logutils import warning
from cs.naysync import IterableAsyncQueue, amap
from cs.pfx import pfx_cal, pfx_method
from cs.resources import MultiOpenMixin
from cs.result import Result
from cs.upd import print
from cs.urlutils import URL

from .pilfer import Pilfer, uses_pilfer

from cs.debug import trace, X

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
  @promote
  def get_url(self, url: URL):
    ''' Fetch `url` in streaming mode, discarding its content.
    '''
    P = self.pilfer
    cache = P.content_cache
    PR = lambda *args: print(f'get_url{url}', *args)
    cache_keys = self.pilfer.cache_keys_for_url(url)
    if not cache_keys:
      PR("no cache keys")
      return
    R = Result(f'get_url({url})')
    new_keys = []
    try:
      with self._lock:
        for cache_key in cache_keys:
          if (cache_key not in self.fetching and cache_key not in cache):
            new_keys.append(cache_key)
            self.fetching[cache_key] = R
      if not new_keys:
        PR("all cache keys currently cached or being fetched")
        return
      PR(f'cache {url} -> new_keys')
      try:
        R.run_func(
            cache.cache_response,
            url,
            new_keys,
        )
      except Exception as e:
        warning("get_url(%s): %s", url, e)
    finally:
      # clean out the fetching table
      with self._lock:
        for cache_key in new_keys:
          try:
            FR = self.fetching[cache_key]
          except KeyError:
            warning("get_url(%s):no %r in self.fetching", url, cache_key)
          else:
            if FR is R:
              del self.fetching[cache_key]
            else:
              warning(
                  "get_url(%s): self.fetching[%r] is not our Result", url,
                  cache_key
              )

  @uses_pilfer
  async def prefetch_worker(self, urlQ, *, P: Pilfer):
    ''' Worker to fetch URLs from `urlQ` via the mitmproxy.
    '''

    async for _ in amap(self.get_url, urlQ, concurrent=True, unordered=True):
      pass

  def startup_shutdown(self):
    ''' Open the fetcher, yield the worker task.
    '''
    with self.pilfer:
      q = IterableAsyncQueue()
      try:
        t = asyncio.create_task(self.prefetch_worker(q))
        yield t
      finally:
        q.close()
