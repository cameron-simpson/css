#!/usr/bin/env python3
#
# Hook to run a mitmproxy monitor.
#

import asyncio
from collections import ChainMap, defaultdict
from dataclasses import dataclass, field
from functools import partial
from inspect import isgeneratorfunction
import os
from signal import SIGINT
import sys
from threading import Thread
from typing import Callable, Iterable, Mapping, Optional

from mitmproxy import ctx, http
import mitmproxy.addons.dumper
from mitmproxy.options import Options
##from mitmproxy.proxy.config import ProxyConfig
##from mitmproxy.proxy.server import ProxyServer
from mitmproxy.tools.dump import DumpMaster

from icontract import require
import requests
from typeguard import typechecked

from cs.cmdutils import vprint
from cs.context import stackattrs
from cs.deco import attr, Promotable, promote
from cs.fileutils import atomic_filename
from cs.lex import r, s, tabulate
from cs.logutils import warning
from cs.naysync import amap, IterableAsyncQueue
from cs.pfx import Pfx, pfx_call, pfx_method
from cs.progress import Progress
from cs.py.func import funccite
from cs.queues import IterableQueue
from cs.resources import RunState, uses_runstate
from cs.rfc2616 import content_length, content_type
from cs.upd import print as upd_print
from cs.urlutils import URL

from . import DEFAULT_MITM_LISTEN_HOST, DEFAULT_MITM_LISTEN_PORT
from .parse import get_name_and_args
from .pilfer import Pilfer, uses_pilfer

if sys.stdout.isatty():
  print = upd_print

# monkey patch so that Dumper.echo calls the desired print()
mitmproxy.addons.dumper.print = print

@require(lambda consumer: isgeneratorfunction(consumer))
@typechecked
def process_stream(
    consumer: Callable[Iterable[bytes], None],
    progress_name: Optional[str] = None,
    *,
    content_length: Optional[int] = None,
    name: Optional[str] = None,
    runstate: Optional[RunState] = None,
) -> Callable[bytes, bytes]:
  ''' Dispatch `consumer(bytes_iter)` in a `Thread` to consume data from the flow.
      Return a callable to set as the `flow.response.stream` in the caller.

      The `Flow.response.stream` attribute can be set to a callable
      which accepts a `bytes` instance as its sole callable;
      this provides no context to the stream processor.
      You can keep context my preparing that callable with a closure,
      but often it is clearer to write a generator which accepts
      an iterable of `bytes` and yields `bytes`. This function
      enables that.

      Parameters:
      * `consumer`: a callable accepting an iterable of `bytes` instances
        and returning an iterable of `bytes` instances;
        usually a generator function yielding `bytes` instances
      * `progress_name`: optional progress bar name, default `None`;
        do not present a progress bar if `None`
      * `content_length`: optional expected length of the data stream,
        typically supplied from the response 'Content-Length` header
      * `name`: an optional string to name the worker `Thread`,
        default from `progress_name` or the name of `consumer`

      For example, here is the stream setting from `stream_flow`,
      which inserts a pass through stream for responses above a
      certain size (its purpose is essentially to switch mitmproxy
      from its default "consume all and produce `.content`" mode
      to a streaming mode, important if this is being used as a real
      web browsing proxy):

          length = content_length(flow.response.headers)
          if ( flow.request.method in ('GET',)
               and (length is None or length >= threshold)
          ):
              # put the flow into streaming mode, changing nothing
              flow.response.stream = process_stream(
                  lambda bss: bss, f'stream {flow.request}', content_length=length
              )
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
  if runstate is not None:

    def cancel_Qs(rs: RunState):
      print(rs, "CANCELLED, close queues")
      data_Q.close()
      if progress_Q is not None:
        progress_Q.close()

    runstate.notify_cancel.add(cancel_Qs)

  def filter_data(Qin, Qout):
    ''' consume `data_Q` via the `consumer()` function, yield `bytes` istances.
      '''
    try:
      for obs in consumer(Qin):
        Qout.put(obs)
    finally:
      Qout.close()

  # dispatch a worker Thread to consume data_Q and put results on post_Q
  data_Q = IterableQueue(name=f'{name} -> consumer')
  post_Q = IterableQueue(name=f'{name} <- consumer')
  Thread(
      target=filter_data,
      args=(data_Q, post_Q),
      name=f'{name}: data_Q -> consumer -> post_Q',
  ).start()

  def copy_bs(bs: bytes) -> Iterable[bytes]:
    ''' Copy `bs` to the `data_Q` and also to the `progress_Q` if not `None`.
        Yield chunks from the `post_Q`.
    '''
    if len(bs) == 0:
      try:
        # end of input = shut down the streams and collect the entire post_Q
        data_Q.close()
        if progress_Q is not None:
          progress_Q.close()
        # yield the remaining data from post_Q
        for obs in post_Q:
          if len(obs) > 0:
            yield obs
      finally:
        # EOF
        yield b''
    else:
      if data_Q.closed:
        if len(bs) > 0:
          warning(
              "discarding %d bytes after close of data_Q:%s", len(bs),
              data_Q.name
          )
      else:
        data_Q.put(bs)
        if progress_Q is not None:
          progress_Q.put(bs)
        # yield any ready data from post_Q
        while not post_Q.empty():
          obs = next(post_Q)
          if len(bs) > 0:
            yield obs

  return copy_bs

@attr(default_hooks=('responseheaders',))
@uses_pilfer
@typechecked
def stream_flow(hook_name, flow, *, P: Pilfer = None, threshold=262144):
  ''' If the flow has no content-length or the length is at least
      threshold, put the lfow into streaming mode.
  '''
  assert hook_name == 'responseheaders'
  assert not flow.response.stream
  length = content_length(flow.response.headers)
  if (flow.request.method in ('GET',)
      and (length is None or length >= threshold)):
    # put the flow into streaming mode, changing nothing
    flow.response.stream = process_stream(
        lambda bss: bss,
        ##f'stream {flow.request}',
        content_length=length,
        runstate=flow.runstate,
    )

@attr(default_hooks=('requestheaders',))
def print_rq(hook_name, flow):
  rq = flow.request
  print("RQ:", rq.host, rq.port, rq.url)

@attr(default_hooks=('requestheaders', 'responseheaders'))
@uses_pilfer
def cached_flow(hook_name, flow, *, P: Pilfer = None, mode='missing'):
  ''' Insert at `"requestheaders"` and `"responseheaders"` callbacks
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
  rsp = flow.response
  if rsp:
    rsphdrs = rsp.headers
  url = URL(rq.url)
  rqhdrs = rq.headers
  # scan the sitemaps for the first one offering a key for this URL
  # extra values for use
  extra = ChainMap(rsphdrs, rqhdrs) if rsp else rqhdrs
  for sitemap in P.sitemaps_for(url):
    url_key = sitemap.url_key(url, extra=extra)
    if url_key is not None:
      break
  else:
    PR("no URL key")
    return
  cache = P.content_cache
  cache_key = cache.cache_key_for(sitemap, url_key)
  with cache:
    if rsp:
      # update the cache
      if getattr(flow, 'from_cache', False):
        # ignore a response we ourselves pulled from the cache
        pass
      elif rq.method != 'GET':
        PR("response is not from a GET, do not cache")
      elif rsp.status_code != 200:
        PR("response status_code", rsp.status_code, "is not 200, do not cache")
      elif flow.runstate.cancelled:
        PR("flow.runstate", flow.runstate, "cancelled, do not cache")
      else:
        # response from upstream, update the cache
        PR("to cache, cache_key", cache_key)
        if rsp.content is None:
          # we are at the response headers
          # and will stream the content to the cache file
          assert hook_name == "responseheaders"
          rsp.stream = process_stream(
              lambda bss: cache.cache_response(
                  url,
                  cache_key,
                  bss,
                  rqhdrs,
                  rsp.headers,
                  mode=mode,
                  decoded=False,
                  runstate=flow.runstate,
              ),
              f'cache {cache_key}',
              content_length=content_length(rsp.headers),
              runstate=flow.runstate,
          )

        else:
          assert hook_name == "response"
          md = cache.cache_response(
              url,
              cache_key,
              rsp.content,
              rqhdrs,
              rsphdrs,
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
      if rq.method == 'HEAD':
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
  rsp = flow.response
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

@require(lambda flow: not flow.response.stream)
@typechecked
def process_content(hook_name: str, flow, pattern_type: str, *, P: Pilfer):
  ''' The generic operation of conditionally processing the content of a URL.

      This is aimed at gathering the content and calling handlers
      for the content based on the URL matches. If there are no
      matches to the URL, the content is not gathered.

      It is written to be called from `responseheaders` hook so
      that the gathering of content can be conditional on matching
      the URL.
      A traditional `mitmproxy` content handlwr would use the `response`
      hook, but the mere existence of such a hook causes `mitmproxy`
      to gather the content for all URLs.

      Parameters:
      * `hook_name`: the `mitmproxy` hook being fired
      * `flow`: the `Flow` for the URL
      * `pattern_type`: the name identifying the patterns to use formatches
      * `P`: the content `Pilfer` which holds the site maps

      For each `Pilfer` site map matches are obtained from `P.url_matches(pattern_type)`.
      If there are any matches a stream processor is dispatched to collect the content bytes.
      When all the content is gathered, each match's `.sitemap.{pattern_type.lower()}_content`
      method is called with `(match,flow,content_bs)`.
  '''
  # TODO: avoid prefetched from prefecthed URLs, do not prefetch multiple times
  PR = lambda *a: print(
      'PROCESS_CONTENT', pattern_type, hook_name, flow.request, *a
  )
  rq = flow.request
  url = URL(rq.url)
  if rq.method != "GET":
    return
  matches = list(P.url_matches(url, pattern_type))
  if not matches:
    # nothing to do
    return

  def gather_content(bss):
    ''' Gather the content of the URL.
        At the end, process the content against each match.
        We do not use the `response` hook because that would gather
        content for all URLs instead of just those of interest.
        We process the stream content before yielding the find `b''` EOF marker.
    '''
    try:
      chunks = []
      for bs in bss:
        if len(bs) == 0:
          break
        chunks.append(bs)
        yield bs
      content_bs = b''.join(chunks)
      method_name = f'content_{pattern_type.lower()}'
      for match in matches:
        try:
          content_handler = getattr(match.sitemap, method_name)
        except AttributeError as e:
          warning(
              "no %s on match.sitemap of %s",
              f'{match.sitemap.__class__.__name__}.{method_name}',
              match,
          )
          continue
        if not callable(content_handler):
          warning(
              "%s is not callable on match of %s",
              f'{match.sitemap.__class__.__name__}.{method_name}',
              match,
          )
          continue
        try:
          pfx_call(content_handler, match, flow, content_bs)
        except Exception as e:
          warning("match function %s fails: %e", match.pattern_arg, e)
    finally:
      # finally, send EOF
      yield b''

  flow.response.stream = process_stream(
      gather_content, f'gather content for {pattern_type}'
  )

@attr(default_hooks=('responseheaders',))
@uses_pilfer
@typechecked
def prefetch_urls(hook_name, flow, *, P: Pilfer = None):
  assert P is not None
  process_content(hook_name, flow, 'PREFETCH', P=P)

@attr(default_hooks=('responseheaders',))
@uses_pilfer
@typechecked
def save_stream(save_as_format: str, hook_name, flow, *, P: Pilfer = None):
  rsp = flow.response
  save_as = pfx_call(P.format_string, save_as_format, flow.request.url)

  def save(bss: Iterable[bytes]):
    with atomic_filename(save_as, mode='xb') as T:
      for bs in bss:
        T.write(bs)

  rsp.stream = process_stream(
      save,
      f'{flow.request}: save {flow.response.headers["content-type"]} -> {save_as!r}',
      content_length=content_length(rsp.headers),
      runstate=flow.runstate,
  )

@dataclass
class MITMHookAction(Promotable):

  HOOK_SPEC_MAP = {
      'cache': cached_flow,
      'dump': dump_flow,
      'prefetch': prefetch_urls,
      'print': print_rq,
      'save': save_stream,
      'stream': stream_flow,
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
    try:
      action = cls.HOOK_SPEC_MAP[name]
    except KeyError as e:
      raise ValueError(
          f'unknown action name {name!r} (not in {cls.__name__}.HOOK_SPEC_MAP)'
      ) from e
    return cls(action=action, args=args, kwargs=kwargs)

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
    ##print("MITMAddon.__getattr__", repr(hook_name))
    prefix = f'{self.__class__.__name__}.{hook_name}'
    with Pfx(prefix):
      if hook_name in ('addons', 'add_log', 'clientconnect',
                       'clientdisconnect', 'serverconnect',
                       'serverdisconnect'):
        raise AttributeError(f'rejecting obsolete hook .{hook_name}')
      hook_actions = self.hook_map[hook_name]
      if not hook_actions:
        raise AttributeError(f'no actions for {hook_name=}')
      return partial(self.call_hooks_for, hook_name)

  def load(self, loader):
    loader.add_option(
        name="tls_version_client_min",
        typespec=str,
        default="TLS1",
        help="Set the tls_version_client_min option.",
    )

  def requestheaders(self, flow):
    ''' On `requestheaders`, set `flow.runstate` to a `RunState`
        then call the hooks.
        The `RunState` is not started until `responseheaders`.
    '''
    assert not hasattr(flow, 'runstate')
    flow.runstate = RunState(str(flow.request))
    self.call_hooks_for("requestheaders", flow)

  def responseheaders(self, flow):
    ''' On `responseheaders`, start `flow.runstate` then call the hooks.
    '''
    flow.runstate.start()
    self.call_hooks_for("responseheaders", flow)

  def response(self, flow):
    ''' On `response`, call the hooks then stop `flow.runstate`.
    '''
    self.call_hooks_for("response", flow)
    flow.runstate.stop()

  def error(self, flow):
    ''' On `error`, cancel `flow.runstate`, call the hooks, then stop `flow.runstate`.
    '''
    # it is possible to have an error before responseheaders
    if flow.runstate.running:
      flow.runstate.cancel()
    self.call_hooks_for("error", flow)
    if flow.runstate.running:
      flow.runstate.stop()

  @pfx_method
  def call_hooks_for(self, hook_name: str, *mitm_hook_a, **mitm_hook_kw):
    ''' This calls all the actions for the specified `hook_name`.
    '''
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
        warning("exception calling hook_action[%d]: %s", i, e)
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
      # `.content` to access.
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
        assert len(stream_funcs) > 1

        def stream(bs: bytes) -> Iterable[bytes]:
          ''' Run each `bytes` instance through all the stream functions.
          '''
          stream_excs = []
          bss = [bs]
          for stream_func in stream_funcs:
            # feed bss through the stream function, collect results for the next function
            obss = []
            for bs in bss:
              try:
                obs = stream_func(bs)
              except Exception as e:
                warning(
                    "exception calling hook_action stream_func %s: %s",
                    funccite(stream_func), e
                )
                stream_excs.append(e)
                ##breakpoint()
              else:
                if isinstance(obs, bytes):
                  obss.append(obs)
                else:
                  obss.extend(obs)
            bss = obss
          if excs:
            if len(excs) == 1:
              raise excs[0]
            raise ExceptionGroup(
                f'multiple exceptions running actions for .{hook_name}', excs
            )
          return bss

      flow.response.stream = stream

    if excs:
      if len(excs) == 1:
        raise excs[0]
      raise ExceptionGroup(
          f'multiple exceptions running actions for .{hook_name}', excs
      )

@uses_pilfer
@uses_runstate
@typechecked
async def run_proxy(
    listen_host=DEFAULT_MITM_LISTEN_HOST,
    listen_port=DEFAULT_MITM_LISTEN_PORT,
    *,
    addon: MITMAddon,
    runstate: RunState,
    P: Pilfer,
):
  opts = Options(
      listen_host=listen_host,
      listen_port=listen_port,
      ssl_insecure=True,
  )
  mitm_proxy_url = f'http://{listen_host}:{listen_port}/'
  https_proxy = os.environ.get('https_proxy')
  if https_proxy:
    upstream_proxy_url = https_proxy
    opts.mode = (f'upstream:{https_proxy}',)
  else:
    upstream_proxy_url = None
  proxy = DumpMaster(opts)
  proxy.addons.add(addon)
  vprint(f'Starting mitmproxy listening on {listen_host}:{listen_port}.')
  on_cancel = lambda rs, transition: proxy.should_exit.set()
  runstate.fsm_callback('STOPPING', on_cancel)

  async def prefetch_worker(urlQ):
    ''' Worker to fetch URLs from `urlQ` via the mitmproxy.
    '''

    @promote
    def get_url(url: URL):
      ''' Fetch `url` in streaming mode, discarding its content.
      '''
      try:
        rsp = pfx_call(
            trace(requests.get),
            url.url,
            proxies={url.scheme: mitm_proxy_url},
            stream=True,
        )
      except Exception as e:
        warning("prefetch_worker: %s", e)
      else:
        # consume the stream
        for _ in rsp.iter_content(chunk_size=8192):
          pass

    async for _ in amap(get_url, urlQ, concurrent=True, unordered=True):
      pass

  prefetchQ = IterableQueue()
  with stackattrs(
      P,
      prefetchQ=prefetchQ,
      proxy=proxy,
      mitm_proxy_url=mitm_proxy_url,
      upstream_proxy_url=upstream_proxy_url,
  ):
    loop = asyncio.get_running_loop()
    # TODO: this belongs in RunState.__enter_exit__
    loop.add_signal_handler(SIGINT, runstate.cancel)
    try:
      asyncio.create_task(prefetch_worker(prefetchQ))
      await proxy.run()  # Run inside the event loop
    finally:
      loop.remove_signal_handler(SIGINT)
      prefetchQ.close()
      vprint("Stopping mitmproxy.")
      proxy.shutdown()
      runstate.fsm_callback_discard('STOPPING', on_cancel)
