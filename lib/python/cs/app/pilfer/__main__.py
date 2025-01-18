#!/usr/bin/env python3

import asyncio
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import cached_property
import os
import os.path
from os.path import expanduser
from getopt import GetoptError
import sys
from typing import Iterable
try:
  import xml.etree.cElementTree as ElementTree
except ImportError:
  pass

from typeguard import typechecked

from cs.cmdutils import BaseCommand, popopts
from cs.context import stackattrs
from cs.later import Later
from cs.lex import (cutprefix, cutsuffix, is_identifier, tabulate)
import cs.logutils
from cs.logutils import (debug, error)
import cs.pfx
from cs.pfx import Pfx, pfx_call
from cs.resources import uses_runstate

from . import DEFAULT_JOBS, DEFAULT_FLAGS_CONJUNCTION
from .pilfer import Pilfer
from .pipelines import PipeLineSpec

def main(argv=None):
  ''' Pilfer command line function.
  '''
  return PilferCommand(argv).run()

@typechecked
def urls(url, stdin=None, cmd=None) -> Iterable[str]:
  ''' Generator to yield input URLs.
  '''
  if stdin is None:
    stdin = sys.stdin
  if cmd is None:
    cmd = cs.pfx.cmd
  if url != '-':
    # literal URL supplied, deliver to pipeline
    yield url
  else:
    # read URLs from stdin
    try:
      do_prompt = stdin.isatty()
    except AttributeError:
      do_prompt = False
    if do_prompt:
      # interactively prompt for URLs, deliver to pipeline
      prompt = cmd + ".url> "
      while True:
        try:
          url = input(prompt)
        except EOFError:
          break
        else:
          yield url
    else:
      # read URLs from non-interactive stdin, deliver to pipeline
      lineno = 0
      for line in stdin:
        lineno += 1
        with Pfx("stdin:%d", lineno):
          if not line.endswith('\n'):
            raise ValueError("unexpected EOF - missing newline")
          url = line.strip()
          if not line or line.startswith('#'):
            debug("SKIP: %s", url)
            continue
          yield url

class PilferCommand(BaseCommand):

  @dataclass
  class Options(BaseCommand.Options):
    configpath: str = field(
        default_factory=lambda: os.environ.get('PILFERRC') or
        expanduser('~/.pilferrc')
    )
    jobs: int = DEFAULT_JOBS
    flagnames: str = tuple(DEFAULT_FLAGS_CONJUNCTION.replace(',', ' ').split())

    @property
    def configpaths(self):
      ''' A list of the config filesystem paths.
      '''
      return [fspath for fspath in self.configpath.split(':') if fspath]

    COMMON_OPT_SPECS = dict(
        **BaseCommand.Options.COMMON_OPT_SPECS,
        c_=('configpath', 'Colon separated list of config paths.'),
        j_=(
            'jobs',
            ''' How many jobs (actions: URL fetches, minor computations)
                to run at a time. Default: {DEFAULT_JOBS}
            ''',
            int,
        ),
        F_=(
            'flagnames',
            'Flags which must be true for operation to continue.',
            lambda s: s.replace(',', ' ').split(),
        ),
        u='unbuffered',
        x=('trace', 'Trace action execution.'),
    )

  @contextmanager
  @uses_runstate
  def run_context(self, *, runstate):
    ''' Apply the `options.runstate` to the main `Pilfer`.
    '''
    options = self.options
    badopts = False
    # sanity check the flagnames
    for raw_flagname in options.flagnames:
      with Pfx(raw_flagname):
        flagname = cutprefix(raw_flagname, '!')
        if not is_identifier(flagname):
          error('invalid flag specifier')
          badopts = True
    if badopts:
      raise GetoptError(f'invalid flags: {options.flagnames!r}')
    with super().run_context():
      later = Later(self.options.jobs)
      with later:
        pilfer = Pilfer(later=later, rcpaths=options.configpaths)
        with pilfer:
          with stackattrs(
              self.options,
              later=later,
              pilfer=pilfer,
          ):
            yield

  @staticmethod
  def get_argv_pipespec(argv, argv_offset=0):
    ''' Parse a pipeline specification from the argument list `argv`.
        Return `(PipeLineSpec,new_argv_offset)`.

        A pipeline specification is specified by a leading argument of the
        form `'pipe_name:{'`, followed by arguments defining functions for the
        pipeline, and a terminating argument of the form `'}'`.

        Note: this syntax works well with traditional Bourne shells.
        Zsh users can use 'setopt IGNORE_CLOSE_BRACES' to get
        sensible behaviour. Bash users may be out of luck.
    '''
    start_arg = argv[argv_offset]
    pipe_name = cutsuffix(start_arg, ':{')
    if pipe_name is start_arg or not pipe_name:
      raise ValueError('expected "pipe_name:{", got: %r' % (start_arg,))
    with Pfx(start_arg):
      argv_offset += 1
      spec_offset = argv_offset
      while argv[argv_offset] != '}':
        argv_offset += 1
      spec = PipeLineSpec(pipe_name, argv[spec_offset:argv_offset])
      argv_offset += 1
      return spec, argv_offset

  @popopts(md=('show_md', 'Show metadata.'))
  def cmd_cache(self, argv):
    ''' Usage: {cmd} [cache-keys...]
          List the URLs associated with the cache keys.
    '''
    options = self.options
    show_md = options.show_md
    cache = options.pilfer.content_cache
    with cache:
      cache_keys = argv or sorted(cache.keys())
      if show_md:
        for cache_key in cache_keys:
          print(cache_key)
          md = cache.get(cache_key, {})
          for line in tabulate(*((f'  {mdk}', pformat(mdv))
                                 for mdk, mdv in sorted(md.items()))):
            print(line)
      else:
        for line in tabulate(*((cache_key, cache.get(cache_key, {}).get('url'))
                               for cache_key in cache_keys)):
          print(line)

  @popopts
  def cmd_from(self, argv):
    ''' Usage: {cmd} source [pipeline-defns..]
          Source may be a URL or "-" to read URLs from standard input.
    '''
    options = self.options
    P = options.pilfer
    if not argv:
      raise GetoptError("missing URL")
    url = argv.pop(0)
    # Load any named pipeline definitions on the command line.
    argv_offset = 0
    while argv and argv[argv_offset].endswith(':{'):
      spec, argv_offset = self.get_argv_pipespec(argv, argv_offset)
      P.pipe_specs[spec.name] = spec
    # prepare the main pipeline specification from the remaining argv
    if not argv:
      raise GetoptError("missing main pipeline")
    pipespec = PipeLineSpec(name="CLI", stage_specs=argv)
    # prepare an input containing URLs
    if url == '-':
      urls = (line.rstrip('\n') for line in sys.stdin)
    else:
      urls = [url]

    # sanity check the pipeline spec
    try:
      pipeline = pipespec.make_stage_funcs(P=P)
    except ValueError as e:
      raise GetoptError(
          f'invalid pipeline spec {pipespec.stage_specs}: {e}'
      ) from e

    async def print_from(item_Ps):
      ''' Consume `(result,Pilfer)` 2-tuples from the pipeline and print the results.
      '''
      async for result, _ in item_Ps:
        print(result)

    async def run():
      # consume everything from the main pipeline
      await print_from(pipespec.run_pipeline(urls))
      # close and join any running diversions
      await P.diversions.join()

    asyncio.run(run())

  @popopts
  def cmd_mitm(self, argv):
    ''' Usage: {cmd} [@[address]:port] hook:action...
          Run a mitmproxy for traffic filtering.
    '''
    from .mitm import (
        DEFAULT_LISTEN_HOST, DEFAULT_LISTEN_PORT, MITMAddon, run_proxy
    )
    listen_host = DEFAULT_LISTEN_HOST
    listen_port = DEFAULT_LISTEN_PORT
    if argv and argv[0].startswith('@'):
      ip_port = argv.pop(0)[1:]
      with Pfx("@[address]:port %r", ip_port):
        try:
          host, port = ip_port.rsplit(':', 1)
          if host:
            listen_host = host
          listen_port = int(port)
        except ValueError as e:
          raise GetoptError(f'invalid [address]:port: {e}') from e
    if not argv:
      raise GetoptError('missing hooks')
    mitm_addon = MITMAddon()
    for hook in argv:
      with Pfx("hook %r", hook):
        try:
          hook_names, action = hook.split(':', 1)
        except ValueError:
          raise GetoptError("missing colon")
        for hook_name in hook_names.split(','):
          pfx_call(mitm_addon.add_hook, hook_name, action)
    asyncio.run(run_proxy(listen_host, listen_port, addon=mitm_addon))

  def cmd_sitemaps(self, argv):
    ''' Usage: {cmd}
          List the site maps from the config.
    '''
    for line in tabulate(
        *[[pattern, str(sitemap)]
          for pattern, sitemap in self.options.pilfer.sitemaps]):
      print(line)

sys.exit(main(sys.argv))
