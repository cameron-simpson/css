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
from threading import Thread
from typing import Callable, Iterable, Mapping, Optional

from mitmproxy import ctx, http
from mitmproxy.options import Options
##from mitmproxy.proxy.config import ProxyConfig
##from mitmproxy.proxy.server import ProxyServer
from mitmproxy.tools.dump import DumpMaster

from typeguard import typechecked

from cs.cmdutils import vprint
from cs.deco import attr, Promotable, promote
from cs.fileutils import atomic_filename
from cs.lex import r, s, tabulate
from cs.logutils import warning
from cs.pfx import Pfx, pfx_call
from cs.progress import Progress
from cs.py.func import funccite
from cs.queues import IterableQueue
from cs.resources import RunState, uses_runstate
from cs.upd import print
from cs.urlutils import URL

from . import DEFAULT_MITM_LISTEN_HOST, DEFAULT_MITM_LISTEN_PORT
from .parse import get_name_and_args
from .pilfer import Pilfer, uses_pilfer

@typechecked
def process_stream(
    consumer: Callable[Iterable[bytes], None],
    progress_name: Optional[str] = None,
    *,
    content_length: Optional[int] = None,
    name: Optional[str] = None,
) -> Callable[bytes, bytes]:
  ''' Dispatch `consumer(bsiter)` in a `Thread` to consume data from the flow.
      Return a callable to set as the response.stream in the caller.

      Parameters:
      * `consumer`: a callable accepting an iterable of `bytes` instances
      * `progress_name`: optional progress bar name, default `None`;
        do not present a progress bar if `None`
      * `content_length`: optional expected length of the data stream,
        typically supplied from the response 'Content-Length` header
      * `name`: an optional string to name the worker `Thread`,
        default from `progress_name` or the name of `consumer`
  '''
  if name is None:
    name = progress_name or funccite(consumer)
  if progress_name is None:
    progress_Q = None
  else:
    progress_Q = Progress(
        progress_name,
        total=content_length,
    ).qbar(
        itemlenfunc=len,
        incfirst=True,
        report_print=print,
    )
  data_Q = IterableQueue(name=name)
  Thread(target=consumer, args=(data_Q,), name=name).start()

  def copy_bs(bs: bytes) -> bytes:
    ''' Copy `bs` to the `Data_Q` and also to the `progress_Q` if not `None`.
        Return `bs` unchanged.
    '''
    try:
      if len(bs) == 0:
        data_Q.close()
        if progress_Q is not None:
          progress_Q.close()
      else:
        data_Q.put(bs)
        if progress_Q is not None:
          progress_Q.put(bs)
    except Exception as e:
      warning("%s: exception: %s", name, s(e))
    return bs

  return copy_bs

@attr(default_hooks=('requestheaders',))
def print_rq(hook_name, flow):
  rq = flow.request
  print("RQ:", rq.host, rq.port, rq.url)

@attr(default_hooks=('requestheaders', 'responseheaders'))
@uses_pilfer
def cached_flow(hook_name, flow, *, P: Pilfer = None, mode='missing'):
  ''' Insert at `"requestheaders"` and `"response"` callbacks
      to intercept a flow using the cache.
      If there is no `flow.response`, consult the cache.
      If there is a `flow.response`, update the cache.
  '''
  assert P is not None
  PR = lambda *a: print('CACHED_FLOW', hook_name, flow.request, *a)
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
      rsp = flow.response
      if getattr(flow, 'from_cache', False):
        # ignore a response we ourselves pulled form the cache
        pass
      elif flow.request.method != 'GET':
        PR("response is not from a GET, do not cache")
      elif rsp.status_code != 200:
        PR("response status_code", rsp.status_code, "is not 200, do not cache")
      else:
        # response from upstream, update the cache
        PR("to cache, cache_key", cache_key)
        content_length_s = rsp.headers.get('content-length')
        content_length = None if content_length_s is None else int(
            content_length_s
        )
        if rsp.content is None:
          # we are at the response headers
          # and will stream the content to the cache file
          assert hook_name == "responseheaders"
          rsp.stream = process_stream(
              lambda bss: cache.cache_response(
                  url,
                  cache_key,
                  bss,
                  flow.request.headers,
                  rsp.headers,
                  mode=mode,
                  decoded=False,
              ),
              f'cache {cache_key}',
              content_length=content_length,
          )

        else:
          assert hook_name == "response"
          md = cache.cache_response(
              url,
              cache_key,
              rsp.content,
              flow.request.headers,
              rsp.headers,
              mode=mode,
              decoded=True,
          )
    else:
      # probe the cache
      assert hook_name == 'requestheaders'
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
      # set the response, preempting the upstream fetch
      rsp_hdrs = dict(md.get('response_headers', {}))
      # The http.Response.make factory accepts the supplied content
      # and _encodes_ it according to the Content-Encoding header, if any.
      # But we cached the encoded content. So we pull off the Content-Encoding header
      # (keeping a note of it), make the Response, then put the header back.
      ce = rsp_hdrs.pop('content-encoding', 'identity')
      flow.response = http.Response.make(
          200,  # HTTP status code
          content,
          rsp_hdrs,
      )
      flow.response.headers['content-encoding'] = ce
      flow.from_cache = True
      PR("from cache, cache_key", cache_key)

@attr(default_hooks=('requestheaders', 'responseheaders', 'response'))
@uses_pilfer
@typechecked
def dump_flow(hook_name, flow, *, P: Pilfer = None):
  ''' Dump request information: headers and query parameters.
  '''
  assert P is not None
  PR = lambda *a: print('DUMP_FLOW', hook_name, flow.request, *a)
  rq = flow.request
  url = URL(rq.url)
  PR(rq)
  if hook_name == 'requestheaders':
    sitemap = P.sitemap_for(url)
    if sitemap is None:
      PR("no site map")
    else:
      PR("sitemap", sitemap)
    print("  Request Headers:")
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
  elif hook_name == 'responseheaders':
    print("  Response Headers:")
    for line in tabulate(*[(key, value)
                           for key, value in sorted(rsp.headers.items())]):
      print("   ", line)
  elif hook_name == 'response':
    PR("  Content:", len(flow.response.content))
  else:
    PR("  no action for hook", hook_name)

@attr(default_hooks=('responseheaders',))
@uses_pilfer
@typechecked
def save_stream(save_as_format: str, hook_name, flow, *, P: Pilfer = None):
  rsp = flow.response
  content_length_s = rsp.headers.get('content-length')
  content_length = None if content_length_s is None else int(content_length_s)
  save_as = pfx_call(P.format_string, save_as_format, flow.request.url)

  def save(bss: Iterable[bytes]):
    with atomic_filename(save_as, mode='xb') as T:
      for bs in bss:
        T.write(bs)

  rsp.stream = process_stream(
      save,
      f'{flow.request}: save {flow.response.headers["content-type"]} -> {save_as!r}',
      content_length=content_length,
  )


@attr(default_hooks=('responseheaders',))
@uses_pilfer
@typechecked
def watch_flow(hook_name, flow, *, P: Pilfer = None):
  ''' Watch data chunks from a stream flow.
  '''
  rq = flow.request
  rsp = flow.response
  PR = lambda *a: print('WATCH_FLOW', hook_name, rq, *a)
  PR("response.stream was", r(rsp.stream))

  print("  Response Headers:")
  for line in tabulate(*[(key, value)
                         for key, value in sorted(rsp.headers.items())]):
    print("   ", line)

  content_length = rsp.headers.get('content-length')
  progress_Q = Progress(
      str(rq),
      total=None if content_length is None else int(content_length),
  ).qbar(
      itemlenfunc=len,
      incfirst=True,
      report_print=print,
  )

  def watch(bs: bytes) -> bytes:
    if len(bs) == 0:
      progress_Q.close()
    else:
      progress_Q.put(bs)
    return bs

  rsp.stream = watch

@dataclass
class MITMHookAction(Promotable):

  HOOK_SPEC_MAP = {
      'cache': cached_flow,
      'dump': dump_flow,
      'print': print_rq,
      'save': save_stream,
      'watch': watch_flow,
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

  @classmethod
  def promote(cls, obj):
    ''' Promote a callable as the direct action.
    '''
    if callable(obj):
      return cls(action=obj)
    return super().promote(obj)

class MITMAddon:
  ''' A mitmproxy addon class which collects multiple actions per
      hook and calls them all in order.
  '''

  def __init__(self):
    self.hook_map = defaultdict(list)

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
        raise AttributeError(f'rejecting obsolete hook .{hook_name}')

      def call_hooks(*mitm_hook_a, **mitm_hook_kw):
        # look up the actions when we're called
        hook_actions = self.hook_map[hook_name]
        if not hook_actions:
          return
        # any exceptions from the actions
        excs = []
        # for collating any .stream functions
        stream_funcs = []
        for i, (action, action_args, action_kwargs) in enumerate(hook_actions):
          if hook_name == 'responseheaders':
            flow = mitm_hook_a[0]
            # note the initial state of the .steam attribute
            stream0 = flow.response.stream
            assert not stream0, \
                f'expected falsey flow.response.stream, got {flow.response.stream=}'
          try:
            pfx_call(
                action,
                *action_args,
                hook_name,
                *mitm_hook_a,
                **action_kwargs,
                **mitm_hook_kw,
            )
          except Exception as e:
            warning("%s: exception calling hook_action[%d]: %s", prefix, i, e)
            excs.append(e)
          if hook_name == 'responseheaders':
            # if the .stream attribute was set, append it to the
            # stream functions and reset the .stream attribute
            if flow.response.stream:
              stream_funcs.append(flow.response.stream)
              flow.response.stream = stream0
        if hook_name == 'responseheaders' and stream_funcs:
          # After the actions have run, define the stream attribute
          # to run whatever stream functions were applied.
          #
          # Because the actions do not know about each other, we
          # wrap all the stream functions in a function which chains
          # them together. If there's only one, we pass it straight
          # though without a wrapper.
          #
          # Also, if there's a action for the "response" hook we
          # append a stream function which collates the final
          # output of the stream functions and computes a `.content`
          # attribute so that the "response" action has a valid
          # `.content to access.
          #
          if self.hook_map['response']:
            # collate the final stream into a raw_content bytes instance
            content_bss = []

            def content_stream(bs: bytes) -> bytes:
              nonlocal content_bss
              if len(bs) == 0:
                # record the consumed data as the response.content
                flow.response.content = b''.join(content_bss)
                content_bss = None
              else:
                content_bss.append(bs)
              return bs

            stream_funcs.append(content_stream)

          if len(stream_funcs) == 1:

            stream, = stream_funcs

          else:

            def stream(bs: bytes) -> bytes:
              ''' Run each bytes instance through all the stream functions.
              '''
              stream_excs = []
              for stream_func in stream_funcs:
                try:
                  bs2 = stream_func(bs)
                except Exception as e:
                  warning(
                      "%s: exception calling hook_action stream_func %s: %s",
                      prefix, funccite(stream_func), e
                  )
                  stream_excs.append(e)
                  breakpoint()
                else:
                  bs = bs2
              if excs:
                if len(excs) == 1:
                  raise excs[0]
                raise ExceptionGroup(
                    f'multiple exceptions running actions for .{hook_name}',
                    excs
                )
              return bs

          flow.response.stream = stream

        if excs:
          if len(excs) == 1:
            raise excs[0]
          raise ExceptionGroup(
              f'multiple exceptions running actions for .{hook_name}', excs
          )

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
      ssl_insecure=True,
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
