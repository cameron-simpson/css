#!/usr/bin/env python3
#
# Web page utility.
#       - Cameron Simpson <cs@cskk.id.au> 07jul2010
#

from __future__ import with_statement, print_function
from collections import defaultdict
from configutils import ConfigParser
import os
import os.path
import errno
from getopt import getopt, GetoptError
import re
import shlex
from string import Formatter, whitespace
from subprocess import Popen, PIPE
import sys
from threading import Lock, RLock, Thread
from time import sleep
from types import SimpleNamespace as NS
from urllib.parse import quote, unquote
from urllib.error import HTTPError, URLError
from urllib.request import build_opener, HTTPBasicAuthHandler, HTTPCookieProcessor
try:
  import xml.etree.cElementTree as ElementTree
except ImportError:
  import xml.etree.ElementTree as ElementTree
from icontract import require
from cs.app.flag import PolledFlags
from cs.debug import thread_dump, ifdebug
from cs.env import envsub
from cs.excutils import logexc, LogExceptions
from cs.fileutils import mkdirn
from cs.later import Later, RetryError
from cs.lex import (
    get_dotted_identifier, get_identifier, is_identifier, get_other_chars,
    get_qstr
)
import cs.logutils
from cs.logutils import (
    setup_logging, logTo, debug, error, warning, exception, trace, D
)
from cs.mappings import MappingChain, SeenSet
from cs.obj import copy as obj_copy
import cs.pfx
from cs.pfx import Pfx
from cs.pipeline import (
    pipeline, FUNC_ONE_TO_ONE, FUNC_ONE_TO_MANY, FUNC_SELECTOR,
    FUNC_MANY_TO_MANY, FUNC_PIPELINE
)
from cs.py.func import (funcname, yields_type, returns_type, yields_str)
from cs.py.modules import import_module_name
from cs.queues import NullQueue
from cs.resources import MultiOpenMixin
from cs.seq import seq
from cs.threads import locked
from cs.urlutils import URL, isURL, NetrcHTTPPasswordMgr
from cs.x import X

# parallelism of jobs
DEFAULT_JOBS = 4

# default flag status probe
DEFAULT_FLAGS_CONJUNCTION = '!PILFER_DISABLE'

usage = '''Usage: %s [options...] op [args...]
  %s url URL actions...
      URL may be "-" to read URLs from standard input.
  Options:
    -c config
        Load rc file.
    -F flag-conjunction
        Space separated list of flag or !flag to satisfy as a conjunction.
    -j jobs
	How many jobs (actions: URL fetches, minor computations)
	to run at a time.
        Default: %d
    -q  Quiet. Don't recite surviving URLs at the end.
    -u  Unbuffered. Flush print actions as they occur.
    -x  Trace execution.'''

def main(argv, stdin=None):
  if stdin is None:
    stdin = sys.stdin
  argv = list(argv)
  xit = 0
  argv0 = argv.pop(0)
  cmd = os.path.basename(argv0)
  setup_logging(cmd)
  logTo('.pilfer.log')

  P = Pilfer()
  quiet = False
  jobs = DEFAULT_JOBS
  flagnames = DEFAULT_FLAGS_CONJUNCTION

  badopts = False

  try:
    opts, argv = getopt(argv, 'c:F:j:qux')
  except GetoptError as e:
    warning("%s", e)
    badopts = True
    opts = ()

  for opt, val in opts:
    with Pfx("%s", opt):
      if opt == '-c':
        P.rcs[0:0] = load_pilferrcs(val)
      elif opt == '-F':
        flagnames = val
      elif opt == '-j':
        jobs = int(val)
      elif opt == '-q':
        quiet = True
      elif opt == '-u':
        P.flush_print = True
      elif opt == '-x':
        P.do_trace = True
      else:
        raise NotImplementedError("unimplemented option")

  # break the flags into separate words and syntax check
  flagnames = flagnames.split()
  for flagname in flagnames:
    if flagname.startswith('!'):
      flag_ok = is_identifier(flagname, 1)
    else:
      flag_ok = is_identifier(flagname)
    if not flag_ok:
      error('invalid flag specifier: %r', flagname)
      badopts = True

  dflt_rc = os.environ.get('PILFERRC')
  if dflt_rc is None:
    dflt_rc = envsub('$HOME/.pilferrc')
  if dflt_rc:
    with Pfx("$PILFERRC: %s", dflt_rc):
      P.rcs.extend(load_pilferrcs(dflt_rc))

  if not argv:
    error("missing op")
    badopts = True
  else:
    op = argv.pop(0)
    if op.startswith('http://') or op.startswith('https://'):
      # push the URL back and infer missing "url" op word
      argv.insert(0, op)
      op = 'url'
    with Pfx(op):
      if op == 'url':
        if not argv:
          error("missing URL")
          badopts = True
        else:
          url = argv.pop(0)

          # prepare a blank PilferRC and supply as first in chain for this Pilfer
          rc = PilferRC(None)
          P.rcs.insert(0, rc)

          # Load any named pipeline definitions on the command line.
          #
          # A pipeline specification is specified by a leading argument
          # of the form "pipe_name:{", followed by arguments defining
          # functions for the pipeline, and a terminating argument of the
          # form "}".
          #
          # Return `(spec, argv2, errors)` where `spec` is a PipeSpec
          # embodying the specification, `argv2` is the list of arguments
          # after the specification and `errors` is a list of error
          # messages encountered parsing the function arguments.
          #
          # If the leading argument does not commence a function specification
          # then `spec` will be None and `argv2` will be `argv`.
          #
          # Note: this syntax works well with traditional Bourne shells.
          # Zsh users can use 'setopt IGNORE_CLOSE_BRACES' to get
          # sensible behaviour. Bash users may be out of luck.
          #
          while len(argv) and argv[0].endswith(':{'):
            openarg = argv.pop(0)
            with Pfx(openarg):
              pipe_name = openarg[:-2]
              argv2 = []
              end_pos = None
              for pos, arg in enumerate(argv):
                if arg == '}':
                  end_pos = pos
                  break
              if end_pos is None:
                error("no closing '}'")
                badopts = True
                argv = []
              else:
                spec = PipeSpec(pipe_name, argv[:end_pos])
                try:
                  rc.add_pipespec(spec)
                except KeyError as e:
                  error("add pipe: %s", e)
                  badopts = True
                argv = argv[end_pos + 1:]

          # now load the main pipeline
          if not argv:
            error("missing main pipeline")
            badopts = True
          else:
            main_spec = PipeSpec(None, argv)

          # gather up the remaining definition as the running pipeline
          pipe_funcs, errors = argv_pipefuncs(argv, P.action_map, P.do_trace)

          # report accumulated errors and set badopts
          if errors:
            for err in errors:
              error(err)
            badopts = True
          if not badopts:
            LTR = Later(jobs)
            P.flagnames = flagnames
            if cs.logutils.D_mode or ifdebug():
              # poll the status of the Later regularly
              def pinger(L):
                while True:
                  D(
                      "PINGER: L: quiescing=%s, state=%r: %s", L._quiescing,
                      L._state, L
                  )
                  sleep(2)

              ping = Thread(target=pinger, args=(LTR,))
              ping.daemon = True
              ping.start()
            with LTR as L:
              P.later = L
              # construct the pipeline
              pipeline = pipeline(
                  L,
                  pipe_funcs,
                  name="MAIN",
                  outQ=NullQueue(name="MAIN_PIPELINE_END_NQ",
                                 blocking=True).open(),
              )
              X("MAIN: RUN PIPELINE...")
              with pipeline:
                for U in urls(url, stdin=stdin, cmd=cmd):
                  X("MAIN: PUT %r", U)
                  pipeline.put(P.copy_with_vars(_=U))
              X("MAIN: RUN PIPELINE: ALL ITEMS .put")
              # wait for main pipeline to drain
              LTR.state("drain main pipeline")
              for item in pipeline.outQ:
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
              #  - [missing] topologically sort the diversions
              #  - pick the [most-ancestor-like] diversion that is busy
              #    or exit loop if they are all idle
              #  - close the div
              #  - wait for that div to drain
              #  - repeat
              # drain all the divserions, choosing the busy ones first
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
                LTR.state("CLOSE DIV %s", busy_div)
                busy_div.close(enforce_final_close=True)
                outQ = busy_div.outQ
                D("DRAIN DIV %s", busy_div)
                LTR.state("DRAIN DIV %s: outQ=%s", busy_div, outQ)
                for item in outQ:
                  # diversions are supposed to discard their outputs
                  error("%s: RECEIVED %r", busy_div, item)
                LTR.state("DRAINED DIV %s using outQ=%s", busy_div, outQ)
                divnames = P.open_diversion_names
              LTR.state("quiescing")
              L.wait_outstanding(until_idle=True)
              # Now the diversions should have completed and closed.
            # out of the context manager, the Later should be shut down
            LTR.state("WAIT...")
            L.wait()
            LTR.state("WAITED")
      else:
        error("unsupported op")
        badopts = True

  if badopts:
    print(usage % (cmd, cmd, DEFAULT_JOBS), file=sys.stderr)
    xit = 2

  return xit

@yields_str
def urls(url, stdin=None, cmd=None):
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

# TODO: recursion protection in action_map expansion
def argv_pipefuncs(argv, action_map, do_trace):
  ''' Process command line strings and return a corresponding list
      of actions to construct a Later.pipeline.
  '''
  # we reverse the list to make action expansion easier
  argv = list(argv)
  errors = []
  pipe_funcs = []
  while argv:
    action = argv.pop(0)
    # support commenting-out of individual actions
    if action.startswith('#'):
      continue
    # macro - prepend new actions
    func_name, offset = get_identifier(action)
    if func_name and func_name in action_map:
      expando = action_map[func_name]
      argv[:0] = expando
      continue
    if action == "per":
      # fork a new pipeline instance per item
      # terminate this pipeline with a function to spawn subpipelines
      # using the tail of the action list from this point
      if not argv:
        errors.append("no actions after %r" % (per,))
      else:
        tail_argv = list(argv)
        name = "per:[%s]" % (','.join(argv))
        pipespec = PipeSpec(name, argv)

        def per(P):
          pipeline = pipeline(
              P.later,
              pipespec.actions,
              inputs=(P,),
              name="%s(%s)" % (name, P)
          )
          with P.later.release():
            for P2 in pipeline.outQ:
              yield P2

        pipe_funcs.append((FUNC_ONE_TO_MANY, per))
      argv = []
      continue
    try:
      A = Action(action, do_trace)
    except ValueError as e:
      errors.append("bad action %r: %s" % (action, e))
    else:
      pipe_funcs.append(A)
  return pipe_funcs, errors

def notNone(v, name="value"):
  if v is None:
    raise ValueError("%s is None" % (name,))
  return True

def url_xml_find(U, match):
  for found in url_io(URL(U, None).xml_find_all, (), match):
    yield ElementTree.tostring(found, encoding='utf-8')

class Pilfer(NS):
  ''' State for the pilfer app.
      Notable attribute include:
        .flush_print    Flush output after print(), default False.
        .user_agent     Specify user-agent string, default None.
        .user_vars      Mapping of user variables for arbitrary use.
  '''

  def __init__(self, *a, **kw):
    self._name = 'Pilfer-%d' % (seq(),)
    self._lock = Lock()
    self.user_vars = {'save_dir': '.'}
    self._ = None
    self.flush_print = False
    self.do_trace = False
    self.flags = PolledFlags()
    self._print_to = None
    self._print_lock = Lock()
    self.user_agent = None
    self._lock = RLock()
    self.rcs = []  # chain of PilferRC libraries
    self.seensets = {}
    self.diversions_map = {}  # global mapping of names to divert: pipelines
    self.opener = build_opener()
    self.opener.add_handler(HTTPBasicAuthHandler(NetrcHTTPPasswordMgr()))
    self.opener.add_handler(HTTPCookieProcessor())
    super().__init__(**kw)

  def __str__(self):
    return "%s[%s]" % (self._name, self._)

  __repr__ = __str__

  def copy(self, *a, **kw):
    ''' Convenience function to shallow copy this Pilfer with modifications.
    '''
    return obj_copy(self, *a, **kw)

  @property
  def defaults(self):
    ''' Mapping for default values formed by cascading PilferRCs.
    '''
    return MappingChain(mappings=[rc.defaults for rc in self.rcs])

  @property
  def _(self):
    ''' Shortcut to this Pilfer's user_vars['_'] entry - the current item value.
    '''
    return self.user_vars['_']

  @_.setter
  def _(self, value):
    if value is not None and not isinstance(value, str):
      raise TypeError("Pilfer._: expected string, received: %r" % (value,))
    self.user_vars['_'] = value

  @property
  def url(self):
    ''' self._ as a URL object.
    '''
    return URL(self._, None)

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

  @locked
  def seenset(self, name):
    ''' Return the SeenSet implementing the named "seen" set.
    '''
    seen = self.seensets
    if name not in seen:
      backing_path = MappingChain(
          mappings=[rc.seen_backing_paths for rc in self.rcs]
      ).get(name)
      if backing_path is not None:
        backing_path = envsub(backing_path)
        if (not os.path.isabs(backing_path)
            and not backing_path.startswith('./')
            and not backing_path.startswith('../')):
          backing_basedir = self.defaults.get('seen_dir')
          if backing_basedir is not None:
            backing_basedir = envsub(backing_basedir)
            backing_path = os.path.join(backing_basedir, backing_path)
      seen[name] = SeenSet(name, backing_path)
    return seen[name]

  def seen(self, url, seenset='_'):
    ''' Test if the named `url` has been seen. Default seetset is '_'.
    '''
    return url in self.seenset(seenset)

  def see(self, url, seenset='_'):
    ''' Mark a `url` as seen. Default seetset is '_'.
    '''
    self.seenset(seenset).add(url)

  @property
  @locked
  def diversions(self):
    ''' The current list of named diversions.
    '''
    return list(self.diversions_map.values())

  @property
  @locked
  def diversion_names(self):
    ''' The current list of diversion names.
    '''
    return list(self.diversions_map.keys())

  @property
  @locked
  @logexc
  def open_diversion_names(self):
    ''' The current list of open named diversions.
    '''
    names = []
    for divname in self.diversion_names:
      div = self.diversion(divname)
      if not div.closed:
        names.append(divname)
    return names

  @logexc
  def quiesce_diversions(self):
    D("%s.quiesce_diversions...", self)
    while True:
      D("%s.quiesce_diversions: LOOP: pass over diversions...", self)
      for div in self.diversions:
        D("%s.quiesce_diversions: check %s ...", self, div)
        div.counter.check()
        D("%s.quiesce_diversions: quiesce %s ...", self, div)
        div.quiesce()
      D("%s.quiesce_diversions: now check that they are all quiet...", self)
      quiet = True
      for div in self.diversions:
        if div.counter:
          D("%s.quiesce_diversions: NOT QUIET: %s", self, div)
          quiet = False
          break
      if quiet:
        D("%s.quiesce_diversions: all quiet!", self)
        return

  @locked
  def diversion(self, pipe_name):
    ''' Return the diversion named `pipe_name`.
        A diversion embodies a pipeline of the specified name.
        There is only one of a given name in the shared state.
        They are instantiated at need.
    '''
    diversions = self.diversions_map
    if pipe_name not in diversions:
      spec = self.pipes.get(pipe_name)
      if spec is None:
        raise KeyError(
            "no diversion named %r and no pipe specification found" %
            (pipe_name,)
        )
      pipe_funcs, errors = spec.pipe_funcs(self.action_map, self.do_trace)
      if errors:
        for err in errors:
          error(err)
        raise KeyError(
            "invalid pipe specification for diversion named %r" % (pipe_name,)
        )
      name = "DIVERSION:%s" % (pipe_name,)
      outQ = NullQueue(name=name, blocking=True)
      outQ.open()  # open outQ so it can be closed at the end of the pipeline
      div = pipeline(self.later, pipe_funcs, name=name, outQ=outQ)
      div.open()  # will be closed in main program shutdown
      diversions[pipe_name] = div
    return diversions[pipe_name]

  @logexc
  def pipe_through(self, pipe_name, inputs):
    ''' Create a new pipeline from the specification named `pipe_name`.
        It will collect items from the iterable `inputs`.
        `pipe_name` may be a PipeSpec.
    '''
    with Pfx("pipe spec %r" % (pipe_name,)):
      name = "pipe_through:%s" % (pipe_name,)
      return self.pipe_from_spec(pipe_name, inputs, name=name)

  def pipe_from_spec(self, pipe_name, name=None):
    ''' Create a new pipeline from the specification named `pipe_name`.
    '''
    if isinstance(pipe_name, PipeSpec):
      spec = pipe_name
      pipe_name = str(spec)
    else:
      spec = self.pipes.get(pipe_name)
      if spec is None:
        raise ValueError("no pipe specification named %r" % (pipe_name,))
    if name is None:
      name = "pipe_from_spec:%s" % (spec,)
    with Pfx("%s", spec):
      pipe_funcs, errors = spec.pipe_funcs(self.action_map, self.do_trace)
      if errors:
        for err in errors:
          error(err)
        raise ValueError("invalid pipe specification")
    return pipeline(self.later, pipe_funcs, name=name, inputs=inputs)

  def _rc_pipespecs(self):
    for rc in self.rcs:
      yield rc.pipe_specs

  @property
  def pipes(self):
    return MappingChain(get_mappings=self._rc_pipespecs)

  def _rc_action_maps(self):
    for rc in self.rcs:
      yield rc.action_map

  @property
  def action_map(self):
    return MappingChain(get_mappings=self._rc_action_maps)

  def _print(self, *a, **kw):
    file = kw.pop('file', None)
    if kw:
      raise ValueError("unexpected kwargs %r" % (kw,))
    with self._print_lock:
      if file is None:
        file = self._print_to if self._print_to else sys.stdout
      print(*a, file=file)
      if self.flush_print:
        file.flush()

  @require(lambda kw: all(isinstance(v, str) for v in kw.values()))
  def set_user_vars(self, **kw):
    ''' Update self.user_vars from the keyword arguments.
    '''
    self.user_vars.update(kw)

  def copy_with_vars(self, **kw):
    ''' Make a copy of `self` with copied .user_vars, update the vars and return the copied Pilfer.
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

  def save_url(self, U, saveas=None, dir=None, overwrite=False, **kw):
    ''' Save the contents of the URL `U`.
    '''
    debug(
        "save_url(U=%r, saveas=%r, dir=%s, overwrite=%r, kw=%r)...", U, saveas,
        dir, overwrite, kw
    )
    with Pfx("save_url(%s)", U):
      U = URL(U, None)
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

def yields_Pilfer(func):
  ''' Decorator for generators which should yield Pilfers.
  '''
  return yields_type(func, Pilfer)

def returns_Pilfer(func):
  ''' Decorator for functions which should return Pilfers.
  '''
  return returns_type(func, Pilfer)

class FormatArgument(str):

  @property
  def as_int(self):
    return int(self)

class FormatMapping(object):
  ''' A mapping object to set or fetch user variables or URL attributes.
      Various URL attributes are known, and may not be assigned to.
      This mapping is used with str.format to fill in {value}s.
  '''

  def __init__(self, P, U=None, factory=None):
    ''' Initialise this FormatMapping from a Pilfer `P`.
	The optional parameter `U` (default from `P._`) is the
	object whose attributes are exposed for format strings,
	though P.user_vars preempt them.
	The optional parameter `factory` is used to promote the
	value `U` to a useful type; it calls URL(U, None) by default.
    '''
    self.pilfer = P
    if U is None:
      U = P._
    if factory is None:
      factory = lambda x: URL(x, None)
    self.url = factory(U)

  def _ok_attrkey(self, k):
    ''' Test for validity of `k` as a public non-callable attribute of self.url.
    '''
    if not k[0].isalpha():
      return False
    U = self.url
    try:
      attr = getattr(U, k)
    except AttributeError:
      return False
    return not callable(attr)

  def keys(self):
    ks = (
        set([k for k in dir(self.url) if self._ok_attrkey(k)]) +
        set(self.pilfer.user_vars.keys())
    )
    return ks

  def __getitem__(self, k):
    return FormatArgument(self._getitem(k))

  def _getitem(self, k):
    P = self.pilfer
    url = self.url
    with Pfx(url):
      if k in P.user_vars:
        return P.user_vars[k]
      if not self._ok_attrkey(k):
        raise KeyError(
            "unapproved attribute (missing or callable or not public): %r" %
            (k,)
        )
      try:
        attr = getattr(url, k)
      except AttributeError as e:
        raise KeyError("no such attribute: .%s: %s" % (k, e))
      return attr

  def get(self, k, default):
    try:
      return self[k]
    except KeyError:
      return default

  def __setitem__(self, k, value):
    P = self.pilfer
    url = self.url
    with Pfx(url):
      if self._ok_attrkey(k):
        raise KeyError("it is forbidden to assign to attribute .%s" % (k,))
      else:
        P.user_vars[k] = value

  def format(self, s):
    ''' Format the string `s` using this mapping.
    '''
    return Formatter().vformat(s, (), self)

def new_dir(dirpath):
  ''' Create the directory `dirpath` or `dirpath-n` if `dirpath` exists.
      Return the path of the directory created.
  '''
  try:
    os.makedirs(dirpath)
  except OSError as e:
    if e.errno != errno.EEXIST:
      exception("os.makedirs(%r): %s", dirpath, e)
      raise
    dirpath = mkdirn(dirpath, '-')
  return dirpath

def has_exts(U, suffixes, case_sensitive=False):
  ''' Test if the .path component of a URL ends in one of a list of suffixes.
      Note that the .path component does not include the query_string.
  '''
  ok = False
  path = U.path
  if not path.endswith('/'):
    base = os.path.basename(path)
    if not case_sensitive:
      base = base.lower()
      suffixes = [sfx.lower() for sfx in suffixes]
    for sfx in suffixes:
      if base.endswith('.' + sfx):
        ok = True
        break
  return ok

@yields_str
def with_exts(urls, suffixes, case_sensitive=False):
  for U in urls:
    ok = False
    path = U.path
    if not path.endswith('/'):
      base = os.path.basename(path)
      if not case_sensitive:
        base = base.lower()
        suffixes = [sfx.lower() for sfx in suffixes]
      for sfx in suffixes:
        if base.endswith('.' + sfx):
          ok = True
          break
    if ok:
      yield U
    else:
      debug("with_exts: discard %s", U)

def url_delay(U, delay, *a):
  sleep(float(delay))
  return U

def url_query(U, *a):
  U = URL(U, None)
  if not a:
    return U.query
  qsmap = dict(
      [
          (qsp.split('=', 1) if '=' in qsp else (qsp, ''))
          for qsp in U.query.split('&')
      ]
  )
  return ','.join([unquote(qsmap.get(qparam, '')) for qparam in a])

def url_io(func, onerror, *a, **kw):
  ''' Call `func` and return its result.
      If it raises URLError or HTTPError, report the error and return `onerror`.
  '''
  debug("url_io(%s, %s, %s, %s)...", func, onerror, a, kw)
  try:
    return func(*a, **kw)
  except (URLError, HTTPError) as e:
    warning("%s", e)
    return onerror

def url_io_iter(I):
  ''' Generator that calls `I.next()` until StopIteration, yielding
      its values.
      If the call raises URLError or HTTPError, report the error
      instead of aborting.
  '''
  while True:
    try:
      item = next(I)
    except StopIteration:
      break
    except (URLError, HTTPError) as e:
      warning("%s", e)
    else:
      yield item

@yields_str
def url_hrefs(U):
  ''' Yield the HREFs referenced by a URL.
      Conceals URLError, HTTPError.
  '''
  return url_io_iter(URL(U, None).hrefs(absolute=True))

@yields_str
def url_srcs(U):
  ''' Yield the SRCs referenced by a URL.
      Conceals URLError, HTTPError.
  '''
  return url_io_iter(URL(U, None).srcs(absolute=True))

# actions that work on the whole list of in-play URLs
# these return Pilfers
many_to_many = {
      'sort':         lambda Ps, key=lambda P: P._, reverse=False: \
                        sorted(Ps, key=key, reverse=reverse),
      'last':         lambda Ps: Ps[-1:],
    }

# actions that work on individual Pilfer instances, returning multiple strings
one_to_many = {
    'hrefs': lambda P: url_hrefs(P._),
    'srcs': lambda P: url_srcs(P._),
    'xml': lambda P, match: url_xml_find(P._, match),
    'xmltext': lambda P, match: ElementTree.XML(P._).findall(match),
}

# actions that work on individual Pilfer instances, returning single strings
one_to_one = {
    '..':
    lambda P: URL(P._, None).parent,
    'delay':
    lambda P, delay: (P._, sleep(float(delay)))[0],
    'domain':
    lambda P: URL(P._, None).domain,
    'hostname':
    lambda P: URL(P._, None).hostname,
    'print':
    lambda P, **kw: (P._, P.print_url_string(P._, **kw))[0],
    'query':
    lambda P, *a: url_query(P._, *a),
    'quote':
    lambda P: quote(P._),
    'unquote':
    lambda P: unquote(P._),
    'save':
    lambda P, *a, **kw: (P._, P.save_url(P._, *a, **kw))[0],
    'title':
    lambda P: P._.page_title,
    'type':
    lambda P: url_io(P._.content_type, ""),
    'xmlattr':
    lambda P, attr:
    [A for A in (ElementTree.XML(P._).get(attr),) if A is not None],
}

one_test = {
    'has_title':
    lambda P: P._.page_title is not None,
    'reject_re':
    lambda P, regexp: not regexp.search(P._),
    'same_domain':
    lambda P: notNone(P._.referer, "%r.referer" % (P._,)
                      ) and P._.domain == P._.referer.domain,
    'same_hostname':
    lambda P: notNone(P._.referer, "%r.referer" % (P._,)) and P._.hostname == P
    ._.referer.hostname,
    'same_scheme':
    lambda P: notNone(P._.referer, "%r.referer" % (P._,)
                      ) and P._.scheme == P._.referer.scheme,
    'select_re':
    lambda P, regexp: regexp.search(P._),
}

# regular expressions used when parsing actions
re_GROK = re.compile(r'([a-z]\w*(\.[a-z]\w*)*)\.([_a-z]\w*)', re.I)

def Action(action_text, do_trace):
  ''' Wrapper for parse_action: parse an action text and promote (sig, function) into an _Action.
  '''
  parsed = parse_action(action_text, do_trace)
  try:
    sig, function = parsed
  except TypeError:
    action = parsed
  else:
    action = ActionFunction(action_text, sig, lambda: function)
  return action

def pilferify11(func):
  ''' Decorator for 1-to-1 Pilfer=>nonPilfer functions to return a Pilfer.
  '''

  def pf(P, *a, **kw):
    return P.copy_with_vars(_=func(P, *a, **kw))

  return pf

def pilferify1m(func):
  ''' Decorator for 1-to-many Pilfer=>nonPilfers functions to yield Pilfers.
  '''

  def pf(P, *a, **kw):
    for value in func(P, *a, **kw):
      yield P.copy_with_vars(_=value)

  return pf

def pilferifymm(func):
  ''' Decorator for 1-to-many Pilfer=>nonPilfers functions to yield Pilfers.
  '''

  def pf(Ps, *a, **kw):
    if not isinstance(Ps, list):
      Ps = list(Ps)
    if Ps:
      P0 = Ps[0]
      for value in func(Ps, *a, **kw):
        yield P0.copy_with_vars(_=value)

  return pf

def pilferifysel(func):
  ''' Decorator for selector Pilfer=>bool functions to yield Pilfers.
  '''

  def pf(Ps, *a, **kw):
    for P in Ps:
      if func(P, *a, **kw):
        yield P

  return pf

def parse_action(action, do_trace):
  ''' Accept a string `action` and return an _Action subclass instance or a (sig, function) tuple.
      This is primarily used by action_func below, but also called
      by subparses such as selectors applied to the values of named
      variables.
      Selectors return booleans, all other functions return or yield Pilfers.
  '''
  # save original form of the action string
  action0 = action

  if action.startswith('!'):
    # ! shell command to generate items based off current item
    # receive text lines, stripped
    return ActionShellCommand(action0, action[1:])

  if action.startswith('|'):
    # | shell command to pipe though
    # receive text lines, stripped
    return ActionShellFilter(action0, action[1:])

  # select URLs matching regexp
  # /regexp/
  # named groups in the regexp get applied, per URL, to the variables
  if action.startswith('/'):
    if action.endswith('/'):
      regexp = action[1:-1]
    else:
      regexp = action[1:]
    regexp = re.compile(regexp)
    if regexp.groupindex:
      # a regexp with named groups
      def named_re_match(P):
        U = P._
        m = regexp.search(U)
        if m:
          varmap = m.groupdict()
          if varmap:
            P = P.copy_with_vars(**varmap)
          yield P

      return FUNC_ONE_TO_MANY, named_re_match
    else:
      return FUNC_SELECTOR, lambda P: regexp.search(P._)

  # select URLs not matching regexp
  # -/regexp/
  if action.startswith('-/'):
    if action.endswith('/'):
      regexp = action[2:-1]
    else:
      regexp = action[2:]
    regexp = re.compile(regexp)
    if regexp.groupindex:
      raise ValueError(
          "named groups may not be used in regexp rejection patterns"
      )
    return FUNC_SELECTOR, lambda P: not regexp.search(P._)

  # parent
  # ..
  if action == '..':
    return FUNC_ONE_TO_ONE, pilferify11(lambda P: P._.parent)

  # select URLs ending in particular extensions
  if action.startswith('.'):
    if action.endswith('/i'):
      exts, case = action[1:-2], False
    else:
      exts, case = action[1:], True
    exts = exts.split(',')
    return FUNC_SELECTOR, lambda P: has_exts(P._, exts, case_sensitive=case)

  # select URLs not ending in particular extensions
  if action.startswith('-.'):
    if action.endswith('/i'):
      exts, case = action[2:-2], False
    else:
      exts, case = action[2:], True
    exts = exts.split(',')
    return FUNC_SELECTOR, lambda P: not has_exts(
        P._, exts, case_sensitive=case
    )

  # catch "a.b.c" and convert to "grok:a.b.c"
  m = re_GROK.match(action)
  if m:
    action = "grok:" + action

  # collect leading identifier and process with parse
  name, offset = get_identifier(action)
  if not name:
    raise ValueError("unrecognised special action: %r" % (action,))

  # comparison
  # name==
  if action.startswith('==', offset):
    text = action[offset + 2:]

    def compare(P):
      U = P._
      M = FormatMapping(P, U)
      try:
        vvalue = M[name]
      except KeyError:
        error("unknown variable %r", name)
        raise
      cmp_value = M.format(text)
      return vvalue == cmp_value

    return FUNC_SELECTOR, compare

  # uncomparison
  # name!=
  if action.startswith('!=', offset):
    text = action[offset + 2:]

    def uncompare(P):
      U = P._
      M = FormatMapping(P, U)
      try:
        vvalue = M[name]
      except KeyError:
        error("unknown variable %r", name)
        raise
      cmp_value = M.format(text)
      return vvalue != cmp_value

    return FUNC_SELECTOR, uncompare

  # contains
  # varname(value,value,...)
  if action.startswith('(', offset):
    args, kwargs, offset = parse_args(action, offset + 1, ')')
    if kwargs:
      raise ValueError(
          "you may not have kw= arguments in the 'contains' value list: %r" %
          (kwargs,)
      )
    values = action[m.end():].split(',')

    def in_list(P):
      U = P._
      M = FormatMapping(P, U)
      try:
        vvalue = M[name]
      except KeyError:
        error("unknown variable %r", name)
        raise
      for value in values:
        cvalue = M.format(value)
        if vvalue == cvalue:
          return True
      return False

    return FUNC_SELECTOR, in_list

  # assignment
  # varname=
  if action.startswith('=', offset):
    exprtext = action[offset + 1:]

    def assign(P):
      U = P._
      param_value = P.format_string(exprtext, U)
      P2 = P.copy_with_vars(**{param: param_value})
      return P2

    return FUNC_ONE_TO_ONE, assign

  # test of variable value
  # varname~selector
  if action.startswith('~', offset):
    selector = Action(action[offset + 1:])
    if selector.sig != FUNC_SELECTOR:
      raise ValueError(
          "expected selector function but found: %s" % (selector,)
      )

    def do_test(P):
      U = P._
      M = FormatMapping(P, U)
      try:
        vvalue = M[name]
      except KeyError:
        error("unknown variable %r", name)
        return False
      return selector(P, vvalue)

    return FUNC_SELECTOR, do_test

  if name == 's':
    # s/this/that/
    result_is_Pilfer = False
    if offset == len(action):
      raise ValueError("missing delimiter")
    delim = action[offset]
    delim2pos = action.find(delim, offset + 1)
    if delim2pos < offset + 1:
      raise ValueError("missing second delimiter (%r)" % (delim,))
    regexp = action[offset + 1:delim2pos]
    if not regexp:
      raise ValueError("empty regexp")
    delim3pos = action.find(delim, delim2pos + 1)
    if delim3pos < delim2pos + 1:
      raise ValueError("missing third delimiter (%r)" % (delim,))
    repl_format = action[delim2pos + 1:delim3pos]
    offset = delim3pos + 1
    repl_all = False
    repl_icase = False
    re_flags = 0
    while offset < len(action):
      modchar = action[offset]
      offset += 1
      if modchar == 'g':
        repl_all = True
      elif modchar == 'i':
        repl_icase = True
        re_flags != re.IGNORECASE
      else:
        raise ValueError("unknown s///x modifier: %r" % (modchar,))
    debug(
        "s: regexp=%r, replacement=%r, repl_all=%s, repl_icase=%s", regexp,
        repl_format, repl_all, repl_icase
    )

    def substitute(P):
      ''' Perform a regexp substitution on the source string.
          `replacement` is a format string for the replacement text
          using the str.format method.
          The matched groups from the regexp take the positional arguments 1..n,
          with 0 used for the whole matched string.
          The keyword arguments consist of '_' for the whole matched string
          and any named groups.
      '''
      src = P._
      debug(
          "SUBSTITUTE: src=%r, regexp=%r, replacement=%r, replace_all=%s)...",
          src, regexp.pattern, replacement, replace_all
      )
      strs = []
      offset = 0
      for m in regexp.finditer(src):
        # positional replacements come from the match groups
        repl_args = [m.group(0)] + list(m.groups())
        # named replacements come from the named regexp groups
        repl_kw = {'_': m.group(0)}
        repl_kw.update(m.groupdict())
        # save the unmatched section
        strs.append(src[offset:m.start()])
        # save the matched section with replacements
        strs.append(replacement.format(*repl_args, **repl_kw))
        offset = m.end()
        if not replace_all:
          break
      # save the final unmatched section
      strs.append(src[offset:])
      result = ''.join(strs)
      debug("SUBSTITUTE: src=%r, result=%r", src, result)
      if isURL(src):
        result = URL(result, src.referer)
      return result

    return FUNC_ONE_TO_ONE, substitute

  if name in ("copy", "divert", "pipe"):
    # copy:pipe_name[:selector]
    # divert:pipe_name[:selector]
    # pipe:pipe_name[:selector]
    marker = action[offset]
    offset += 1
    pipe_name, offset = get_identifier(action, offset)
    if not pipe_name:
      raise ValueError("no pipe name")
    if offset >= len(action):
      selector = None
    else:
      if action[offset] != marker:
        raise ValueError(
            "expected second marker to match first: expected %r, saw %r" %
            (marker, action[offset])
        )
      selector = Action(action[offset + 1:])
      if selector.sig != FUNC_SELECTOR:
        raise ValueError(
            "expected selector function but found: %s" % (selector,)
        )
    if name == 'copy':

      def copy(self, P):
        if selector is None or selector(P):
          pipe = P.diversion(pipe_name)
          pipe.put(P)
        return P

      return FUNC_ONE_TO_ONE, copy
    elif name == 'divert':

      def divert(self, P):
        if selector is None or selector(P):
          pipe = P.diversion(pipe_name)
          pipe.put(P)
        else:
          yield P

      return FUNC_ONE_TO_MANY, divert
    elif name == 'pipe':
      return ActionPipeTo(action0, pipe_name)
    else:
      raise RuntimeError("unhandled action %r" % (name,))

  if name == 'grok' or name == 'grokall':
    # grok:a.b.c.d[:args...]
    # grokall:a.b.c.d[:args...]
    result_is_Pilfer = True
    if offset >= len(action):
      raise ValueError("missing marker")
    marker = action[offset]
    offset += 1
    grokker, offset = get_dotted_identifier(action, offset)
    if '.' not in grokker:
      raise ValueError("no dotted identifier found")
    grok_module, grok_funcname = grokker.rsplit('.', 1)
    if offset >= len(action):
      args, kwargs = (), {}
    elif action[offset] != marker:
      raise ValueError(
          "expected second marker to match first: expected %r, saw %r" %
          (marker, action[offset])
      )
    else:
      args, kwargs, offset = parse_action_args(action, offset)
    if offset < len(action):
      raise ValueError("unparsed content after args: %r", action[offset:])
    if name == "grok":

      @returns_Pilfer
      def grok(P):
        ''' Grok performs a user-specified analysis on the supplied Pilfer state `P`.
            (The current value, often an URL, is `P._`.)
            Import `func_name` from module `module_name`.
            Call `func_name( P, *a, **kw ).
            Receive a mapping of variable names to values in return.
            If not empty, copy P and apply the mapping via which is applied
            with P.set_user_vars().
            Returns P (possibly copied), as this is a one-to-one function.
        '''
        mfunc = P.import_module_func(grok_module, grok_funcname)
        if mfunc is None:
          error("import fails")
        else:
          var_mapping = mfunc(P, *args, **kwargs)
          if var_mapping:
            debug("grok: var_mapping=%r", var_mapping)
            P = P.copy('user_vars')
            P.set_user_vars(**var_mapping)
        return P

      return FUNC_ONE_TO_ONE, grok
    elif name == "grokall":

      @yields_Pilfer
      def grokall(Ps):
        ''' Grokall performs a user-specified analysis on the items.
            Import `func_name` from module `module_name`.
            Call `func_name( Ps, *a, **kw ).
            Receive a mapping of variable names to values in return,
            which is applied to each item[0] via .set_user_vars().
            Return the possibly copied Ps.
        '''
        if not isinstance(Ps, list):
          Ps = list(Ps)
        if Ps:
          mfunc = Ps[0].import_module_func(grok_module, grok_funcname)
          if mfunc is None:
            error("import fails")
          else:
            try:
              var_mapping = mfunc(Ps, *args, **kwargs)
            except Exception:
              exception("call")
            else:
              if var_mapping:
                Ps = [P.copy('user_vars') for P in Ps]
              for P in Ps:
                P.set_user_vars(**var_mapping)
        return Ps

      return FUNC_ONE_TO_MANY, grokall
    else:
      raise RuntimeError("unhandled action %r", name)

  if name == 'for':
    # for:varname=value,...
    # for:varname:{start}..{stop}
    # warning: implies 'per'
    if offset == len(action) or action[offset] != ':':
      raise ValueError("missing colon")
    offset += 1
    varname, offset = get_identifier(action, offset)
    if not varname:
      raise ValueError("missing varname")
    if offset == len(action):
      raise ValueError("missing =values or :start..stop")
    marker = action[offset]
    if marker == '=':
      # for:varname=value,...
      values = action[offset + 1:]

      def for_specific(P):
        U = P._
        # expand "values", split on whitespace, iterate with new Pilfer
        value_list = P.format_string(values, U).split()
        for value in value_list:
          yield P.copy_with_vars(**{varname: value})

      return FUNC_ONE_TO_MANY, for_specific
    if marker == ':':
      # for:varname:{start}..{stop}
      start, stop = action[offset + 1:].split('..', 1)

      def for_range(P):
        U = P._
        # expand "values", split on whitespace, iterate with new Pilfer
        istart = int(P.format_string(start, U))
        istop = int(P.format_string(stop, U))
        for value in range(istart, istop + 1):
          yield P.copy_with_vars(**{varname: str(value)})

      return FUNC_ONE_TO_MANY, for_range
    raise ValueError("unrecognised marker after varname: %r", marker)

  if name in ('see', 'seen', 'unseen'):
    # see[:seenset,...[:value]]
    # seen[:seenset,...[:value]]
    # unseen[:seenset,...[:value]]
    seensets = ('_',)
    value = '{_}'
    if offset < len(action):
      marker = action[offset]
      seensets, offset = get_other_chars(action, offset + 1, marker)
      seensets = seensets.split(',')
      if not seensets:
        seensets = ('_',)
      if offset < len(action):
        if action[offset] != marker:
          raise ValueError(
              "parse should have a second marker %r at %r" % (action[offset:])
          )
        value = action[offset + 1:]
        if not value:
          value = '{_}'
    if name == 'see':
      func_sig = FUNC_ONE_TO_ONE

      def see(P):
        U = P._
        see_value = P.format_string(value, U)
        for seenset in seensets:
          P.see(see_value, seenset)
        return P

      return FUNC_ONE_TO_ONE, see
    if name == 'seen':

      def seen(P):
        U = P._
        see_value = P.format_string(value, U)
        return any([P.seen(see_value, seenset) for seenset in seensets])

      return FUNC_SELECTOR, seen
    if name == 'unseen':

      def unseen(P):
        U = P._
        see_value = P.format_string(value, U)
        return not any([P.seen(see_value, seenset) for seenset in seensets])

      return FUNC_SELECTOR, unseen
    raise RuntimeError("unsupported action %r", name)

  if name == 'unique':
    # unique
    seen = set()

    def unique(P):
      value = P._
      if value not in seen:
        seen.add(value)
        yield P

    return FUNC_ONE_TO_MANY, unique

  if action == 'first':
    is_first = [True]

    def first(P):
      if is_first[0]:
        is_first[0] = False
        return True

    return FUNC_SELECTOR, first

  if action == 'new_save_dir':
    # create a new directory based on {save_dir} and update save_dir to match
    def new_save_dir(P):
      return P.copy_with_vars(save_dir=new_dir(P.save_dir))

    return FUNC_ONE_TO_ONE, new_save_dir

  # some other function: gather arguments and then look up function by name in mappings
  if offset < len(action):
    marker = action[offset]
    args, kwargs, offset = parse_action_args(action, offset + 1)
    if offset < len(action):
      raise ValueError(
          "unparsed text after arguments: %r (found a=%r, kw=%r)" %
          (action[offset:], args, kwargs)
      )
  else:
    args = ()
    kwargs = {}

  if name in many_to_many:
    # many-to-many functions get passed straight in
    sig = FUNC_MANY_TO_MANY
    func = many_to_many[name]
  elif name in one_to_many:
    sig = FUNC_ONE_TO_MANY
    func = one_to_many[name]
  elif name in one_to_one:
    func = one_to_one[name]
    sig = FUNC_ONE_TO_ONE
  elif name in one_test:
    func = one_test[name]
    sig = FUNC_SELECTOR
  else:
    raise ValueError("unknown action")

  # pretty up lambda descriptions
  if func.__name__ == '<lambda>':
    func.__name__ = '<lambda %r>' % (name,)
  if sig == FUNC_ONE_TO_ONE:
    func = pilferify11(func)
  elif sig == FUNC_ONE_TO_MANY:
    func = pilferify1m(func)

  if args or kwargs:

    def func_args(*a, **kw):
      a2 = args + a
      kw2 = dict(kwargs)
      kw2.update(kw)
      return func(*a2, **kw2)

    func = func_args
  return sig, func

def parse_action_args(action, offset, delim=None):
  ''' Parse [[kw=]arg[,[kw=]arg...] from `action` at `offset`, return (args, kwargs, offset).
     An arg is a quoted string or a sequence of nonwhitespace excluding `delim` and comma.
  '''
  other_chars = ',' + whitespace
  if delim is not None:
    other_chars += delim
  args = []
  kwargs = {}
  while offset < len(action):
    with Pfx("parse_action_args(%r)", action[offset:]):
      ch1 = action[offset]
      if delim is not None and ch1 == delim:
        break
      if ch1 == ',':
        offset += 1
        continue
      kw = None
      # gather leading "kw=" if present
      name, offset1 = get_identifier(action, offset)
      if name and offset1 < len(action) and action[offset1] == '=':
        kw = name
        offset = offset1 + 1
      if ch1 == '"' or ch1 == "'":
        arg, offset = get_qstr(action, offset, q=ch1)
      else:
        arg, offset = get_other_chars(action, offset, other_chars)
  return args, kwargs, offset

def retriable(func):
  ''' A decorator for a function to probe the Pilfer flags and raise RetryError if unsatisfied.
  '''

  def retry_func(P, *a, **kw):
    ''' Call func after testing flags.
    '''
    if not P.test_flags():
      raise RetryError('flag conjunction fails: %s' % (' '.join(P.flagnames)))
    return func(P, *a, **kw)

  retry_func.__name__ = 'retriable(%s)' % (funcname(func),)
  return retry_func

class _Action(NS):

  def __init__(self, srctext, sig):
    self.srctext = srctext
    self.sig = sig

  def __str__(self):
    s = "Action(%s:%r" % (self.variety, self.srctext)
    if self.args:
      s += ",args=%r" % (self.args,)
    if self.kwargs:
      s += ",kwargs=%r" % (self.kwargs,)
    s += ")"
    return s

  def __call__(self, P):
    ''' Calling an _Action with an item creates a functor and passes the item to it.
    '''
    return self.functor()(P, *self.args, **self.kwargs)

  @property
  def variety(self):
    ''' Textual representation of functor style.
    '''
    sig = self.sig
    if sig == FUNC_ONE_TO_ONE:
      return "ONE_TO_ONE"
    if sig == FUNC_ONE_TO_MANY:
      return "SELECTOR"
    if sig == FUNC_MANY_TO_MANY:
      return "MANY_TO_MANY"
    if sig == FUNC_PIPELINE:
      return "PIPELINE"
    return "UNKNOWN(%d)" % (sig,)

class ActionFunction(_Action):

  def __init__(self, action0, sig, func):
    _Action.__init__(self, action0, sig)
    # stash a retriable version of the function
    self.func = retriable(func)

  def functor(self, L):
    return self.func

class ActionPipeTo(_Action):

  def __init__(self, action0, pipespec):
    _Action.__init__(self, action0, FUNC_PIPELINE)
    self.pipespec = pipespec

  class _OnDemandPipeline(MultiOpenMixin):

    def __init__(self, pipespec, L):
      MultiOpenMixin.__init__(self)
      self.pipespec = pipespec
      self.later = L
      self._Q = None

    @property
    def outQ(self):
      X("GET _OnDemandPipeline.outQ")
      return self._Q.outQ

    def put(self, P):
      with self._lock:
        Q = self._Q
        if Q is None:
          X(
              "ActionPipeTo._OnDemandPipeline: create pipeline from %s",
              self.pipespec
          )
          Q = self._Q = P.pipe_from_spec(self.pipespec)
      self._pipeline.put(P)

  def functor(self, L):
    ''' Return an _OnDemandPipeline to process piped items.
    '''
    X("ActionPipeTo: create _OnDemandPipeline(%s)", self.pipespec)
    return self._OnDemandPipeline(self.pipespec, L)

class ActionShellFilter(_Action):

  def __init__(self, action0, shcmd, args, kwargs):
    _Action.__init__(action0, FUNC_PIPELINE, args, kwargs)
    self.shcmd = shcmd

  # TODO: substitute parameters into shcmd
  def functor(self):
    ''' Return an iterable queue interface to a shell pipeline.
    '''
    return self.ShellProcFilter(self.shcmd)

class ShellProcFilter(MultiOpenMixin):
  ''' An iterable queue-like interface to a filter subprocess.
  '''

  def __init__(self, shcmd, outQ):
    ''' Set up a subprocess running `shcmd`.
        `no_flush`: do not flush input lines for the subprocess, block buffer instead.
        `discard`: discard .put items, close subprocess stdin immediately after startup.
    '''
    MultiOpenMixin.__init__(self)
    self.shcmd = shcmd
    self.shproc = None
    self.outQ = outQ
    outQ.open()

  def _startproc(self, shcmd):
    self.shproc = Popen(shcmd, shell=True, stdin=PIPE, stdout=PIPE)

    def copy_out(fp, outQ):
      ''' Copy lines from the shell output, put new Pilfers onto the outQ.
      '''
      for line in fp:
        if not line.endswith('\n'):
          raise ValueError('premature EOF (missing newline): %r' % (line,))
        outQ.put(P.copy_with_vars(_=line[:-1]))
      outQ.close()

    self.copier = Thread(
        name="%s.copy_out" % (self,),
        target=copy_out,
        args=(shproc.stdout, self.outQ)
    ).start()

  def put(self, P):
    with self._lock:
      if self.shproc is None:
        self._startproc()
    self.shproc.stdin.write(P._)
    self.shproc.stdin.write('\n')
    if not self.no_flush:
      self.shproc.stdin.flush()

  def shutdown(self):
    if self.shproc is None:
      outQ.close()
    else:
      self.shproc.wait()
      xit = self.shproc.returncode
      if xit != 0:
        error("exit %d from: %r", xit, self.shcmd)
    self.shproc.stdin.close()

class ActionShellCommand(_Action):

  def __init__(self, action0, shcmd, args, kwargs):
    _Action.__init__(action0, FUNC_PIPELINE, args, kwargs)
    self.shcmd = shcmd

  # TODO: substitute parameters into shcmd
  def functor(self):
    ''' Return an iterable queue interface to a shell pipeline.
    '''
    return self.ShellProcCommand(self.shcmd, self.outQ)

class ShellProcCommand(MultiOpenMixin):
  ''' An iterable queue-like interface to a shell command subprocess.
  '''

  def __init__(self, shcmd, outQ):
    ''' Set up a subprocess running `shcmd`.
        `discard`: discard .put items, close subprocess stdin immediately after startup.
    '''
    MultiOpenMixin.__init__(self)
    self.shcmd = shcmd
    self.shproc = None
    self.outQ = outQ
    outQ.open()

  def _startproc(self, shcmd):
    self.shproc = Popen(shcmd, shell=True, stdin=PIPE, stdout=PIPE)
    self.shproc.stdin.close()

    def copy_out(fp, outQ):
      ''' Copy lines from the shell output, put new Pilfers onto the outQ.
      '''
      for line in fp:
        if not line.endswith('\n'):
          raise ValueError('premature EOF (missing newline): %r' % (line,))
        outQ.put(P.copy_with_vars(_=line[:-1]))
      outQ.close()

    self.copier = Thread(
        name="%s.copy_out" % (self,),
        target=copy_out,
        args=(self.shproc.stdout, self.outQ)
    ).start()

  def put(self, P):
    with self._lock:
      if self.shproc is None:
        self._startproc()
    self.shproc.stdin.write(P._)
    self.shproc.stdin.write('\n')
    if not self.no_flush:
      self.shproc.stdin.flush()

  def shutdown(self):
    if self.shproc is None:
      self.outQ.close()
    else:
      self.shproc.wait()
      xit = self.shproc.returncode
      if xit != 0:
        error("exit %d from: %r", xit, self.shcmd)

def action_shcmd(shcmd):
  ''' Return (function, func_sig) for a shell command.
  '''
  shcmd = shcmd.strip()

  @yields_str
  def function(P):
    U = P._
    uv = P.user_vars
    try:
      v = P.format_string(shcmd, U)
    except KeyError as e:
      warning("shcmd.format(%r): KeyError: %s", uv, e)
    else:
      with Pfx(v):
        with open('/dev/null') as fp0:
          fd0 = fp0.fileno()
          try:
            subp = Popen(
                ['/bin/sh', '-c', 'sh -uex; ' + v],
                stdin=fd0,
                stdout=PIPE,
                close_fds=True
            )
          except Exception as e:
            exception("Popen: %r", e)
            return
        for line in subp.stdout:
          if line.endswith('\n'):
            yield line[:-1]
          else:
            yield line
        subp.wait()
        xit = subp.returncode
        if xit != 0:
          warning("exit code = %d", xit)

  return function, FUNC_ONE_TO_MANY

def action_pipecmd(shcmd):
  ''' Return (function, func_sig) for pipeline through a shell command.
  '''
  shcmd = shcmd.strip()

  @yields_str
  def function(items):
    if not isinstance(items, list):
      items = list(items)
    if not items:
      return
    P = items[0]
    uv = P.user_vars
    try:
      v = P.format_string(shcmd, P._)
    except KeyError as e:
      warning("pipecmd.format(%r): KeyError: %s", uv, e)
    else:
      with Pfx(v):
        # spawn the shell command
        try:
          subp = Popen(
              ['/bin/sh', '-c', 'sh -uex; ' + v],
              stdin=PIPE,
              stdout=PIPE,
              close_fds=True
          )
        except Exception as e:
          exception("Popen: %r", e)
          return
        # spawn a daemon thread to feed items to the pipe
        def feedin():
          for P in items:
            print(P._, file=subp.stdin)
          subp.stdin.close()

        T = Thread(target=feedin, name='feedin to %r' % (v,))
        T.daemon = True
        T.start()
        # read lines from the pipe, trim trailing newlines and yield
        for line in subp.stdout:
          if line.endswith('\n'):
            yield line[:-1]
          else:
            yield line
        subp.wait()
        xit = subp.returncode
        if xit != 0:
          warning("exit code = %d", xit)

  return function, FUNC_MANY_TO_MANY

class PipeSpec(NS):
  ''' A pipeline specification: a name and list of actions.
  '''

  def __init__(self, name, argv):
    super().__init__()
    self.name = name
    self.argv = argv

  @logexc
  def pipe_funcs(self, L, action_map, do_trace):
    ''' Compute a list of functions to implement a pipeline.
        It is important that this list is constructed anew for each
        new pipeline instance because many of the functions rely
        on closures to track state.
    '''
    with Pfx(self.name):
      pipe_funcs, errors = argv_pipefuncs(self.argv, L, action_map, do_trace)
    return pipe_funcs, errors

def load_pilferrcs(pathname):
  ''' Load PilferRC instances from the supplied `pathname`, recursing if this is a directory.
      Return a list of the PilferRC instances obtained.
  '''
  rcs = []
  with Pfx(pathname):
    if os.path.isfile(pathname):
      # filename: load pilferrc file
      rcs.append(PilferRC(pathname))
    elif os.path.isdir(pathname):
      # directory: load pathname/rc and then pathname/*.rc
      # recurses if any of these are also directories
      rcpath = os.path.join(pathname, "rc")
      if os.path.exists(rcpath):
        rcs.extend(load_pilferrcs(rcpath))
      subrcs = sorted(
          name for name in os.listdir(pathname)
          if not name.startswith('.') and name.endswith('.rc')
      )
      for subrc in subrcs:
        rcpath = os.path.join(pathname, subrc)
        rcs.extend(load_pilferrcs(rcpath))
    else:
      warning("neither a file nor a directory, ignoring")
  return rcs

class PilferRC(NS):

  def __init__(self, filename):
    ''' Initialise the PilferRC instance. Load values from `filename` if not None.
    '''
    super().__init__()
    self.filename = filename
    self._lock = Lock()
    self.defaults = {}
    self.pipe_specs = {}
    self.action_map = {}
    self.seen_backing_paths = {}
    if filename is not None:
      self.loadrc(filename)

  @locked
  def add_pipespec(self, spec, pipe_name=None):
    ''' Add a PipeSpec to this Pilfer's collection, optionally with a different `pipe_name`.
    '''
    if pipe_name is None:
      pipe_name = spec.name
    specs = self.pipe_specs
    if pipe_name in specs:
      raise KeyError("pipe %r already defined" % (pipe_name,))
    specs[pipe_name] = spec

  def loadrc(self, filename):
    ''' Read a pilferrc file and load pipeline definitions.
    '''
    trace("load %s", filename)
    with Pfx(filename):
      cfg = ConfigParser()
      with open(filename) as fp:
        cfg.readfp(fp)
      self.defaults.update(cfg.defaults().items())
      if cfg.has_section('actions'):
        for action_name in cfg.options('actions'):
          with Pfx('[actions].%s', action_name):
            self.action_map[action_name] = shlex.split(
                cfg.get('actions', action_name)
            )
      if cfg.has_section('pipes'):
        for pipe_name in cfg.options('pipes'):
          with Pfx('[pipes].%s', pipe_name):
            pipe_spec = cfg.get('pipes', pipe_name)
            debug("loadrc: pipe = %s", pipe_spec)
            self.add_pipespec(PipeSpec(pipe_name, shlex.split(pipe_spec)))
      # load [seen] name=>backing_path mapping
      # NB: not yet envsub()ed
      if cfg.has_section('seen'):
        for setname in cfg.options('seen'):
          backing_path = cfg.get('seen', setname).strip()
          self.seen_backing_paths[setname] = backing_path

  def __getitem__(self, pipename):
    ''' Fetch PipeSpec by name.
    '''
    return self.pipe_specs[pipename]

  def __setitem__(self, pipename, pipespec):
    specs = self.pipespecs
    if pipename in specs:
      raise KeyError("repeated definition of pipe named %r", pipename)
    specs[pipename] = pipespec

if __name__ == '__main__':
  sys.exit(main(sys.argv))
