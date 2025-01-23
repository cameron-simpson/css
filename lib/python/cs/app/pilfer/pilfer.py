#!/usr/bin/env python3

import asyncio
from collections import defaultdict
from configparser import ConfigParser, UNNAMED_SECTION
from contextlib import contextmanager
import copy
from dataclasses import dataclass, field
from fnmatch import fnmatch
from functools import cached_property
import os
import os.path
from os.path import (
    abspath,
    exists as existspath,
    expanduser,
    isabs as isabspath,
    join as joinpath,
)
import shlex
import sys
from threading import RLock
from urllib.request import build_opener, HTTPBasicAuthHandler, HTTPCookieProcessor
from typing import Any, Callable, Iterable, List, Mapping, Optional, Tuple

import requests

from cs.app.flag import PolledFlags
from cs.cmdutils import vprint
from cs.deco import decorator, default_params, promote
from cs.env import envsub
from cs.excutils import logexc, LogExceptions
from cs.fs import HasFSPath, needdir
from cs.later import Later, uses_later
from cs.logutils import (debug, error, warning, exception)
from cs.mappings import mapped_property, SeenSet
from cs.naysync import agen, amap, async_iter, StageMode
from cs.obj import copy as obj_copy
from cs.pfx import Pfx, pfx_call, pfx_method
from cs.pipeline import pipeline
from cs.py.modules import import_module_name
from cs.resources import MultiOpenMixin, RunStateMixin
from cs.seq import seq
from cs.threads import locked, HasThreadState, ThreadState
from cs.upd import print
from cs.urlutils import URL, NetrcHTTPPasswordMgr

from .cache import ContentCache
from .format import FormatMapping
from .parse import import_name
from .sitemap import SiteMap
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
  ##func = trace(func)
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

@dataclass
class PseudoFlow:
  ''' A class resembling `mitmproxy`'s `http.Flow` class in basic ways
      so that I can use it with the pilfer.cache.ContentCache` class.
  '''

  request: requests.Request = None
  response: requests.Response = None

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
  user_agent: str = 'Pilfer'
  rcpaths: list[str] = field(default_factory=list)
  url_opener: Any = field(default_factory=build_opener)
  later: Later = field(default_factory=Later)
  base_actions: Mapping[str, Any] = field(
      default_factory=lambda: dict(
          cache=one_to_many(cache, with_P=True),
          hrefs=one_to_many(hrefs),
          print=one_to_many(lambda item: (print(item), item)[-1:]),
          srcs=one_to_many(srcs),
          unseen=(unseen_sfunc, StageMode.STREAM),
      )
  )
  content_cache: ContentCache = None
  _diversion_tasks: dict = field(default_factory=dict)

  @uses_later
  def __post_init__(self, item=None, later: Later = None):
    self.url_opener.add_handler(HTTPBasicAuthHandler(NetrcHTTPPasswordMgr()))
    self.url_opener.add_handler(HTTPCookieProcessor())
    if self.fspath is None:
      self.fspath = abspath(
          expanduser(
              self.rc_map[None]['var'] or os.environ.get('PILFERDIR')
              or '~/var/pilfer'
          )
      )
      needdir(self.fspath) and vprint("made", self.shortpath)
    if self.content_cache is None:
      self.content_cache = ContentCache(
          expanduser(self.rc_map[None]['cache'] or self.pathto('cache'))
      )
    ##self._lock = Lock()
    self._lock = RLock()

  def __str__(self):
    return "%s[%s]" % (self.name, self._)

  __repr__ = __str__

  @contextmanager
  def startup_shutdown(self):
    with self.later:
      with self.content_cache:
        yield

  @property
  def defaults(self):
    ''' Mapping for default values formed by cascading PilferRCs.
    '''
    return self.rc_map[None]

  @property
  def _(self):
    ''' Shortcut to this Pilfer's user_vars['_'] entry - the current item value.
    '''
    return self.user_vars['_']

  @_.setter
  def _(self, value):
    self.user_vars['_'] = value

  @property
  def url(self):
    ''' `self._` as a `URL` object.
    '''
    return URL.promote(self._)

  @cached_property
  def rc_map(self) -> Mapping[str | None, Mapping[str, str]]:
    ''' A `defaultdict` containing the merged sections from
        `self.rcpaths`, assembled in reverse order so that later
        rc files are overridden by earlier rc files.

        The unnamed sections are merged into the entry with key `None`.
    '''
    mapping = defaultdict(lambda: defaultdict(str))
    for rcpath in reversed(self.rcpaths):
      print("Pilfer.rc_map:", rcpath)
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
    return pipeline(self.later, pipe_funcs, name=name, inputs=inputs)

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
            warning("ignore unknown bare sitwemap name: %r", sitemap_spec)
            continue
        else:
          if map_name in named:
            warning("ignore previously seen map name: %r", map_name)
            continue
          map_class = import_name(map_spec)
          sitemap = map_class(name=map_name)
          named[map_name] = sitemap
        # TODO: precompile glob style pattern to regexp?
        map_list.append((pattern, sitemap))
    return map_list

  @promote
  def sitemap_for(self, url: URL):
    hostname = url.hostname
    for pattern, sitemap in self.sitemaps:
      if fnmatch(hostname, pattern):
        return sitemap
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
    if not raw:
      value = self.format_string(value, U)
    FormatMapping(self)[k] = value

  # Note: this method is _last_ because otherwise it it shadows the
  # @promote decorator, used on earlier methods.
  @classmethod
  def promote(cls, P):
    '''Promote anything to a `Pilfer`.
    '''
    if not isinstance(P, cls):
      P = cls(P)
    return P

uses_pilfer = default_params(P=Pilfer.default)

@promote
@uses_pilfer
def cache(url: URL, *, sitemap: SiteMap = None, P: Pilfer):
  if sitemap is None:
    sitemap = P.sitemap_for(url)
    if sitemap is not None:
      vprint("cache: sitemap", sitemap, "for", url)
      url_key = sitemap.url_key(url)
      if url_key is not None:
        vprint("cache", sitemap, "->", url_key)
        with P.content_cache:
          P.content_cache.cache_url(url, sitemap)
      else:
        vprint("cache", sitemap, "no key for", url)
    else:
      vprint("cache: no sitemap for", url)
  yield url
