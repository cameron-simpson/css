#!/usr/bin/env python3

from collections import ChainMap, defaultdict
from collections.abc import MutableMapping
from configparser import ConfigParser, UNNAMED_SECTION
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import cached_property
from itertools import chain
import os
import os.path
import shlex
import sys
from threading import Lock, RLock
from urllib.request import build_opener, HTTPBasicAuthHandler, HTTPCookieProcessor
from typing import Any, Callable, Iterable, Mapping, Optional, Tuple

import requests

from cs.app.flag import PolledFlags
from cs.deco import decorator, default_params, promote
from cs.env import envsub
from cs.excutils import logexc, LogExceptions
from cs.later import Later, uses_later
from cs.lex import r
from cs.logutils import (debug, error, warning, exception, D)
from cs.mappings import mapped_property, SeenSet
from cs.naysync import afunc, agen, async_iter, StageMode
from cs.obj import copy as obj_copy
from cs.pfx import Pfx, pfx_call
from cs.pipeline import pipeline
from cs.py.modules import import_module_name
from cs.queues import NullQueue
from cs.resources import MultiOpenMixin, RunStateMixin
from cs.seq import seq
from cs.threads import locked, HasThreadState, ThreadState
from cs.upd import print
from cs.urlutils import URL, NetrcHTTPPasswordMgr

from .format import FormatMapping
from .sitemap import SiteMap
from .urls import hrefs, srcs

from cs.debug import trace, X, r, s

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
        result, result_P = result
        yield result, result_P
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

@dataclass(kw_only=True)
class Diversions:
  ''' A collection of diversion pipelines.
  '''
  specs: Mapping[str, str] = field(default_factory=dict)
  pilfer: "Pilfer"

  def __post_init__(self):
    self._tasks = []

  @mapped_property
  def pipes(self, pipe_name):
    pipeline = PipeLineSpec.from_str(self.specs[pipe_name]
                                     ).make_pipeline(self.pilfer)

    async def discard():
      ''' Discard the output of the diversion pipeline.
      '''
      async for _ in pipeline.outq:
        pass

    self._tasks.append(asyncio.create_task(discard()))
    return pipeline

  def close(self):
    ''' Close the input queues of the existing pipelines.
    '''
    for pipeline in self.pipes.values():
      pipeline.close()

  async def join(self):
    ''' Close all the pipelines and wait for their discard atasks to complete.
    '''
    self.close()
    for task in self._tasks:
      await task

@dataclass
class Pilfer(HasThreadState, MultiOpenMixin, RunStateMixin):
  ''' State for the pilfer app.

      Notable attributes include:
      * `flush_print`: flush output after print(), default `False`.
      * `user_agent`: specify user-agent string, default `None`.
      * `user_vars`: mapping of user variables for arbitrary use.
  '''

  name: str = field(default_factory=lambda: f'Pilfer-{seq()}')
  user_vars: Mapping[str, Any] = field(
      default_factory=lambda: dict(_=None, save_dir='.')
  )
  flush_print: bool = False
  do_trace: bool = False
  flags: Mapping = field(default_factory=PolledFlags)
  user_agent: str = 'Pilfer'
  rcpaths: list[str] = field(default_factory=list)
  url_opener: Any = field(default_factory=build_opener)
  later: Later = field(default_factory=Later)

  perthread_state = ThreadState()

  DEFAULT_ACTION_MAP = {
      'hrefs': one_to_many(hrefs),
      'print': one_to_many(lambda item: (print(item), item)[-1:]),
      'srcs': one_to_many(srcs),
      'unseen': (unseen_sfunc, StageMode.STREAM),
  }

  @uses_later
  def __post_init__(self, item=None, later: Later = None):
    self.diversions = Diversions(specs=self.rc_map['pipes'], pilfer=self)
    self.url_opener.add_handler(HTTPBasicAuthHandler(NetrcHTTPPasswordMgr()))
    self.url_opener.add_handler(HTTPCookieProcessor())
    ##self._lock = Lock()
    self._lock = RLock()

  def __str__(self):
    return "%s[%s]" % (self._name, self._)

  __repr__ = __str__

  @contextmanager
  def startup_shutdown(self):
    with self.later:
      yield

  def copy(self, *a, **kw):
    ''' Convenience function to shallow copy this `Pilfer` with modifications.
    '''
    return obj_copy(self, *a, **kw)

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
    actions = dict(self.DEFAULT_ACTION_MAP)
    for action_name, action_spec in self.rc_map['actions'].items():
      with Pfx("[actions] %s = %s", action_name, action_spec):
        actions[action_name] = pfx_call(shlex.split, action_spec)
    return actions

  @mapped_property
  def pipe_specs(self, pipe_name):
    ''' An on demand mapping of `pipe_name` to `PipeLineSpec`s
        derived from `self.rc_map['pipes']`.
    '''
    pipe_spec = self.rc_map['pipes'][pipe_name]
    return PipeLineSpec.from_str(pipe_spec)

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

  @property

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

  ##@require(lambda kw: all(isinstance(v, str) for v in kw))
  def set_user_vars(self, **kw):
    ''' Update self.user_vars from the keyword arguments.
    '''
    self.user_vars.update(kw)

  def copy_with_vars(self, **kw):
    ''' Make a copy of `self` with copied .user_vars, update the
        vars and return the copied Pilfer.
    '''
    P = self.copy('user_vars')
    P.set_user_vars(**kw)
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
        saveas = os.path.join(save_dir, U.basename)
        if saveas.endswith('/'):
          saveas += 'index.html'
      if saveas == '-':
        outfd = os.dup(sys.stdout.fileno())
        content = U.content
        with self._lock:
          with os.fdopen(outfd, 'wb') as outfp:
            outfp.write(content)
      else:
        with Pfx(saveas):
          if not overwrite and os.path.exists(saveas):
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
