#!/usr/bin/env python3

import asyncio
from contextlib import contextmanager
from dataclasses import dataclass
from functools import cached_property
import os
import os.path
from os.path import expanduser
from getopt import GetoptError
import sys
from threading import Thread
from time import sleep
from typing import Iterable
try:
  import xml.etree.cElementTree as ElementTree
except ImportError:
  pass

from typeguard import typechecked

from cs.cmdutils import BaseCommand
from cs.context import stackattrs
from cs.debug import ifdebug
from cs.env import envsub
from cs.later import Later, uses_later
from cs.lex import (cutprefix, cutsuffix, get_identifier, is_identifier)
import cs.logutils
from cs.logutils import (debug, error, warning, D)
from cs.naysync import async_iter
import cs.pfx
from cs.pfx import Pfx
from cs.pipeline import pipeline, StageType
from cs.queues import NullQueue
from cs.resources import uses_runstate

from . import DEFAULT_JOBS, DEFAULT_FLAGS_CONJUNCTION, Pilfer
from .actions import Action
from .pipelines import PipeLineSpec
from .rc import load_pilferrcs, PilferRC

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

  GETOPT_SPEC = 'c:F:j:qux'

  USAGE_FORMAT = '''Usage: {cmd} [options...] op [args...]
    Options:
      -c config
          Load rc file.
      -F flag-conjunction
          Space separated list of flag or !flag to satisfy as a conjunction.
      -j jobs
      How many jobs (actions: URL fetches, minor computations)
      to run at a time.
          Default: ''' + str(
      DEFAULT_JOBS
  ) + '''
      -q  Quiet. Don't recite surviving URLs at the end.
      -u  Unbuffered. Flush print actions as they occur.
      -x  Trace execution.'''

  @dataclass
  class Options(BaseCommand.Options):
    configpath: str = ''
    jobs: int = DEFAULT_JOBS
    flagnames: str = tuple(DEFAULT_FLAGS_CONJUNCTION.replace(',', ' ').split())

    @cached_property
    def configpaths(self):
      ''' A list of the config filesystem paths.
      '''
      configpath = self.configpath
      if not configpath:
        configpath = os.environ.get('PILFERRC') or expanduser('~/.pilferrc')
      return [fspath for fspath in configpath.split(':') if fspath]

    COMMON_OPT_SPECS = dict(
        **BaseCommand.Options.COMMON_OPT_SPECS,
        c_=('configpath', 'Colon separated list of config paths.'),
        j_=('jobs', int),
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
    # sanity check the flagnames
    for raw_flagname in options.flagnames:
      with Pfx(raw_flagname):
        flagname = cutprefix(raw_flagname, '!')
        if not is_identifier(flagname):
          error('invalid flag specifier')
          badopts = True
    with super().run_context():
      later = Later(self.options.jobs)
      with later:
        pilfer = Pilfer(later=later)
        pilfer.rcs.extend(map(PilferRC, options.configpaths))
        with pilfer:
          with stackattrs(
              self.options,
              later=later,
              pilfer=pilfer,
          ):
            yield

  @staticmethod
  def hack_postopts_argv(argv, options):
    ''' Infer "url" subcommand if the first argument looks like an URL.
    '''
    if argv:
      op = argv[0]
      if op.startswith('http://') or op.startswith('https://'):
        # push the URL back and infer missing "url" op word
        argv.insert(0, 'url')
    return argv

  def cmd_from(self, argv):
    ''' Usage: {cmd} source [pipeline-defns..]
          Source may be a URL or "-" to read URLs from standard input.
    '''
    options = self.options
    P = options.pilfer
    if not argv:
      raise GetoptError("missing URL")
    url = argv.pop(0)
    # prepare a blank PilferRC and supply as first in chain for this Pilfer
    rc = PilferRC(None)
    P.rcs.insert(0, rc)
    # Load any named pipeline definitions on the command line.
    argv_offset = 0
    while argv and argv[argv_offset].endswith(':{'):
      spec, argv_offset = self.get_argv_pipespec(argv, argv_offset)
      try:
        rc.add_pipespec(spec)
      except KeyError as e:
        raise GetoptError("add pipe: %s", e)
    # prepare the main pipeline specification from the remaining argv
    if not argv:
      raise GetoptError("missing main pipeline")
    pipespec = PipeLineSpec(name="CLI", stage_specs=argv)
    # prepare an input containing URLs
    if url == '-':
      urls = (line.rstrip('\n') for line in sys.stdin)
    else:
      urls = [url]

    async def print_from(item_Ps):
      ''' Consume `(result,Pilfer)` 2-tuples from the pipeline and print the results.
      '''
      async for result, _ in item_Ps:
        print(result)

    asyncio.run(print_from(pipespec.run_pipeline(urls)))

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
    if pipe_name is start_arg:
      raise ValueError('expected "pipe_name:{", got: %r' % (start_arg,))
    with Pfx(start_arg):
      argv_offset += 1
      spec_offset = argv_offset
      while argv[argv_offset] != '}':
        argv_offset += 1
      spec = PipeLineSpec(pipe_name, argv[spec_offset:argv_offset])
      argv_offset += 1
      return spec, argv_offset

sys.exit(main(sys.argv))
