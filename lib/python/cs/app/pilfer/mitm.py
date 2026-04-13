#!/usr/bin/env python3
#
# Hook to run a mitmproxy monitor.
#

import asyncio
from collections import defaultdict
from dataclasses import dataclass, field
from functools import cached_property, partial
from inspect import isgeneratorfunction
import logging
import os
from pprint import pprint
from signal import SIGINT
import sys
from threading import Thread
from typing import Callable, Iterable, Optional, Tuple

from icontract import require
from mitmproxy import http
import mitmproxy.addons.dumper
from mitmproxy.options import Options
##from mitmproxy.proxy.config import ProxyConfig
##from mitmproxy.proxy.server import ProxyServer
from mitmproxy.tools.dump import DumpMaster
from typeguard import typechecked

from cs.cmdutils import vprint
from cs.context import stackattrs
from cs.deco import attr, Promotable, promote, uses_verbose
from cs.fileutils import atomic_filename
from cs.fs import shortpath
from cs.fsm import CancellationError
from cs.gimmicks import Buffer
from cs.lex import get_dotted_identifier, printt, r
from cs.logutils import warning
from cs.pfx import Pfx, pfx_call, pfx_method
from cs.progress import progressbar
from cs.py.func import funccite, func_a_kw
from cs.queues import IterableQueue, WorkerQueue
from cs.resources import RunState, uses_runstate
from cs.rfc2616 import content_encodings, content_length, content_type
from cs.upd import print as upd_print
from cs.urlutils import URL

from . import DEFAULT_MITM_LISTEN_HOST, DEFAULT_MITM_LISTEN_PORT
from .parse import get_action_args, import_name
from .pilfer import Pilfer, uses_pilfer
from .prefetch import URLFetcher
from .sitemap import FlowState
from .util import decode_content

if sys.stdout.isatty():
  print = upd_print

# monkey patch so that Dumper.echo calls the desired print()
mitmproxy.addons.dumper.print = print

def consume_stream(
    consumer: Callable[Iterable[bytes], None], name=None, *, gen_attrs=None
):
  ''' Wrap `consumer` in a generator function suitable for use by a `StreamChain`.
      Return the generator function.
      If the optional `getattrs` are supplied, set them as attributes
      on the returned generator function.
  '''

  # TODO: use progress_name?

  def consumer_gen(bss: Iterable[bytes]) -> Iterable[bytes]:
    ''' A generator suitable as a `Flow` stream.
        This consumees `bss`, copying it to `consumer` via an iterable
        queue, and yielding it unchanged.
    '''
    consumeq, _ = WorkerQueue(consumer, name=name)
    try:
      for bs in bss:
        try:
          consumeq.put(bs)
        finally:
          yield bs
    except:
      # yield any tail after an exception
      yield from bss
      raise
    finally:
      consumeq.close()

  if gen_attrs:
    attr(consumer_gen, **gen_attrs)
  return consumer_gen

class StreamChain:
  ''' A single use callable wrapper for a chain of `Flow` stream functions.
      A new `StreamChain` should be constructed when setting a `Flow`'s
      `.response.stream` attribute, as it keeps state.

      Each stream function is either a callable accepting `bytes`
      and returning:
      - a `bytes`, for a `bytes`->`bytes` filter
      - an iterable of `bytes`
      - `None`, for a filter which merely inspects its inputs;
        (this last is an additional mode beyond the default `Flow` support, 
        and the initial `bytes` argument is passed through by this class)
      or a generator accepting an iterable of `bytes` and yielding `bytes`.

      Note that the generator support means that a callable accepting
      a single `bytes` can never be supplied as a generator, it
      must be an ordinary function returning an iterable of `bytes`.
  '''

  # TODO: optional progress bar - overall and per stream func
  @typechecked
  def __init__(
      self,
      stream_funcs: Iterable[Callable],
      *,
      progress_name: Optional[str] = None
  ):
    progress_name = None  # DEBUG
    self.progress_name = progress_name
    self.progressQ = None
    self.queues = []
    bs_funcs = []
    for func in stream_funcs:
      if not callable(func):
        raise ValueError(f'{func=} is not callable')
      if isgeneratorfunction(func):
        func = self.func_from_generator(func)
      # TODO: can we inspect the function to ensure it accepts a single `Buffer`?
      bs_funcs.append(func)
    self.stream_funcs = bs_funcs
    if progress_name:

      def progress_worker(q):
        for _ in progressbar(q, self.progress_name, itemlenfunc=len):
          pass

      self.progressQ = IterableQueue(name=self.progress_name)
      self.queues.append(self.progressQ)
      Thread(
          name=f'{self.progress_name} progressbar worker',
          target=progress_worker,
          args=(self.progressQ,),
      ).start()

  def __del__(self):
    for q in self.queues:
      q.close()

  def func_from_generator(
      self, genfunc: Callable[Iterable[bytes], Iterable[bytes]]
  ):
    ''' Convert a generator accepting an iterable of `bytes` into
        a conventional stream function.
        Note that this dispatches a worker `Thread`.
    '''

    def worker(qin, qout):
      ''' A worker to consume the input queue `qin` and to place
          resulting data onto the queue `qout`.
      '''
      try:
        obss = genfunc(qin)
        if self.progress_name:
          obss = progressbar(
              obss,
              f'{self.progress_name} -> {getattr(genfunc,"desc",genfunc.__name__)}',
              itemlenfunc=len,
          )
        for bs in obss:
          if len(bs) > 0:
            qout.put(bs)
      finally:
        qout.close()

    def bs_func(bs: bytes) -> Iterable[bytes]:
      ''' Receive a `bytes`, put it on the queue consumed by the generator.
          Yield waiting `bytes`es from the queue emitted from the generator.
      '''
      assert isinstance(bs, bytes)
      at_EOF = len(bs) == 0
      if at_EOF:
        # close the generator input then yield all the output
        genQ.close()
        for bs in outQ:
          assert isinstance(bs, bytes)
          if len(bs) > 0:
            yield bs
      else:
        # send bs to the generator and then yield any ready data
        genQ.put(bs)
        while not outQ.empty:
          bs = outQ.get()
          assert isinstance(bs, bytes)
          if len(bs) > 0:
            yield bs
      if at_EOF:
        yield b''

    outQ = IterableQueue()
    self.queues.append(outQ)
    # dispatch the worker, obtain genQ
    genQ, _ = WorkerQueue(worker, args=(outQ,), name=self.progress_name)
    self.queues.append(genQ)
    # return the per-bytes submitter
    return bs_func

  @typechecked
  def call_stream_func(
      self,
      stream_func: Callable[bytes, bytes | Iterable[bytes] | None],
      bs: bytes,
      stream_excs: list[Exception],
  ) -> Iterable[bytes]:
    ''' Call a single strream function with a `bytes`.
        Return an iterable of `bytes`.
        If the function raises an exception, append the exception
        to `stream_excs` and return the original `bytes`
        unchanged.
    '''
    try:
      obss = stream_func(bs)
    except Exception as e:
      warning("exception calling stream_func %s: %s", funccite(stream_func), e)
      stream_excs.append(e)
      # processing broken, pass the bytes unchanged
      obss = [bs]
    # we expect a bytes or an iterable of bytes or None
    if obss is None:
      # pass the bytes through unchanged
      obss = [bs]
    elif isinstance(obss, Buffer):
      # promote to an iterable of bytes
      obss = [obss]
    return obss

  @pfx_method
  def __call__(self, bs0: bytes) -> Iterable[bytes]:
    ''' Process a single input `bytes`, and yield partial output.
        There will always be a final `b''` to indicate EOF.
        There will never be a spurious `b''` in the pre-EOF stream.
        A spurious `b''` from the stream functions will be discarded.
    '''
    at_EOF = len(bs0) == 0
    bss = () if at_EOF else (bs0,)
    try:
      stream_excs = []
      for stream_func in self.stream_funcs:
        obss = []
        for bs in bss:
          if len(bs) == 0:
            # skip empty bytes, we will supply a final bytes later if at_EOF
            continue
          obss.extend(self.call_stream_func(stream_func, bs, stream_excs))
        if at_EOF:
          # pass the final EOF indicator
          obss.extend(self.call_stream_func(stream_func, b'', stream_excs))
        bss = obss
      for bs in bss:
        if len(bs) > 0:
          yield bs
          if self.progressQ:
            self.progressQ.put(bs)
      if stream_excs:
        raise ExceptionGroup(f'exceptions running {self}', stream_excs)
    finally:
      if at_EOF:
        yield b''

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
  rq = flow.request
  url = URL(rq.url)
  PR = partial(print, 'CACHED_FLOW', rq.method, url.short)
  if rq.method not in ('GET', 'HEAD'):
    ##PR(rq.method, "is not GET or HEAD")
    return
  if flow.response:
    rsphdrs = flow.response.headers
  # scan the sitemaps for the first one offering a key for this URL
  # extra values for use
  cache = P.content_cache
  cache_keys = P.cache_keys_for_url(url)
  if not cache_keys:
    PR("no keys")
    return
  # we want to cache this request (or use the cache for it)
  with cache:
    if flow.response:
      # we've already got the response, use it to update the cache
      assert hook_name in ('responseheaders', 'response')
      if getattr(flow, 'from_cache', False):
        # ignore a response we ourselves pulled from the cache
        pass
      elif rq.method != 'GET':
        PR("response is not from a GET, do not cache")
      elif flow.response.status_code != 200:
        PR(
            "response status_code", flow.response.status_code,
            "is not 200, do not cache"
        )
      elif flow.runstate.cancelled:
        PR("flow.runstate", flow.runstate, "cancelled, do not cache")
      else:
        print("CACHE ->", ",".join(sorted(cache_keys)), url.short)
        # response from upstream, update the cache
        if hook_name == "responseheaders":
          # We are at the response headers
          # and will stream the content to the cache file.
          flow.response.stream = consume_stream(
              lambda bss: cache.cache_stream(
                  bss,
                  cache_keys,
                  url=url,
                  rq_headers=flow.request.headers,
                  rsp_headers=flow.response.headers,
                  mode=mode,
                  runstate=flow.runstate,
              ),
              name=f'cache {cache_keys}',
              gen_attrs=dict(
                  content_length=content_length(flow.response.headers)
              ),
          )
        else:
          # we are at the completed response
          # pass flow.response.content to the cache
          assert hook_name == "response"
          md = cache.cache_stream(
              [flow.response.content],
              cache_keys,
              url=url,
              rq_headers=flow.request.headers,
              rsp_headers=flow.response.headers,
              mode=mode,
          )
    else:
      # probe the cache
      assert hook_name == 'requestheaders'
      try:
        cache_key, md, content_bs = cache.find_content(cache_keys)
      except KeyError:
        # nothing cached
        print("CACHE MISS", ",".join(sorted(cache_keys)), url.short)
        # we want to cache this, remove headers which can return a 304 Not Modified
        for hdr in 'if-modified-since', 'if-none-match':
          if hdr in rq.headers:
            del rq.headers[hdr]
        return
      print("CACHE <-", ",".join(sorted(cache_keys)), url.short)
      # a known key
      if rq.method == 'HEAD':
        content_bs = b''
      # set the response, preempting the upstream fetch
      rsp_hdrs = dict(md.get('response_headers', {}))
      # The http.Response.make factory accepts the supplied content
      # and _encodes_ it according to the Content-Encoding header, if any.
      flow.response = http.Response.make(
          200,  # HTTP status code
          content_bs,
          rsp_hdrs,
      )
      flow.from_cache = True

@attr(
    default_hooks=('requestheaders', 'request', 'responseheaders', 'response')
)
@uses_pilfer
@typechecked
def reject_flow(hook_name, flow, *, P: Pilfer, **reject_kw):
  ''' Reject flows matching all of `reject_kw`.
      If all the conditions specified by `reject_kw` are matched,
      raise `MITMCancelActions` which the caller (`MITMAddon.call_hooks_for`)
      uses to cancel application of following actions.
  '''
  assert P is not None
  rq = flow.request
  url = URL(rq.url)
  PR = lambda *a: print(
      'SELECT', hook_name, flow.request.method, url.short, *a
  )
  for attr, value in reject_kw.items():
    r_value = r(value)
    with Pfx("%s=%s", attr, r_value):
      url_attr = getattr(url, attr)
      if url_attr != value:
        return
  raise MITMCancelActions(f'reject_flow: URL:{url.short}) matches {reject_kw}')

@attr(
    default_hooks=('requestheaders', 'request', 'responseheaders', 'response')
)
@uses_pilfer
@typechecked
def select_flow(hook_name, flow, *, P: Pilfer, **select_kw):
  ''' Select flows matching all of `select_kw`.
      Failue to match a condition raises `MITMCancelActions` which
      the caller (`MITMAddon.call_hooks_for`) uses to cancel
      application of following actions.
  '''
  assert P is not None
  rq = flow.request
  url = URL(rq.url)
  PR = lambda *a: print(
      'SELECT', hook_name, flow.request.method, url.short, *a
  )
  for attr, value in select_kw.items():
    r_value = r(value)
    with Pfx("%s=%s", attr, r_value):
      url_attr = getattr(url, attr)
      if url_attr != value:
        raise MITMCancelActions(
            f'select_flow: {attr}={r_value}: url.{attr}={r(url_attr)} does not match'
        )

@attr(
    default_hooks=('requestheaders', 'request', 'responseheaders', 'response')
)
@uses_pilfer
@typechecked
def dump_flow(hook_name, flow, *, P: Pilfer):
  ''' Dump request information: headers and query parameters.
  '''
  assert P is not None
  PR = lambda *a: print('DUMP_FLOW', hook_name, flow.request, *a)
  rq = flow.request
  url = URL(rq.url)
  rsp = flow.response
  PR(rq)
  if hook_name in ('requestheaders', 'responseheaders'):
    sitemap = P.sitemap_for(url)
    if sitemap is None:
      PR("  no site map for URL")
    else:
      PR(
          "  URL sitemap",
          sitemap,
      )
    print("  Request Headers:")
    printt(
        *[(key, value) for key, value in sorted(rq.headers.items())],
        indent="    ",
    )
    q = url.query_dict()
    if q:
      print("  URL query part:")
      printt(
          *[(param, repr(value)) for param, value in sorted(q.items())],
          indent="    ",
      )
    if rq.method == "POST":
      if rq.urlencoded_form:
        print("  POST query:")
        printt(
            *[
                (param, repr(value))
                for param, value in sorted(rq.urlencoded_form.items())
            ],
            indent="    ",
        )
  if hook_name == 'request':
    PR("  Request Content:", len(flow.request.content))
    ct = content_type(flow.request.headers)
    if ct and ct.content_type == 'application/json':
      pprint(flow.request.json())
  if hook_name == 'responseheaders':
    print("  Response Headers:")
    printt(
        *[(key, value) for key, value in sorted(rsp.headers.items())],
        indent="    ",
    )
  if hook_name == 'response':
    PR("  Response Content:", len(flow.response.content))
    flowstate = FlowState.from_Flow(flow)
    if rq.method == "POST":
      if flowstate.content_type == 'application/json':
        pprint(flow.response.json)
      else:
        print(flow.response.content)

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
      A traditional `mitmproxy` content handler would use the `response`
      hook, but the mere existence of such a hook causes `mitmproxy`
      to gather the content for all URLs.

      Parameters:
      * `hook_name`: the `mitmproxy` hook being fired
      * `flow`: the `Flow` for the URL
      * `pattern_type`: the name identifying the patterns to use for matches
      * `P`: the content `Pilfer` which holds the site maps

      For each `Pilfer` site map matches are obtained from `P.url_matches(pattern_type)`.
      If there are any matches a stream processor is dispatched to collect the content bytes.
      When all the content is gathered, each match's `.sitemap.{pattern_type.lower()}_content`
      method is called with `(match,flow,content_bs)`.
  '''
  # TODO: avoid prefetched from prefecthed URLs, do not prefetch multiple times
  PR = lambda *a: print(
      'PROCESS_CONTENT', pattern_type, hook_name, flow.request.method, flow.
      request.url, *a
  )
  rq = flow.request
  if rq.method != "GET":
    PR("not a GET, ignoring")
    return
  url = URL(rq.url)
  matches = list(P.url_matches(url, pattern_type))
  if not matches:
    # nothing to do
    return

  def gather_content(bss: Iterable[bytes]) -> None:
    ''' Gather the content of the URL.
        At the end, process the content against each match.
        We do not use the `response` hook because that would gather
        content for all URLs instead of just those of interest.
    '''
    bss2 = []
    for bs in bss:
      yield bs
    content_bs = b''.join(bss2)
    method_name = f'content_{pattern_type.lower()}'
    for match in matches:
      PR("for match", match)
      try:
        content_handler = getattr(match.sitemap, method_name)
      except AttributeError:
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
        raise

  flow.response.stream = attr(
      gather_content, desc=f'gather content for {pattern_type}'
  )

@attr(default_hooks=(
    'requestheaders',
    'responseheaders',
))
@uses_pilfer
@typechecked
def prefetch_urls(hook_name, flow, *, P: Pilfer = None):
  ''' Process the content of URLs matching `SiteMap.PREFETCH_PATTERNS`,
      queuing further URLs for fetching via the `SiteMap.content_prefetch` method.

      Prefetched URLs are requested with the "x-prefetch: no" header,
      which we use to disable this action from queuing further
      prefetches from them in an unbound cascade by setting
      `flow.x_prefetch_skip=True` when the header is found.
  '''
  assert P is not None
  rq = flow.request
  if hook_name == 'requestheaders':
    prefetch_flags = rq.headers.pop('x-prefetch', '').strip().split()
    if 'no' in prefetch_flags:
      print("PREFETCH: has x-prefetch for", rq.url, ":", prefetch_flags)
      flow.x_prefetch_skip = True
  elif hook_name == 'responseheaders':
    if getattr(flow, 'x_prefetch_skip', False):
      print("SKIP PREFETCH scan of", rq.url)
    else:
      process_content(hook_name, flow, 'PREFETCH', P=P)
  else:
    warning("prefetch_urls: unexpected hook_name %r", hook_name)

# TODO: a FlowState method - process_soup?
@attr(default_hooks=('responseheaders',))
@uses_pilfer
@typechecked
def patch_soup(hook_name, flow, *, P: Pilfer = None):
  ''' Patch the soup of a URL by calling all `SiteMap.patch_soup_*` methods.
  '''
  flowstate = FlowState.from_Flow(flow)

  def process_soup(bss: Iterable[bytes]) -> Iterable[bytes]:
    ''' Yield a byte stream after patching its soup.
        If the content type is not `text/html`, yield the stream unchanged.

        Gather the input `bss`, a _decoded_ iterable of bytes, into
        text and transform into BS4 `Soup`.
        Call `Pilfer.
    '''
    if flowstate.content_type != 'text/html':
      yield from iter(bss)
      return
    charset = flowstate.content_charset or 'utf-8'
    assert not flowstate.content_encodings
    # set the content so that the soup can be computed
    flowstate.set_content(b''.join(bss))
    # update the flowstate.soup
    for _ in P.run_matches(flowstate, 'soup', 'patch_soup_*'):
      pass
    # new values for the content and text in the flowstate
    flowstate.content_encoding = 'identity'
    text = str(flowstate.soup)
    content = text.encode(charset or 'utf-8')
    flowstate.set_content(content)
    flowstate.text = text
    yield content

  flow.response.stream = attr(process_soup, desc='patch soup')

@attr(default_hooks=('responseheaders',))
@uses_pilfer
@typechecked
def grok_flow(hook_name, flow, *, P: Pilfer = None):
  ''' Grok the fullness of a `Flow`.
  '''
  flowstate = FlowState.from_Flow(flow)
  # ignore URLs for which there is no SiteMap
  if not any(P.sitemaps_for_url_host(flowstate.url)):
    return

  def grok_stream(bss: Iterable[bytes]):
    ''' Fetch stream content then call `Pilfer.grok`.
    '''
    content_bs = b''.join(bss)
    flowstate.content = content_bs
    for _ in P.grok(flowstate):
      pass

  flow.response.stream = consume_stream(
      grok_stream, gen_attrs=dict(desc='grok')
  )

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

  flow.response.stream = attr(save, desc=f'save->{shortpath(save_as)!r}')

@dataclass
class MITMHookAction(Promotable):

  BUILTIN_ACTION_MAP = {
      'cache': cached_flow,
      'dump': dump_flow,
      'grok': grok_flow,
      'prefetch': prefetch_urls,
      'patch_soup': patch_soup,
      'print': print_rq,
      'reject': reject_flow,
      'save': save_stream,
      'select': select_flow,
  }

  name: str
  module_name: Optional[str] = None
  args: list = field(default_factory=list)
  kwargs: dict = field(default_factory=dict)

  def __str__(self):
    return (
        self.__class__.__name__ + ':' +
        func_a_kw(self.__call__, *self.args, **self.kwargs)
    )

  @property
  def default_hooks(self):
    return self.__call__.default_hooks

  # TODO: proper parse of hook_spec
  @classmethod
  def from_str(cls, action_spec: str, offset=0) -> "MITMHookAction":
    ''' Promote `action_spec` to a callable from `cls.BUILTIN_ACTION_MAP`.
    '''
    self, offset = cls.parse(action_spec, offset=offset)
    if offset < len(action_spec):
      raise ValueError(
          f'unparsed text after {action_spec[:offset]}: { action_spec[offset:]=}'
      )
    return self

  @classmethod
  def parse(cls, action_spec: str, offset=0) -> Tuple["MITMHookAction", int]:
    args = []
    kwargs = {}
    criteria = []
    with Pfx(offset):
      name, offset = get_dotted_identifier(action_spec, offset)
      if not name:
        raise ValueError(f'no action name')
    if action_spec.startswith(':', offset):
      module_name = name
      offset += 1
      with Pfx(offset):
        name, offset = get_dotted_identifier(action_spec, offset)
        if not name:
          raise ValueError(
              f'missing module "subname" after module {module_name+":"!r}'
          )
    else:
      module_name = None
    if action_spec.startswith('(', offset):
      # (args...)
      offset += 1
      with Pfx(offset):
        a, kw, offset = get_action_args(action_spec, offset, '@')
      with Pfx(offset):
        if not action_spec.startswith(')', offset):
          raise ValueError('missing closing bracket')
    return cls(
        name=name, module_name=module_name, args=args, kwargs=kwargs
    ), offset

  @cached_property
  def __call__(self):
    ''' The callable form of this action.
    '''
    if self.module_name is None:
      # builtin name
      action = self.BUILTIN_ACTION_MAP[self.name]
    else:
      # import module_name:name
      action = pfx_call(import_name, f'{self.module_name}:{self.name}')
    if self.args or self.kwargs:
      action = partial(action, *self.args, **self.kwargs)
    return action

  @classmethod
  def promote(cls, obj):
    ''' Promote a callable as the direct action.
    '''
    if callable(obj):
      return cls(action=obj)
    return super().promote(obj)

class MITMCancelActions(CancellationError):
  ''' The exception is raise to cancel the chain of actions
      during the `MITMAddon.call_hook_for` method.
  '''

class MITMAddon:
  ''' A mitmproxy addon class which collects multiple actions per
      hook and calls each in order.
  '''

  def __init__(self, logging_handlers=None):
    # a list of (MITMAction,criteria)
    self.hook_map = defaultdict(list)
    self.logging_handlers = logging_handlers

  @promote
  @typechecked
  def add_action(
      self,
      action: MITMHookAction,
      hook_names: Optional[Iterable[str]] = (),
      criteria: Optional[Iterable[Callable[str, bool]]] = (),
  ):
    ''' Add a `MITMHookAction` to a list of hooks for `hook_name`.
    '''
    if not hook_names:
      hook_names = action.default_hooks
      assert hook_names
    for hook_name in hook_names:
      vprint(hook_name, "+", action, criteria)
      self.hook_map[hook_name].append((action, criteria))

  def __getattr__(self, hook_name):
    ''' Return a callable which calls all the hooks for `hook_name`.
    '''
    prefix = f'{self.__class__.__name__}.{hook_name}'
    with Pfx(prefix):
      if hook_name in ('addons', 'add_log', 'clientconnect',
                       'clientdisconnect', 'serverconnect',
                       'serverdisconnect'):
        raise AttributeError(f'rejecting obsolete hook .{hook_name}')
      hook_actions = self.hook_map[hook_name]
      if not hook_actions:
        raise AttributeError(f'no actions for {hook_name=}')

      def call_hooks(*a, **kw):
        root_logger = logging.getLogger()
        with stackattrs(
            root_logger,
            handlers=(root_logger.handlers if self.logging_handlers is None
                      else list(self.logging_handlers)),
        ):
          self.call_hooks_for(hook_name, *a, **kw)

      return call_hooks

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

  @uses_verbose
  def call_hooks_for(
      self, hook_name: str, flow, *mitm_hook_a, verbose: bool, **mitm_hook_kw
  ):
    ''' This calls all the actions for the specified `hook_name`.
    '''
    rq = flow.request
    url = URL(rq.url)
    PR = lambda *a, **kw: print(
        "call_hooks_for", hook_name, rq.method, url.short, *a, **kw
    )
    PR("URL", flow.request.method, url.short)
    # look up the actions when we're called
    hook_actions = self.hook_map[hook_name]
    PR("hooks", hook_actions)
    if not hook_actions:
      PR("no hooks")
      return
    # any exceptions from the actions
    prep_excs = []
    # for collating any .stream functions during responseheaders
    stream_funcs = [] if hook_name == 'responseheaders' else None
    cancelled = False
    for i, (action, criteria) in enumerate(hook_actions):
      if criteria:
        PR(criteria)
        for criterion in criteria:
          if pfx_call(criterion, flow.request.url):
            break
        else:
          PR("SKIP, does not match URL")
          continue
      if stream_funcs is not None:
        # note the initial state of the .stream attribute
        stream0 = flow.response.stream
        assert not stream0, \
            f'expected falsey flow.response.stream, got {flow.response.stream=}'
      try:
        pfx_call(
            action,
            hook_name,
            flow,
            *mitm_hook_a,
            **mitm_hook_kw,
        )
      except MITMCancelActions as e:
        if verbose:
          PR("cancelling further actions:", e._)
        cancelled = True
      except Exception as e:
        warning("exception calling hook_action[%d]: %s", i, e)
        prep_excs.append(e)
      if stream_funcs is None:
        ##PR("stream_funcs is None")
        pass
      else:
        # If the .stream attribute was modified, append it to the
        # stream functions and reset the .stream attribute.
        stream = flow.response.stream
        if stream is not stream0:
          if stream:
            stream_funcs.append(stream)
          flow.response.stream = stream0
      # perform no further actions is we got a MITMCancelActions
      if cancelled:
        break
    if stream_funcs:
      # After the actions have run, define the stream attribute
      # to run whatever stream functions were applied.
      #
      # Because the actions do not know about each other, we
      # wrap all the stream functions in a StreamChain which chains
      # them together.
      #
      # If the content is encoded (eg compressed) we insert a
      # decoding stream function at the start.
      #
      # Also, if there's an action for the "response" hook we
      # append a stream function which collates the final
      # output of the stream functions and computes a `.content`
      # attribute so that the "response" action has a valid
      # `.content` to access.
      #
      assert hook_name == 'responseheaders'
      # Streaming may change the size of the content, drop the Content-Length header;
      # wget at least is confused (stalls!) if it's longer than the content.
      flow.response.headers.pop('content-length', None)
      # We will always pass the decompressed data to the stream stages.
      # Insert a decoder if required.
      encodings = [
          enc for enc in content_encodings(flow.response.headers)
          if enc != 'identity'
      ]
      if encodings:
        # insert a decoding pass using a copy of the headers as they are now
        stream_funcs.insert(
            0, partial(decode_content, flow.response.headers.copy())
        )
        flow.response.headers.pop('content-encoding', None)
      if self.hook_map['response']:
        # We have hooks for the response, so add a stream handler to
        # collate the final stream into a raw content bytes instance
        # and set it as flow.response.content.
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

      flow.response.stream = StreamChain(stream_funcs)

    if prep_excs:
      if len(prep_excs) == 1:
        raise prep_excs[0]
      raise ExceptionGroup(
          f'multiple exceptions running actions for .{hook_name}', prep_excs
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
    upstream_proxy_url: Optional[str] = None,
):
  ''' Asynchronous function to run a `mitmproxy.tools.dump.DumpMaster`
      proxy, listening on `listen_host:listen_port` and processing flows
      via the actions in `addon`.

      Parameters:
      * `addon`: a `MITMAddon` for processing `Flow`s
      * `upstream_proxy_url`: an optional specification of the
        upstream proxy used to fetch the originals; if `None`, fall
        back to the `$https_proxy` environment variable; pass `""`
        to use no proxy
  '''
  opts = Options(
      listen_host=listen_host,
      listen_port=listen_port,
      ssl_insecure=True,
  )
  mitm_proxy_url = f'http://{listen_host}:{listen_port}/'
  if upstream_proxy_url is None:
    upstream_proxy_url = os.environ.get('https_proxy')
  if upstream_proxy_url:
    # None or empty string
    opts.mode = (f'upstream:{upstream_proxy_url}',)
  else:
    upstream_proxy_url = None
  proxy = DumpMaster(opts)
  proxy.addons.add(addon)
  vprint(f'Starting mitmproxy listening on {listen_host}:{listen_port}.')
  on_cancel = lambda rs, transition: proxy.should_exit.set()
  runstate.fsm_callback('STOPPING', on_cancel)
  with stackattrs(
      P.state,
      proxy=proxy,
      mitm_proxy_url=mitm_proxy_url,
      upstream_proxy_url=upstream_proxy_url,
  ):
    loop = asyncio.get_running_loop()
    # TODO: this belongs in RunState.__enter_exit__
    loop.add_signal_handler(SIGINT, runstate.cancel)
    # Hold the root Pilfer open.
    with P:
      # by default, requests through the Pilfer use this mitm proxy
      with stackattrs(P.session, proxies=dict(
          http=f'{listen_host}:{listen_port}',
          https=f'{listen_host}:{listen_port}',
      )):
        try:
          prefetcher = URLFetcher(pilfer=P)
          with prefetcher as worker_task:
            with stackattrs(P.state, prefetcher=prefetcher):
              await proxy.run()  # Run inside the event loop
          await worker_task
        finally:
          loop.remove_signal_handler(SIGINT)
          vprint("Stopping mitmproxy.")
          proxy.shutdown()
          runstate.fsm_callback_discard('STOPPING', on_cancel)
