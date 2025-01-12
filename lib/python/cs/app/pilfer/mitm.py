#!/usr/bin/env python3
#
# Hook to run a mitmproxy monitor.
#

import asyncio
from collections import defaultdict
from dataclasses import dataclass, field
from functools import partial
from signal import SIGINT
from typing import Callable, Mapping

from mitmproxy import http
from mitmproxy.options import Options
##from mitmproxy.proxy.config import ProxyConfig
##from mitmproxy.proxy.server import ProxyServer
from mitmproxy.tools.dump import DumpMaster

from typeguard import typechecked

from cs.cmdutils import vprint
from cs.deco import Promotable, promote
from cs.logutils import warning
from cs.pfx import Pfx, pfx_call
from cs.resources import RunState, uses_runstate
from cs.upd import print

from .parse import get_name_and_args

from cs.debug import trace, X, r, s

DEFAULT_LISTEN_HOST = '127.0.0.1'
DEFAULT_LISTEN_PORT = 3131

@dataclass
class MITMHookAction(Promotable):

  HOOK_SPEC_MAP = {
      'print': print,
  }

  action: Callable
  args: list = field(default_factory=list)
  kwargs: dict = field(default_factory=dict)

  # TODO: proper parse of hook_spec
  @classmethod
  def from_str(cls, hook_spec: str):
    ''' Promote `hook_spec` to a callable from `cls.HOOK_SPEC_MAP`.
    '''
    name, args, kwargs, offset = get_name_and_args(hook_spec)
    breakpoint()
    if not name:
      raise ValueError(f'expected dotted identifier: {hook_spec!r}')
    if offset < len(hook_spec):
      raise ValueError(f'unparsed text after params: {hook_spec[offset:]!r}')
    return trace(cls)(action=cls.HOOK_SPEC_MAP[name], args=args, kwargs=kwargs)

  def __call__(self, *a, **kw):
    return trace(self.action)(*self.args, *a, **self.kwargs, **kw)
    return pfx_call(self.action, *self.args, *a, **self.kwargs, **kw)

@dataclass
class MITMAddon:

  hook_map: Mapping[str, list[MITMHookAction]] = field(
      default_factory=partial(defaultdict, list)
  )

  @promote
  @typechecked
  def add_hook(self, hook_name: str, hook_action: MITMHookAction):
    ''' Add a `MITMHookAction` to a list of hooks for `hook_name`.
    '''
    self.hook_map[hook_name].append(hook_action)

  def __getattr__(self, hook_name):
    ''' Return a callable which calls all the hooks for `hook_name`.
    '''
    prefix = f'{self.__class__.__name__}.{hook_name}'
    with Pfx(prefix):
      if hook_name in ('addons', 'add_log', 'serverdisconnect'):
        raise AttributeError(f'missing .{hook_name}')
      try:
        hook_actions = self.hook_map[hook_name]
      except KeyError as e:
        raise AttributeError(f'unknown hook name {hook_name=}') from e

      print(prefix, '...')

      def call_hooks(*a, **kw):
        if not hook_actions:
          print(f'{prefix}(*{a!r}, **{kw!r}')
          return
        last_e = None
        for i, hook_action in enumerate(hook_actions):
          try:
            pfx_call(hook_action, *a, **kw)
          except Exception as e:
            warning("%s: exception calling hook_action[%d]: %s", prefix, i, e)
            last_e = e
        if last_e is None:
          raise last_e

      return call_hooks

  def request(self, flow: http.HTTPFlow):
    """This method is called for every HTTP request."""
    print(f"Intercepted request to: {flow.request.url}")

@typechecked
@uses_runstate
async def run_proxy(
    listen_host=DEFAULT_LISTEN_HOST,
    listen_port=DEFAULT_LISTEN_PORT,
    *,
    addon: MITMAddon,
    runstate: RunState,
):
  opts = Options(listen_host=listen_host, listen_port=listen_port)
  proxy = DumpMaster(opts)
  proxy.addons.add(addon)
  vprint("Starting mitmproxy listening on {listen_host}:{listen_port}.")
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
