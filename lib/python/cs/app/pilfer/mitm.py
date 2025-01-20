#!/usr/bin/env python3
#
# Hook to run a mitmproxy monitor.
#

import asyncio
from collections import defaultdict
from dataclasses import dataclass, field
from functools import partial
import os
from signal import SIGINT
from typing import Callable, Mapping

from mitmproxy import ctx, http
from mitmproxy.options import Options
##from mitmproxy.proxy.config import ProxyConfig
##from mitmproxy.proxy.server import ProxyServer
from mitmproxy.tools.dump import DumpMaster

from typeguard import typechecked

from cs.cmdutils import vprint
from cs.deco import attr, Promotable, promote
from cs.lex import tabulate
from cs.logutils import warning
from cs.pfx import Pfx, pfx_call
from cs.resources import RunState, uses_runstate
from cs.upd import print
from cs.urlutils import URL

from .parse import get_name_and_args
from .pilfer import Pilfer, uses_pilfer


def print_rq(flow):
  rq = flow.request
  print("RQ:", rq.host, rq.port, rq.url)

@attr(default_hooks=('requestheaders', 'response'))
@uses_pilfer
def cached_flow(flow, *, P: Pilfer = None, mode='missing'):
  ''' Insert at `"requestheaders"` and `"response"` callbacks
      to intercept a flow using the cache.
      If there is no `flow.response`, consult the cache.
      If there is a `flow.response`, update the cache.
  '''
  assert P is not None
  PR = lambda *a: print('CACHED_FLOW:', flow.request, *a)
  rq = flow.request
  if rq.method not in ('GET', 'HEAD'):
    PR(rq.method, "is not GET or HEAD")
    return
  url = URL(rq.url)
  sitemap = P.sitemap_for(url)
  if sitemap is None:
    PR("no site map")
    return
  url_key = sitemap.url_key(url)
  if url_key is None:
    PR("no URL key")
    return
  cache = P.content_cache
  cache_key = cache.cache_key_for(sitemap, url_key)
  with cache:
    if flow.response:
      if getattr(flow, 'from_cache', False):
        pass
      elif flow.request.method != 'GET':
        PR("response is not from a GET, do not cache")
      elif flow.response.status_code != 200:
        PR(
            "response status_code", flow.response.status_code,
            " is not 200, do not cache"
        )
      else:
        # response from upstream, update the cache
        PR("to cache, cache_key", cache_key)
        md = cache.cache_response(
            url,
            cache_key,
            flow.response.content,
            flow.request.headers,
            flow.response.headers,
            mode=mode,
        )
    else:
      # probe the cache
      md = cache.get(cache_key, {}, mode=mode)
      if not md:
        # nothing cached
        PR("not cached, pass through")
        # we want to cache this, remove headers which can return a 304 Not Modified
        for hdr in 'if-modified-since', 'if-none-match':
          if hdr in rq.headers:
            del rq.headers[hdr]
        return
      if flow.request.method == 'HEAD':
        content = b''
      else:
        try:
          content = cache.get_content(cache_key)
        except KeyError as e:
          warning("cached_flow: %s %s: %s", rq.method, rq.pretty_url, e)
          return
      # set the response, should preempt the upstream fetch
      rsp_hdrs = md.get('response_headers', {})
      flow.response = http.Response.make(
          200,  # HTTP status code
          content,
          rsp_hdrs,
      )
      flow.from_cache = True
      PR("from cache, cache_key", cache_key)

@attr(default_hooks=('requestheaders',))
@typechecked
@uses_pilfer
def dump_flow(flow, *, P: Pilfer = None):
  assert P is not None
  PR = lambda *a: print('DUMP_FLOW:', flow.request, *a)
  rq = flow.request
  url = URL(rq.url)
  sitemap = P.sitemap_for(url)
  if sitemap is None:
    PR("no site map")
    return
  PR(rq)
  print("  Headers:")
  for line in tabulate(*[(key, value)
                         for key, value in sorted(rq.headers.items())]):
    print("   ", line)
  if rq.method == "GET":
    q = url.query_dict()
    if False and q:
      print("  Query:")
      for line in tabulate(*[(param, repr(value))
                             for param, value in sorted(q.items())]):
        print("   ", line)
  elif rq.method == "POST":
    if False and rq.urlencoded_form:
      print("  Query:")
      for line in tabulate(
          *[(param, repr(value))
            for param, value in sorted(rq.urlencoded_form.items())]):
        print("   ", line)

@dataclass
class MITMHookAction(Promotable):

  HOOK_SPEC_MAP = {
      'cache': cached_flow,
      'dump': dump_flow,
      'print': print_rq,
  }

  action: Callable
  args: list = field(default_factory=list)
  kwargs: dict = field(default_factory=dict)

  @property
  def default_hooks(self):
    return self.action.default_hooks

  # TODO: proper parse of hook_spec
  @classmethod
  def from_str(cls, hook_spec: str):
    ''' Promote `hook_spec` to a callable from `cls.HOOK_SPEC_MAP`.
    '''
    name, args, kwargs, offset = get_name_and_args(hook_spec)
    if not name:
      raise ValueError(f'expected dotted identifier: {hook_spec!r}')
    if offset < len(hook_spec):
      raise ValueError(f'unparsed text after params: {hook_spec[offset:]!r}')
    return cls(action=cls.HOOK_SPEC_MAP[name], args=args, kwargs=kwargs)

  def __call__(self, *a, **kw):
    return self.action(*self.args, *a, **self.kwargs, **kw)
    return pfx_call(self.action, *self.args, *a, **self.kwargs, **kw)

@dataclass
class MITMAddon:

  hook_map: Mapping[str, list[MITMHookAction]] = field(
      default_factory=partial(defaultdict, list)
  )

  @promote
  @typechecked
  def add_action(
      self,
      hook_names: list[str] | tuple[str, ...] | None,
      hook_action: MITMHookAction,
      args,
      kwargs,
  ):
    ''' Add a `MITMHookAction` to a list of hooks for `hook_name`.
    '''
    if hook_names is None:
      hook_names = hook_action.default_hooks
    for hook_name in hook_names:
      self.hook_map[hook_name].append((hook_action, args, kwargs))

  def __getattr__(self, hook_name):
    ''' Return a callable which calls all the hooks for `hook_name`.
    '''
    prefix = f'{self.__class__.__name__}.{hook_name}'
    with Pfx(prefix):
      if hook_name in ('addons', 'add_log', 'clientconnect',
                       'clientdisconnect', 'serverconnect',
                       'serverdisconnect'):
        raise AttributeError(f'missing .{hook_name}')
      try:
        hook_actions = self.hook_map[hook_name]
      except KeyError as e:
        raise AttributeError(f'unknown hook name {hook_name=}') from e

      def call_hooks(*a, **kw):
        if not hook_actions:
          return
        last_e = None
        for i, (action, args, kwargs) in enumerate(hook_actions):
          try:
            pfx_call(action, *args, *a, **kwargs, **kw)
          except Exception as e:
            warning("%s: exception calling hook_action[%d]: %s", prefix, i, e)
            last_e = e
        if last_e is not None:
          raise last_e

      return call_hooks

  def load(self, loader):
    loader.add_option(
        name="tls_version_client_min",
        typespec=str,
        default="TLS1",
        help="Set the tls_version_client_min option.",
    )

@uses_runstate
@typechecked
async def run_proxy(
    listen_host=DEFAULT_MITM_LISTEN_HOST,
    listen_port=DEFAULT_MITM_LISTEN_PORT,
    *,
    addon: MITMAddon,
    runstate: RunState,
):
  opts = Options(
      listen_host=listen_host,
      listen_port=listen_port,
  )
  https_proxy = os.environ.get('https_proxy')
  if https_proxy:
    opts.mode = (f'upstream:{https_proxy}',)
  proxy = DumpMaster(opts)
  proxy.addons.add(addon)
  vprint(f'Starting mitmproxy listening on {listen_host}:{listen_port}.')
  on_cancel = lambda rs, transition: proxy.should_exit.set()
  runstate.fsm_callback('STOPPING', on_cancel)
  loop = asyncio.get_running_loop()
  # TODO: this belongs in RunState.__enter_exit__
  loop.add_signal_handler(SIGINT, runstate.cancel)
  try:
    await proxy.run()  # Run inside the event loop
  finally:
    loop.remove_signal_handler(SIGINT)
    vprint("Stopping mitmproxy.")
    proxy.shutdown()
    runstate.fsm_callback_discard('STOPPING', on_cancel)
