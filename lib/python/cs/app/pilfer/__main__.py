#!/usr/bin/env python3

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
import cs.pfx
from cs.pfx import Pfx
from cs.pipeline import pipeline, StageType
from cs.queues import NullQueue
from cs.resources import uses_runstate

from . import DEFAULT_JOBS, DEFAULT_FLAGS_CONJUNCTION, Pilfer
from .actions import Action
from .pipelines import PipeSpec
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
    flagnames: str = DEFAULT_FLAGS_CONJUNCTION

    @cached_property
    @trace(retval=True)
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
        pilfer.rcs.extend(options.configpaths)
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

  def cmd_url(self, argv):
    ''' Usage: {cmd} URL [pipeline-defns..]
          URL may be "-" to read URLs from standard input.
    '''
    options = self.options
    later = options.later
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

    # now load the main pipeline
    if not argv:
      raise GetoptError("missing main pipeline")

    # TODO: main_spec never used?
    main_spec = PipeSpec(None, argv)

    # gather up the remaining definition as the running pipeline
    pipe_funcs, errors = argv_pipefuncs(argv, P.action_map, P.do_trace)

    # report accumulated errors and set badopts
    if errors:
      for err in errors:
        error(err)
      raise GetoptError("invalid main pipeline")

    P.flagnames = options.flagnames.split()
    if cs.logutils.D_mode or ifdebug():
      # poll the status of the Later regularly
      def pinger(later):
        while True:
          D(
              "PINGER: later: quiescing=%s, state=%r: %s", later._quiescing,
              later._state, later
          )
          sleep(2)

      ping = Thread(target=pinger, args=(later,))
      ping.daemon = True
      ping.start()
    P.later = later
    # construct the pipeline
    pipe = pipeline(
        pipe_funcs,
        name="MAIN",
        outQ=NullQueue(name="MAIN_PIPELINE_END_NQ", blocking=True).open(),
    )
    with pipe:
      for U in urls(url, stdin=sys.stdin, cmd=self.cmd):
        pipe.put(P.copy_with_vars(_=U))
    # wait for main pipeline to drain
    later.state("drain main pipeline")
    for item in pipe.outQ:
      warning("main pipeline output: escaped: %r", item)
    # At this point everything has been dispatched from the input queue
    # and the only remaining activity is in actions in the diversions.
    # As long as there are such actions, the Later will be busy.
    # In fact, even after the Later first quiesces there may
    # be stalled diversions waiting for EOF in order to process
    # their "func_final" actions. Releasing these may pass
    # tasks to other diversions.
    # Therefore we iterate:
    #  - wait for the Later to quiesce
    #  - TODO: topologically sort the diversions
    #  - pick the [most-ancestor-like] diversion that is busy
    #    or exit loop if they are all idle
    #  - close the div
    #  - wait for that div to drain
    #  - repeat
    # drain all the diversions, choosing the busy ones first
    divnames = P.open_diversion_names
    while divnames:
      busy_name = None
      for divname in divnames:
        div = P.diversion(divname)
        if div._busy:
          busy_name = divname
          break
      # nothing busy? pick the first one arbitrarily
      if not busy_name:
        busy_name = divnames[0]
      busy_div = P.diversion(busy_name)
      later.state("CLOSE DIV %s", busy_div)
      busy_div.close(enforce_final_close=True)
      outQ = busy_div.outQ
      later.state("DRAIN DIV %s: outQ=%s", busy_div, outQ)
      for item in outQ:
        # diversions are supposed to discard their outputs
        error("%s: RECEIVED %r", busy_div, item)
      later.state("DRAINED DIV %s using outQ=%s", busy_div, outQ)
      divnames = P.open_diversion_names
    later.state("quiescing")
    later.wait_outstanding(until_idle=True)
    # Now the diversions should have completed and closed.
    # out of the context manager, the Later should be shut down
    later.state("WAIT...")
    later.wait()
    later.state("WAITED")

  @staticmethod
  def get_argv_pipespec(argv, argv_offset=0):
    ''' Parse a pipeline specification from the argument list `argv`.
        Return `(PipeSpec,new_argv_offset)`.

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
      spec = PipeSpec(pipe_name, argv[spec_offset:argv_offset])
      argv_offset += 1
      return spec, argv_offset

sys.exit(main(sys.argv))
