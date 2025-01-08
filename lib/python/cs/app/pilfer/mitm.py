#!/usr/bin/env python3
#
# Hook to run a mitmproxy monitor.
#

import asyncio
from signal import SIGINT

from mitmproxy import http
from mitmproxy.options import Options
##from mitmproxy.proxy.config import ProxyConfig
##from mitmproxy.proxy.server import ProxyServer
from mitmproxy.tools.dump import DumpMaster

from cs.cmdutils import vprint
from cs.resources import RunState, uses_runstate

from cs.debug import trace, X, r, s

DEFAULT_LISTEN_HOST = '127.0.0.1'
DEFAULT_LISTEN_PORT = 3131

class InterceptAddon:

  def request(self, flow: http.HTTPFlow):
    """This method is called for every HTTP request."""

    print(f"Intercepted request to: {flow.request.url}")

  def response(self, flow: http.HTTPFlow):
    """This method is called for every HTTP response."""

    print(f"Intercepted response from: {flow.request.url}")

@uses_runstate
async def run_proxy(
    listen_host=DEFAULT_LISTEN_HOST,
    listen_port=DEFAULT_LISTEN_PORT,
    *,
    runstate: RunState,
):
  opts = Options(listen_host=listen_host, listen_port=listen_port)
  proxy = DumpMaster(opts)
  proxy.addons.add(InterceptAddon())
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
