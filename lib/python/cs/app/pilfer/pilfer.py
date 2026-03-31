#!/usr/bin/env python3

import asyncio
from collections import defaultdict
from configparser import ConfigParser, UNNAMED_SECTION
from contextlib import contextmanager
import copy
from dataclasses import dataclass, field
import errno
from fnmatch import fnmatch
from functools import cached_property
from http.cookies import CookieError, Morsel
from itertools import zip_longest
import os
import os.path
from os.path import (
    abspath,
    dirname,
    exists as existspath,
    expanduser,
    isabs as isabspath,
    isdir as isdirpath,
    join as joinpath,
)
import shlex
import shutil
import sys
from threading import RLock
from urllib.request import build_opener, HTTPBasicAuthHandler, HTTPCookieProcessor
from typing import Any, Callable, Generator, Iterable, List, Mapping, Optional, Tuple
from types import SimpleNamespace as NS

import requests
from requests.cookies import RequestsCookieJar
from requests.adapters import HTTPAdapter
from typeguard import typechecked

from cs.app.flag import PolledFlags
from cs.cmdutils import vprint
from cs.context import contextif, stackattrs
from cs.deco import decorator, default_params, promote
from cs.env import envsub
from cs.excutils import logexc, LogExceptions
from cs.fileutils import atomic_filename
from cs.fs import HasFSPath, needdir, validate_rpath
from cs.later import Later, uses_later
from cs.logutils import (debug, error, warning, exception)
from cs.mappings import mapped_property, SeenSet
from cs.naysync import agen, amap, async_iter, StageMode
from cs.ndjson import dump_ndjson, scan_ndjson
from cs.pfx import Pfx, pfx_call, pfx_method
from cs.pipeline import pipeline
from cs.py.modules import import_module_name
from cs.resources import MultiOpenMixin, RunStateMixin
from cs.seq import seq
from cs.sqltags import SQLTags
from cs.tagset import TagSet
from cs.threads import locked, HasThreadState, ThreadState
from cs.upd import print
from cs.urlutils import URL, NetrcHTTPPasswordMgr

from .cache import ContentCache
from .cookies import morsel, read_firefox_cookies
from .format import FormatMapping
from .parse import import_name
from .sitemap import FlowState, SiteMap
from .urls import hrefs, srcs

@decorator
def one_to_many(func, fast=None, with_P=False, new_P=False):
  ''' A decorator for one-to-many core functions for use as a stage function.
      This produces an asynchronous generator which yields
      `(result,Pilfer)` 2-tuples from a function expecting a single
      item and producing an iterable of results.

      Decorator parameters:
      * `fast`: optional flag, passed to `@agen` when wrapping the function
      * `with_P`: optional flag, default `False`: if true, pass
        `item,Pilfer` to the function instead of just `item`
      * `new_P`: optional glag, default `False`; if true then the
        function yields `result,Pilfer` 2-tuples instead of just `result`
  '''
  if with_P:
    wrapper = agen(lambda item, P: func(item, P=P), fast=fast)
  else:
    wrapper = agen(lambda item, _: func(item), fast=fast)

  async def one_to_many_wrapper(item_P):
    item, P = item_P
    async for result in wrapper(item, P):
      if new_P:
        result_item, result_P = result
        yield result_item, result_P
      else:
        yield result, P

  return one_to_many_wrapper

async def unseen_sfunc(
    item_Ps: Iterable[Tuple[Any, "Pilfer"]],
    *,
    sig: Optional[Callable[Any, Any]] = None,
    seen=None
):
  ''' Asynchronous generator yielding unseen items from a stream
      of `(item,Pilfer)` 2-tuples.
  '''
  if sig is None:
    sig = lambda item: item
  if seen is None:
    seen = set()
  async for item, P in async_iter(item_Ps):
    item_sig = sig(item)
    if item_sig not in seen:
      seen.add(item_sig)
      yield item, P

def cache_url(item_P):
  ''' Pilfer base action for caching a UR.
      Passes theough `item_P`, a 2-tuple of `(url,Pilfer)`.
  '''
  url, P = item_P
  P.cache_url(url)
  return item_P

class PilferSession(MultiOpenMixin, HasFSPath):
  ''' A proxy for a `requests.Session` which loads and saves state.
  '''

  @typechecked
  def __init__(self, *, P: "Pilfer", key: str, **rqsession_kw):
    if os.sep in key:
      raise ValueError(f'forbidden {os.sep=} in {key=}')
    validate_rpath(key)
    self.pilfer = P
    self.key = key
    self._session = None

  def __getattr__(self, attr):
    # proxies unknown attributes to the internal requests.Session instance
    return getattr(self._session, attr)

  @property
  def fspath(self):
    ''' The filesystem path of the session state directory.
    '''
    return self.pilfer.pathto(joinpath('sessions', self.key))

  @contextmanager
  def startup_shutdown(self):
    ''' Load the cookie state from its state file, and save on exit.
    '''
    with requests.Session() as session:
      session.mount(
          'https://',
          HTTPAdapter(
              max_retries=1,
              pool_block=True,
              pool_connections=4,
              pool_maxsize=16,
          )
      )
      with stackattrs(self, _session=session):
        self.load_cookies()
        try:
          yield self
        finally:
          self.save_cookies()

  @property
  def cookiespath(self):
    ''' The filesystem path of the cookies save file.
    '''
    return self.pathto('cookies.ndjson')

  @property
  def cookies(self):
    ''' The cookie jar from the underlying `Session`.
    '''
    return self._session.cookies

  @staticmethod
  def cookie_as_morsel(cookie) -> Morsel:
    ''' Return a `http.cookies.Morsel` representing a cookie
        from `self.cookies`.
    '''
    ##pprint(cookie.__dict__)
    return morsel(
        name=cookie.name,
        value=cookie.value,
        domain=cookie.domain,
        path=cookie.path,
        expires=cookie.expires,
        httponly=cookie._rest.get('HttpOnly', False),
        samesite=False,
        secure=cookie.secure,
    )

  def add_morsel(self, morsel: Morsel):
    ''' Set the cookie from an `http.cookies.Morsel` instance.
    '''
    md = dict(morsel)
    self.cookies.set(
        morsel.key,
        morsel.value,
        comment=md['comment'],
        domain=md['domain'],
        path=md['path'],
        expires=md['expires'],
        rfc2109=True,
        secure=md['secure'],
        version=md['version'] or 0,
    )

  def load_cookies(self):
    ''' Read any saved cookies from `self.cookiespath` and update `self.cookies`.
    '''
    cookies = self.cookies
    errors = []
    try:
      with open(self.cookiespath) as f:
        for d in scan_ndjson(f, error_list=errors):
          self.add_morsel(morsel(**d))
    except FileNotFoundError:
      # no saved cookies
      pass

  def save_cookies(self):
    ''' Save `self.cookies` to `self.cookiespath`.
    '''
    cookiespath = self.cookiespath
    cookies_dirpath = dirname(cookiespath)
    needdir(cookies_dirpath)
    with atomic_filename(cookiespath, mode='w', exists_ok=True) as f:
      for cookie in self.cookies:
        try:
          m = self.cookie_as_morsel(cookie)
        except CookieError as e:
          warning("save_cookies: skip %r: %s", cookie.name, e)
          continue
        d = dict(name=m.key, value=m.value)
        d.update(dict(m))
        dump_ndjson(d, f)

@dataclass
class Pilfer(HasThreadState, HasFSPath, MultiOpenMixin, RunStateMixin):
  ''' State for the pilfer app.

      Notable attributes include:
      * `flush_print`: flush output after print(), default `False`.
      * `user_agent`: specify user-agent string, default `None`.
      * `user_vars`: mapping of user variables for arbitrary use.
  '''

  # class attribute holding the per-thread state stack
  perthread_state = ThreadState()

  name: str = field(default_factory=lambda: f'Pilfer-{seq()}')
  user_vars: Mapping[str, Any] = field(
      default_factory=lambda: dict(_=None, save_dir='.')
  )
  flush_print: bool = False
  do_trace: bool = False
  flags: Mapping = field(default_factory=PolledFlags)
  fspath: str = None
  sqltags_db_url: str = None
  user_agent: str = 'Pilfer'
  rcpaths: list[str] = field(default_factory=list)
  url_opener: Any = field(default_factory=build_opener)
  later: Later = field(default_factory=Later)
  base_actions: Mapping[str, Any] = field(
      default_factory=lambda: dict(
          cache_url=one_to_many(cache_url, with_P=True),
          hrefs=one_to_many(hrefs),
          print=one_to_many(lambda item: (print(item), item)[-1:]),
          srcs=one_to_many(srcs),
          unseen=(unseen_sfunc, StageMode.STREAM),
      )
  )
  content_cache: ContentCache = None
  # a session for storing cookies etc
  session: str | PilferSession = None
  # for optional extra things hung on the Pilfer object
  state: NS = field(default_factory=NS)
  _diversion_tasks: dict = field(default_factory=dict)

  @uses_later
  def __post_init__(self, item=None, later: Later = None):
    ''' Initialise various defaults if not initially provided.

        The follow things are set up:
        * a `NetrcHTTPPasswordMgr` is supplied as an `HTTPBasicAuthHandler`
        * `.fspath`: `$PILFERDIR` or the `var` defaults setting or `~/var/pilfer`
        * `.content_cache`: the `cache` defaults setting or the `cache` subdirectory
        * `.sqltags_db_url`: `$PILFER_SQLTAGS` or the `sqltags` defaults setting
    '''
    self.url_opener.add_handler(HTTPBasicAuthHandler(NetrcHTTPPasswordMgr()))
    # TODO: should this be in the PilferSession? find out how it is used
    self.url_opener.add_handler(HTTPCookieProcessor())
    if self.fspath is None:
      self.fspath = abspath(
          os.environ.get('PILFERDIR')
          or expanduser(self.defaults['var'] or '~/var/pilfer')
      )
      needdir(self.fspath) and vprint("made", self.shortpath)
    if self.content_cache is None:
      self.content_cache = ContentCache(
          expanduser(self.defaults['cache'] or self.pathto('cache'))
      )
    if self.sqltags_db_url is None:
      sqltags_db_url = os.environ.get('PILFER_SQLTAGS') or None
      if sqltags_db_url is None:
        sqltags_db_url = self.defaults['sqltags'] or None
        if sqltags_db_url is not None:
          if sqltags_db_url.startswith('~/'):
            sqltags_db_url = expanduser(sqltags_db_url)
          elif not sqltags_db_url.startswith(('/', 'file://', 'memory:')):
            sqltags_db_url = self.pathto(sqltags_db_url)
      self.sqltags_db_url = sqltags_db_url
    # default session named '_'
    if self.session is None:
      self.session = '_'
    # promote string to session named by the string
    if isinstance(self.session, str):
      self.session = PilferSession(P=self, key=self.session)
    ##self._lock = Lock()
    self._lock = RLock()

  def __str__(self):
    return "%s[%s]" % (self.name, self._)

  __repr__ = __str__

  def __enter_exit__(self):
    ''' Run both the inherited context managers.
    '''
    with self.session:
      for _ in zip_longest(
          MultiOpenMixin.__enter_exit__(self),
          HasThreadState.__enter_exit__(self) if self.default else (),
      ):
        yield

  @contextmanager
  def startup_shutdown(self):
    ''' Open the `Later` work queue, the `SQLTags` if specified,
        the content cache.
    '''
    with self.later:
      with contextif(self.sqltags):
        with self.content_cache:
          yield

  @property
  def defaults(self):
    ''' Mapping for default values formed by cascading `PilferRC`s.
    '''
    return self.rc_map[None]

  @property
  def _(self):
    ''' Shortcut to this `Pilfer`'s `user_vars['_']` entry - the current item value.
    '''
    return self.user_vars['_']

  @_.setter
  def _(self, value):
    self.user_vars['_'] = value

  @cached_property
  def sqltags(self):
    ''' A reference to the `SQLTags` knowledge base
        if `self.sqltags_db_url` is not `None`.
    '''
    return SQLTags(self.sqltags_db_url) if self.sqltags_db_url else None

  def dbshell(self):
    ''' Run an interactive database prompt for `self.sqltags`.
    '''
    return self.sqltags.dbshell()

  def load_browser_cookies(
      self, jar: Optional[RequestsCookieJar] = None
  ) -> RequestsCookieJar:
    ''' Load the live browser cookies into `jar`.
        If `jar` is omitted a new `RequestsCookieJar` will be made.
        Return the updated cookie jar.

        The browser cookies are obtained from the file named by
        `self.defaults['browser-cookies']`; currently this is
        expected to be a firefox `cookies.sqlite` database.
    '''
    if jar is None:
      jar = RequestsCookieJar()
    cookies_db = self.defaults['browser-cookies'] or None
    if cookies_db is not None:
      for ffcookie in read_firefox_cookies(expanduser(cookies_db)):
        ffcookie.add_to_jar(jar)
    return jar

  @property
  def url(self):
    ''' `self._` as a `URL` object.
    '''
    return URL.promote(self._)

  def request(
      self,
      url: str | URL,
      *,
      session: Optional[PilferSession] = None,
      headers=None,
      method='GET',
      verify=None,
      **rq_kw,
  ) -> requests.Response:
    ''' Fetch `url` using method (default `'GET'`), return a `requests.Response`.

        Parameters:
        * `session`: an optional `requests.Session` instance, default `self.session`
        * `headers`: optional additional headers to use, updating those from `self.headers()`
        * `method`: the HTTP method to use, default `'GET'`
        * `verify`: SSL certificate verification, passed to `session.request`,
          default from `self.verify` (itself default `True`)
        Other keyword arguments are passed to the `session` request method.
    '''
    vprint(f'{self.__class__.__name__}: {method} {url}')
    if session is None:
      session = self.session
    if verify is None:
      verify = self.verify
    # a fresh headers mapping
    hdrs = self.headers()
    if headers is not None:
      hdrs.update(headers)
    return pfx_call(
        getattr(session, method.lower()),
        str(url),
        headers=hdrs,
        verify=verify,
        **rq_kw
    )

  def GET(self, url: str | URL, **rq_kw):
    ''' Fetch `url` using the `GET` method, return a `requests.Response`.
        This is a shim for `Pilfer.request`.
    '''
    return self.request(url, method='GET', **rq_kw)

  def HEAD(self, url: str | URL, **rq_kw):
    ''' Fetch `url` using the `HEAD` method, return a `requests.Response`.
        This is a shim for `Pilfer.request`.
    '''
    return self.request(url, method='HEAD', **rq_kw)

  def OPTIONS(self, url: str | URL, **rq_kw):
    ''' Fetch `url` using the `OPTIONS` method, return a `requests.Response`.
        This is a shim for `Pilfer.request`.
    '''
    return self.request(url, method='OPTIONS', **rq_kw)

  def POST(self, url: str | URL, **rq_kw):
    ''' Fetch `url` using the `POST` method, return a `requests.Response`.
        This is a shim for `Pilfer.request`.
    '''
    return self.request(url, method='POST', **rq_kw)

  @promote
  def save(self, url: URL, savepath: str, *, makedirs=False, **get_rq_kw):
    ''' Save `url` to the filesystem path `savepath`.
        Return the `requests.Response` object.

        If the optional `makedirs` parameter is true,
        create the required intermediate directories if missing.
        Other keyword parameters are passed to `Pilfer.GET`.
    '''
    savedir = dirname(savepath)
    if makedirs and not isdirpath(savedir):
      needdir(savedir, use_makedirs=True)
    with atomic_filename(savepath, mode='wb') as T:
      rsp = self.GET(url, stream=True)
      if rsp.status_code != 200:
        raise ValueError(f'GET {url.short} {rsp.status_code=} != 200')
      for bs in rsp.iter_content(chunk_size=None):
        T.write(bs)
    return rsp

  @cached_property
  def rc_map(self) -> Mapping[str | None, Mapping[str, str]]:
    ''' A `defaultdict` containing the merged sections from
        `self.rcpaths`, assembled in reverse order so that later
        rc files are overridden by earlier rc files.

        The unnamed sections are merged into the entry with key `None`.
    '''
    mapping = defaultdict(lambda: defaultdict(str))
    for rcpath in reversed(self.rcpaths):
      vprint("Pilfer.rc_map:", rcpath)
      cfg = ConfigParser(allow_unnamed_section=True)
      try:
        pfx_call(cfg.read, rcpath)
      except (FileNotFoundError, PermissionError) as e:
        warning("ConfigParser.read(%r): %s", rcpath, e)
        continue
      msection = mapping[None]
      for field_name, value in cfg[UNNAMED_SECTION].items():
        msection[field_name] = value
      for section_name, section in cfg.items():
        msection = mapping[section_name]
        for field_name, value in section.items():
          msection[field_name] = value
    return mapping

  def normalise_header(self, header_name):
    ''' Return a header name in normalised form for use _in a request_.
    '''
    return '-'.join(
        word.title()
        for word in header_name.lower().replace('_', '-').split('-')
    )

  def headers(self):
    ''' Make a `dict` holding headers to send with a request,
        obtained from the `[headers]` rc file section.
    '''
    hdrs = {}
    for header_name, value in self.rc_map['headers'].items():
      hdrs[self.normalise_header(header_name)] = value
    return hdrs

  @cached_property
  def action_map(self) -> Mapping[str, list[str]]:
    ''' The mapping of action names to action specifications.
    '''
    actions = dict(self.base_actions)
    for action_name, action_spec in self.rc_map['actions'].items():
      with Pfx("[actions] %s = %s", action_name, action_spec):
        actions[action_name] = pfx_call(shlex.split, action_spec)
    return actions

  @mapped_property
  def actions(self, action_name: str):
    ''' A mapping of action name to `ActionSpecification`.
    '''
    from .actions import _Action
    return _Action.from_action_section(action_name, self.rc_map['actions'])

  @mapped_property
  def pipe_specs(self, pipe_name):
    ''' An on demand mapping of `pipe_name` to `PipeLineSpec`s
        derived from `self.rc_map['pipes']`.
    '''
    from .pipelines import PipeLineSpec
    pipe_spec = self.rc_map['pipes'][pipe_name]
    return PipeLineSpec.from_str(pipe_spec)

  @mapped_property
  @locked
  def diversions(self, diversion_name):
    pipeline = self.pipe_specs[diversion_name].make_pipeline(self.pilfer)

    async def discard():
      ''' Discard the output of the diversion pipeline.
      '''
      async for _ in pipeline.outq:
        ##print("diversion[%r} -> %s", diversion_name, _)
        pass

    self._diversion_tasks[diversion_name] = asyncio.create_task(discard())
    return pipeline

  async def close_diversions(self):
    ''' An asynchronous generator which closes all the pipeline diversions
        and yields each diversion name as its discard `Task` completes.
    '''
    diversions = self.diversions
    diversion_names = list(diversions.keys())

    async def close_diversion(diversion_name):
      pipeline = diversions.pop(diversion_name)
      await pipeline.close()
      task = self._diversion_tasks.pop(diversion_name)
      await task
      return diversion_name

    async for diversion_name in amap(
        close_diversion,
        diversion_names,
        concurrent=True,
        unordered=True,
    ):
      yield diversion_name

  @cached_property
  def seen_backing_paths(self):
    ''' The mapping of seenset names to the text files holding their contents.
    '''
    return self.rc_map['seen']

  @mapped_property
  def seensets(self, name):
    ''' An on demand mapping of seen set `name` to a `SeenSet`
        derived from `self.rc_map['seen']`.
    '''
    backing_path = self.rc_map['seen'].get(name)
    if backing_path is not None:
      backing_path = envsub(backing_path)
      if (not isabspath(backing_path) and not backing_path.startswith(
          ('./', '../'))):
        backing_basedir = envsub(self.defaults.get('seen_dir', '.'))
        backing_path = joinpath(backing_basedir, backing_path)
    return SeenSet(name, backing_path)

  def test_flags(self):
    ''' Evaluate the flags conjunction.

        Installs the tested names into the status dictionary as side effect.
        Note that it deliberately probes all flags instead of stopping
        at the first false condition.
    '''
    all_status = True
    flags = self.flags
    for flagname in self.flagnames:
      if flagname.startswith('!'):
        status = not flags.setdefault(flagname[1:], False)
      else:
        status = flags.setdefault(flagname[1:], False)
      if not status:
        all_status = False
    return all_status

  def seen(self, item, seenset='_'):
    ''' Test if `item` has been seen.
        The default seenset is named `'_'`.
    '''
    return item in self.seensets[seenset]

  def see(self, item, seenset='_'):
    ''' Mark an `item` as seen.
        The default seenset is named `'_'`.
    '''
    self.seensets[seenset].add(item)

  @logexc
  def pipe_through(self, pipe_name, inputs):
    ''' Create a new pipeline from the specification named `pipe_name`.
        It will collect items from the iterable `inputs`.
        `pipe_name` may be a PipeLineSpec.
    '''
    with Pfx("pipe spec %r" % (pipe_name,)):
      name = "pipe_through:%s" % (pipe_name,)
      return self.pipe_from_spec(pipe_name, inputs, name=name)

  def pipe_from_spec(self, pipe_name, name=None):
    ''' Create a new pipeline from the specification named `pipe_name`.
    '''
    from .pipelines import PipeLineSpec
    if isinstance(pipe_name, PipeLineSpec):
      spec = pipe_name
      pipe_name = str(spec)
    else:
      spec = self.pipe_specs.get(pipe_name)
      if spec is None:
        raise ValueError(f'no pipe specification named {pipe_name!r}')
    if name is None:
      name = "pipe_from_spec:%s" % (spec,)
    with Pfx(spec):
      pipe_funcs, errors = spec.pipe_funcs(self.action_map, self.do_trace)
      if errors:
        for err in errors:
          error(err)
        raise ValueError('invalid pipe specification')
    return pipeline(self.later, pipe_funcs, name=name)

  @cached_property
  @pfx_method
  def sitemaps(self) -> List[Tuple[str, SiteMap]]:
    ''' A list of `(pattern,SiteMap)` 2-tuples for matching URLs to `SiteMap`s.

        The entries take the form:

            host-pattern = name:module:class

        The `host-pattern` is a glob style pattern as for `fnmatch`.
        Site maps matching multiple hosts should generally include
        the URL hostname in the URL key.
        An additional pattern for the same `module:class` can just be `name`.

        Example:

            [sitemaps]
            docs.python.org = docs:cs.app.pilfer.sitemap:DocSite
            docs.mitmproxy.org = docs
            *.readthedocs.io = docs
    '''
    named = {}
    map_list = []
    for pattern, sitemap_spec in self.rc_map['sitemaps'].items():
      with Pfx("%s = %s", pattern, sitemap_spec):
        try:
          map_name, map_spec = sitemap_spec.split(':', 1)
        except ValueError:
          # no colon - plain map name
          try:
            sitemap = named[sitemap_spec]
          except KeyError:
            warning("ignore unknown bare sitemap name: %r", sitemap_spec)
            continue
        else:
          if map_name in named:
            warning("ignore previously seen map name: %r", map_name)
            continue
          try:
            map_class = import_name(map_spec)
          except (ImportError, SyntaxError) as e:
            warning(e._)
            continue
          sitemap = map_class(name=map_name)
          named[map_name] = sitemap
        # TODO: precompile glob style pattern to regexp?
        map_list.append((pattern, sitemap))
    return map_list

  @promote
  def sitemaps_for_url_host(self, url: URL) -> Generator[SiteMap]:
    ''' Generator yielding sitemaps which match the `url` host part.
    '''
    hostname = url.hostname
    for pattern, sitemap in self.sitemaps:
      if fnmatch(hostname, pattern):
        yield sitemap

  def sitemap_for(self, url: str | URL) -> SiteMap | None:
    ''' Return the first sitemap which matches the `url`, or `None`.
    '''
    for sitemap in self.sitemaps_for_url_host(url):
      return sitemap
    return None

  @promote
  @typechecked
  def run_matches(
      self,
      flowstate: FlowState,
      *run_match_a,
      **run_match_kw,
  ) -> Iterable[Tuple[Callable, TagSet, Any]]:
    ''' A generator to call `SiteMap.run_matches(flowstate,*run_match_a,**run_match_kw)`
        for each `SiteMap` from `self.sitemaps_for_url_host(flowstate.url)`.
        Arguments are as for `SiteMap.run_matches`.
        Yield `(method,match_tags,result)` 3-tuples from each method called.
    '''
    for sitemap in self.sitemaps_for_url_host(flowstate.url):
      yield from pfx_call(
          sitemap.run_matches, flowstate, *run_match_a, **run_match_kw
      )

  @pfx_method
  @promote
  def grok(
      self,
      flowstate: FlowState,
      flowattr: Optional[str] = None,
      **grok_kw,
  ) -> Iterable[Tuple[Callable, TagSet, Any]]:
    ''' A generator to parse information from `flowstate.url` by
        applying all matching methods from the site maps.
        Yield `(method,match_tags,grokked)` 3-tuples.
        This is a shim for `SiteMap.grok`.
    '''
    with self:
      if not (200 <= flowstate.response.status_code < 300):
        warning(
            f'{flowstate.url.short} {flowstate.response.status_code=} != 2xx, not grokking'
        )
        return
      # for each SiteMap associated with the URL host, run its grok methods
      for sitemap in self.sitemaps_for_url_host(flowstate.url):
        yield from sitemap.grok(flowstate, flowattr, **grok_kw)

  @promote
  def url_matches(self, url: URL, pattern_type: str, *, extra=None):
    ''' Scan `self.sitemaps_for_url_host(url)` for patterns matching the URL.
        Yield `SiteMapPatternMatch` instances for each match.
    '''
    for sitemap in self.sitemaps_for_url_host(url):
      patterns = getattr(sitemap, f'{pattern_type}_PATTERNS', None)
      if patterns:
        yield from sitemap.matches(url, patterns, extra=extra)

  @promote
  def url_entity(self, url: URL, *, methodglob='grok_*', **match_kw):
    for sitemap in self.sitemaps_for_url_host(url):
      entity = sitemap.url_entity(url)
      if entity is not None:
        return entity
    return None

  def _print(self, *a, **kw):
    file = kw.pop('file', None)
    if kw:
      raise ValueError(f'unexpected kwargs {kw!r}')
    with self._print_lock:
      if file is None:
        file = self._print_to if self._print_to else sys.stdout
      print(*a, file=file)
      if self.flush_print:
        file.flush()

  def copy_with_vars(self, **update_vars):
    ''' Make a copy of `self` with copied `.user_vars`, update the
        vars and return the copied `Pilfer`.
    '''
    P = copy.replace(self, vars=dict(self.user_vars))
    P.user_vars.update(update_vars)
    return P

  def print_url_string(self, U, **kw):
    ''' Print a string using approved URL attributes as the format dictionary.
        See Pilfer.format_string.
    '''
    print_string = kw.pop('string', '{_}')
    print_string = self.format_string(print_string, U)
    file = kw.pop('file', self._print_to)
    if kw:
      warning("print_url_string: unexpected keyword arguments: %r", kw)
    self._print(print_string, file=file)

  @property
  def save_dir(self):
    return self.user_vars.get('save_dir', '.')

  @promote
  def save_url(self, U: URL, saveas=None, dir=None, overwrite=False, **kw):
    ''' Save the contents of the URL `U`.
    '''
    debug(
        "save_url(U=%r, saveas=%r, dir=%s, overwrite=%r, kw=%r)...", U, saveas,
        dir, overwrite, kw
    )
    with Pfx("save_url(%s)", U):
      save_dir = self.save_dir
      if saveas is None:
        saveas = joinpath(save_dir, U.basename)
        if saveas.endswith('/'):
          saveas += 'index.html'
      if saveas == '-':
        outfd = os.dup(sys.stdout.fileno())
        content = U.content
        with self._lock:
          with os.fdopen(outfd, 'wb') as outfp:
            outfp.write(content)
      else:
        # TODO: use atomic_filename
        with Pfx(saveas):
          if not overwrite and existspath(saveas):
            warning("file exists, not saving")
          else:
            content = U.content
            if content is None:
              error("content unavailable")
            else:
              try:
                with open(saveas, "wb") as savefp:
                  savefp.write(content)
              except Exception:
                exception("save fails")
            # discard contents, releasing memory
            U.flush()

  def import_module_func(self, module_name, func_name):
    with LogExceptions():
      pylib = [
          path
          for path in envsub(self.defaults.get('pythonpath', '')).split(':')
          if path
      ]
      return import_module_name(module_name, func_name, pylib, self._lock)

  def format_string(self, s, U):
    ''' Format a string using the URL `U` as context.
        `U` will be promoted to an URL if necessary.
    '''
    return FormatMapping(self, U=U).format(s)

  def set_user_var(self, k, value, U, raw=False):
    raise RuntimeError("how is this used?")
    if not raw:
      value = self.format_string(value, U)
    FormatMapping(self)[k] = value

  @promote
  def cache_keys_for_url(self,
                         flowstate: FlowState,
                         rq_headers=None) -> set[str]:
    ''' Return a set of cache keys for a `URL` or `FlowState`.

        These are obtained by calling every `SiteMap.cache_key_*`
        method matching the `URL`.
    '''
    url = flowstate.url
    PR = lambda *a, **kw: print("cache_keys_for", url, *a, **kw)
    cache = self.content_cache
    cache_keys = set()
    with self:
      for sitemap in self.sitemaps_for_url_host(url):
        ##PR("sitemap", sitemap)
        for method, match_tags, site_cache_key in sitemap.run_matches(
            flowstate, None, 'cache_key_*'):
          if site_cache_key is not None:
            cache_keys.add(cache.cache_key_for(sitemap, site_cache_key))
    return cache_keys

  @promote
  def cache_url(self, flowstate: FlowState, mode='missing', *, extra=None):
    ''' Cache the content of a `URL` or `FlowState` in the cache
        if missing/updated as indicated by `mode`.
        Return a mapping of each cache key to the cached metadata.
    '''
    return self.content_cache.cache_url(
        flowstate, self.cache_keys_for_url(flowstate), mode=mode
    )

  @promote
  def export_url(
      self,
      url: URL,
      fspath: Optional[str] = None,
      *,
      dir='.',
      prefix: Optional[str] = '',
  ) -> str:
    ''' Export a filesystem path for `url`.
        It is safe to remove the exported path.
        If the URL is present in the cache a hard link (or failing
        that, a copy) is made from the cache file to the export
        path.
        Otherwise the URL is fetched, and cached if there are cache keys.

        The optional `fspath` parameter can be used to specify an export path,
        which must not exist already.
        If not supplied, an export path is composed by joining
        `dir` (default `'.'`) and the URL basename prefixed by
        `prefix (default `''`).
    '''
    if fspath is None:
      fspath = joinpath(dir, prefix + url.basename)
    if existspath(fspath):
      raise FileExistsError(fspath)
    cache_keys = self.cache_keys_for_url(url)
    cache = self.content_cache
    print(f'    {cache_keys}')
    try:
      cache_key, cache_md, cache_fspath = cache.find_cache_fspath(cache_keys)
    except KeyError:
      # nothing cached, fetch the URL
      flowstate = FlowState(url)
      if cache_keys:
        # fetch via the cache
        print(f'    cache_url({url},{cache_keys})')
        cached_map = cache.cache_url(flowstate, cache_keys)
        cache_key, cache_md, cache_fspath = cache.find_cache_fspath(cache_keys)
        print('    -> cache_fspath', cache_fspath)
      else:
        # fetch directly, do not bother with the cache
        print('    GET', url)
        bss = flowstate.iterable_content
        with open(fspath, 'xb') as f:
          for bs in bss:
            f.write(bs)
        print('    ->', fspath)
        return fspath
    print('    cache_fspath', cache_fspath)
    try:
      pfx_call(os.link, cache_fspath, fspath)
    except OSError as e:
      if e.errno == errno.EXDEV:
        pfx_call(shutil.copyfile, cache_fspath, fspath)
      else:
        raise
    return fspath

  # Note: this method is _last_ because otherwise it shadows the
  # @promote decorator, used on earlier methods.
  @classmethod
  def promote(cls, P):
    '''Promote anything to a `Pilfer`.
    '''
    if not isinstance(P, cls):
      P = cls(P)
    return P

uses_pilfer = default_params(P=Pilfer.default)
