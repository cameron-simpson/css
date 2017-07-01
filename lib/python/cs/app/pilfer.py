#!/usr/bin/env python
#
# Web page utility.
#       - Cameron Simpson <cs@zip.com.au> 07jul2010
#

from __future__ import with_statement, print_function
import sys
import os
import os.path
import errno
import shlex
from collections import defaultdict
from copy import copy
from functools import partial
from itertools import chain
import re
if sys.hexversion < 0x02060000: from sets import Set as set
from getopt import getopt, GetoptError
from string import Formatter
from subprocess import Popen, PIPE
from time import sleep
from threading import Lock, RLock, Thread
try:
  from urllib.parse import quote, unquote
except ImportError:
  from urllib import quote, unquote
try:
  from urllib.error import HTTPError, URLError
except ImportError:
  from urllib2 import HTTPError, URLError
try:
  from urllib.request import build_opener, HTTPBasicAuthHandler, HTTPCookieProcessor
except ImportError:
  from urllib2 import build_opener, HTTPBasicAuthHandler, HTTPCookieProcessor
try:
  import xml.etree.cElementTree as ElementTree
except ImportError:
  import xml.etree.ElementTree as ElementTree
from cs.debug import thread_dump, ifdebug
from cs.env import envsub
from cs.excutils import noexc, noexc_gen, logexc, logexc_gen, LogExceptions
from cs.fileutils import file_property, mkdirn
from cs.later import Later, RetryError, \
                    FUNC_ONE_TO_ONE, FUNC_ONE_TO_MANY, FUNC_SELECTOR, FUNC_MANY_TO_MANY
from cs.lex import get_identifier, is_identifier, get_other_chars
import cs.logutils
from cs.logutils import setup_logging, logTo, info, debug, error, warning, exception, trace, D
from cs.mappings import MappingChain, SeenSet
from cs.pfx import Pfx
from cs.app.flag import PolledFlags
import cs.obj
from cs.obj import O
from cs.py.func import funcname, funccite, yields_type, returns_type
from cs.py.modules import import_module_name
from cs.py3 import input, ConfigParser, sorted, ustr, unicode
from cs.queues import NullQueue, NullQ, IterableQueue
from cs.seq import seq
from cs.threads import locked, locked_property
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
    if op.startswith('http://') or op.startswith('https://') :
      # push the URL back and infer missing "url" op word
      argv.insert(0, op)
      op ='url'
    with Pfx(op):
      if op == 'url':
        if not argv:
          error("missing URL")
          badopts = True
        else:
          url = argv.pop(0)

          # load any named pipeline definitions on the command line
          # these are of the form: pipename:{ action... }
          rc = PilferRC(None)
          P.rcs.insert(0, rc)
          while len(argv) and argv[0].endswith(':{'):
            openarg = argv[0]
            with Pfx(openarg):
              spec, argv2, errors = get_pipeline_spec(argv)
              argv = argv2
              if spec is None:
                errors.insert(0, "invalid pipe opening token: %r" % (openarg,))
              if errors:
                badopts = True
                for err in errors:
                  error(err)
              else:
                try:
                  rc.add_pipespec(spec)
                except KeyError as e:
                  error("add pipe: %s", e)
                  badopts = True

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
                  D("PINGER: L: quiescing=%s, state=%r: %s", L._quiescing, L._state, L)
                  sleep(2)
              ping = Thread(target=pinger, args=(LTR,))
              ping.daemon = True
              ping.start()
            with LTR as L:
              P.later = L
              # construct the pipeline
              pipeline = L.pipeline(pipe_funcs,
                                    name="MAIN",
                                    outQ=NullQueue(name="MAIN_PIPELINE_END_NQ",
                                                   blocking=True).open()
                                   )
              with pipeline:
                for U in urls(url, stdin=stdin, cmd=cmd):
                  pipeline.put( P.copy_with_vars(_=U) )
              # wait for main pipeline to drain
              LTR.state("drain main pipeline")
              for item in pipeline.outQ:
                warn("main pipeline output: escaped: %r", item)
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

def yields_str(func):
  ''' Decorator for generators which should yield strings.
  '''
  return yields_type(func, (str, unicode))

def returns_bool(func):
  ''' Decorator for functions which should return Booleans.
  '''
  return returns_type(func, bool)

def returns_str(func):
  ''' Decorator for functions which should return strings.
  '''
  return returns_type(func, (str, unicode))

@yields_str
def urls(url, stdin=None, cmd=None):
  ''' Generator to yield input URLs.
  '''
  if stdin is None:
    stdin = sys.stdin
  if cmd is None:
    cmd = cs.logutils.cmd
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
      of functions to construct a Later.pipeline.
  '''
  # we reverse the list to make action expansion easier
  argv = list(argv)
  errors = []
  pipe_funcs = []
  while argv:
    action = argv.pop(0)
    # support commenting of individual actions
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
      func_sig, function = action_per(action, argv)
      argv = []
      pipe_funcs.append( (func_sig, function) )
    else:
      # regular action
      try:
        func_sig, function = action_func(action, do_trace)
      except ValueError as e:
        errors.append("bad action %r: %s" % (action, e))
      else:
        if func_sig != FUNC_MANY_TO_MANY:
          # other functions are called with each item
          function = retriable(function)
        pipe_funcs.append( (func_sig, function) )
  return pipe_funcs, errors

def get_pipeline_spec(argv):
  ''' Parse a leading pipeline specification from the list of arguments `argv`.
      A pipeline specification is specified by a leading argument
      of the form "pipe_name:{", followed by arguments defining
      functions for the pipeline, and a terminating argument of the
      form "}".

      Return `(spec, argv2, errors)` where `spec` is a PipeSpec
      embodying the specification, `argv2` is the list of arguments
      after the specification and `errors` is a list of error
      messages encountered parsing the function arguments.

      If the leading argument does not commence a function specification
      then `spec` will be None and `argv2` will be `argv`.

      Note: this syntax works well with traditional Bourne shells.
      Zsh users can use 'setopt IGNORE_CLOSE_BRACES' to get
      sensible behaviour. Bash users may be out of luck.
  '''
  errors = []
  pipe_name = None
  spec = None
  if not argv:
    # no arguments, no spec
    argv2 = argv
  else:
    arg = argv[0]
    if not arg.endswith(':{'):
      # not a start-of-spec
      argv2 = argv
    else:
      pipe_name, offset = get_identifier(arg)
      if not pipe_name or offset != len(arg)-2:
        # still not a start-of-spec
        argv2 = argv
      else:
        with Pfx(arg):
          # started with "foo:{"; gather spec until "}"
          for i in range(1, len(argv)):
            if argv[i] == '}':
              spec = PipeSpec(pipe_name, argv[1:i])
              argv2 = argv[i+1:]
              break
          if spec is None:
            errors.append('%s: missing closing "}"' % (arg,))
            argv2 = argv[1:]
  return spec, argv2, errors

def notNone(v, name="value"):
  if v is None:
    raise ValueError("%s is None" % (name,))
  return True

def url_xml_find(U, match):
  for found in url_io(URL(U, None).xml_find_all, (), match):
    yield ElementTree.tostring(found, encoding='utf-8')

class Pilfer(O):
  ''' State for the pilfer app.
      Notable attribute include:
        .flush_print    Flush output after print(), default False.
        .user_agent     Specify user-agent string, default None.
        .user_vars      Mapping of user variables for arbitrary use.
  '''

  def __init__(self, *a, **kw):
    self._name = 'Pilfer-%d' % (seq(),)
    self._lock = Lock()
    self.user_vars = { 'save_dir': '.' }
    self._ = None
    self.flush_print = False
    self.do_trace = False
    self.flags = PolledFlags()
    self._print_to = None
    self._print_lock = Lock()
    self.user_agent = None
    self._lock = RLock()
    self.rcs = []               # chain of PilferRC libraries
    self.seensets = {}
    self.diversions_map = {}        # global mapping of names to divert: pipelines
    self.opener = build_opener()
    self.opener.add_handler(HTTPBasicAuthHandler(NetrcHTTPPasswordMgr()))
    self.opener.add_handler(HTTPCookieProcessor())
    O.__init__(self, **kw)

  def __str__(self):
    return "%s[%s]" % (self._name, self._)
  __repr__ = __str__

  def copy(self, *a, **kw):
    ''' Convenience function to shallow copy this Pilfer with modifications.
    '''
    return cs.obj.copy(self, *a, **kw)

  @property
  def defaults(self):
    ''' Mapping for default values formed by cascading PilferRCs.
    '''
    return MappingChain(mappings=[ rc.defaults for rc in self.rcs ])

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
      backing_path = MappingChain(mappings=[ rc.seen_backing_paths for rc in self.rcs ]).get(name)
      if backing_path is not None:
        backing_path = envsub(backing_path)
        if ( not os.path.isabs(backing_path)
         and not backing_path.startswith('./')
         and not backing_path.startswith('../')
           ):
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
        A diversion enbodies a pipeline of the specified name.
        There is only one of a given name in the shared state.
        They are instantiated at need.
    '''
    diversions = self.diversions_map
    if pipe_name not in diversions:
      spec = self.pipes.get(pipe_name)
      if spec is None:
        raise KeyError("no diversion named %r and no pipe specification found" % (pipe_name,))
      pipe_funcs, errors = spec.pipe_funcs(self.action_map, self.do_trace)
      if errors:
        for err in errors:
          error(err)
        raise KeyError("invalid pipe specification for diversion named %r" % (pipe_name,))
      name = "DIVERSION:%s" % (pipe_name,)
      outQ=NullQueue(name=name, blocking=True)
      outQ.open()   # open outQ so it can be closed at the end of the pipeline
      div = self.later.pipeline(pipe_funcs, name=name, outQ=outQ)
      div.open()    # will be closed in main program shutdown
      diversions[pipe_name] = div
    return diversions[pipe_name]

  @logexc
  def pipe_through(self, pipe_name, inputs):
    ''' Create a new cs.later.Later.pipeline from the specification named `pipe_name`.
        It will collect items from the iterable `inputs`.
        `pipe_name` may be a PipeSpec.
    '''
    if isinstance(pipe_name, PipeSpec):
      spec = pipe_name
      pipe_name = str(spec)
    else:
      spec = self.pipes.get(pipe_name)
      if spec is None:
        raise KeyError("no pipe specification named %r" % (pipe_name,))
    with Pfx("pipe spec %r" % (pipe_name,)):
      name = "pipe_through:%s" % (pipe_name,)
      return self.pipe_from_spec(spec, inputs, name=name)

  def pipe_from_spec(self, spec, inputs, name=None):
    if name is None:
      name = "pipe_from_spec:%s" % (spec,)
    with Pfx("%s", spec):
      pipe_funcs, errors = spec.pipe_funcs(self.action_map, self.do_trace)
      if errors:
        for err in errors:
          error(err)
        raise ValueError("invalid pipe specification")
    return self.later.pipeline(pipe_funcs, name=name, inputs=inputs)

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

  def set_user_vars(self, **kw):
    ''' Update self.user_vars from the keyword arguments.
    '''
    ##for k, v in kw.items():
    ##  if not isinstance(v, (str, unicode)):
    ##    raise TypeError("%s.set_user_vars(%r): non-str value for %r: %r" % (self, kw, k, v))
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
    debug("save_url(U=%r, saveas=%r, dir=%s, overwrite=%r, kw=%r)...", U, saveas, dir, overwrite, kw)
    with Pfx("save_url(%s)", U):
      U = URL(U, None)
      save_dir = self.save_dir
      if saveas is None:
        saveas = os.path.join(save_dir, U.basename)
        if saveas.endswith('/'):
          saveas += 'index.html'
      if saveas == '-':
        sys.stdout.write(U.content)
        sys.stdout.flush()
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
      pylib = [ path for path in envsub(self.defaults.get('pythonpath', '')).split(':') if path ]
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

class FormatArgument(unicode):

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
	The optional paramater `U` (default from `P._`) is the
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
    ks = ( set( [ k for k in dir(self.url) if self._ok_attrkey(k) ] )
         + set(self.pilfer.user_vars.keys())
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
        raise KeyError("unapproved attribute (missing or callable or not public): %r" % (k,))
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
      suffixes = [ sfx.lower() for sfx in suffixes ]
    for sfx in suffixes:
      if base.endswith('.'+sfx):
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
        suffixes = [ sfx.lower() for sfx in suffixes ]
      for sfx in suffixes:
        if base.endswith('.'+sfx):
          ok = True
          break
    if ok:
      yield U
    else:
      debug("with_exts: discard %s", U)

def substitute( P, regexp, replacement, replace_all):
  ''' Perform a regexp substitution on the source string.
      `replacement` is a format string for the replacement text
      using the str.format method.
      The matched groups from the regexp take the positional arguments 1..n,
      with 0 used for the whole matched string.
      The keyword arguments consist of '_' for the whole matched string
      and any named groups.
  '''
  src = P._
  debug("SUBSTITUTE: src=%r, regexp=%r, replacement=%r, replace_all=%s)...",
        src, regexp.pattern, replacement, replace_all)
  strs = []
  sofar = 0
  for m in regexp.finditer(src):
    repl_args = [ m.group(0) ] + list(m.groups())
    repl_kw = { '_': m.group(0) }
    repl_kw.update(m.groupdict())
    strs.append(src[sofar:m.start()])
    strs.append(replacement.format(*repl_args, **repl_kw))
    sofar = m.end()
    if not replace_all:
      break
  strs.append(src[sofar:])
  result = ''.join(strs)
  debug("SUBSTITUTE: src=%r, result=%r", src, result)
  if isURL(src):
    result = URL(result, src.referer)
  return result

def url_delay(U, delay, *a):
  sleep(float(delay))
  return U

def url_query(U, *a):
  U = URL(U, None)
  if not a:
    return U.query
  qsmap = dict( [ ( qsp.split('=', 1) if '=' in qsp else (qsp, '') ) for qsp in U.query.split('&') ] )
  return ','.join( [ unquote(qsmap.get(qparam, '')) for qparam in a ] )

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

@returns_Pilfer
def grok(module_name, func_name, P, *a, **kw):
  ''' Grok performs a user-specified analysis on the supplied Pilfer state `P`.
      (The current value, often an URL, is `P._`.)
      Import `func_name` from module `module_name`.
      Call `func_name( P, *a, **kw ).
      Receive a mapping of variable names to values in return.
      If not empty, copy P and apply the mapping via which is applied
      with P.set_user_vars().
      Returns P (possibly copied), as this is a one-to-one function.
  '''
  mfunc = P.import_module_func(module_name, func_name)
  if mfunc is None:
    error("import fails")
  else:
    var_mapping = mfunc(P, *a, **kw)
    if var_mapping:
      debug("grok: var_mapping=%r", var_mapping)
      P = P.copy('user_vars')
      P.set_user_vars(**var_mapping)
  return P

@yields_Pilfer
def grokall(module_name, func_name, Ps, *a, **kw):
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
    mfunc = Ps[0].import_module_func(module_name, func_name)
    if mfunc is None:
      error("import fails")
    else:
      try:
        var_mapping = mfunc(Ps, *a, **kw)
      except Exception as e:
        exception("call")
      else:
        if var_mapping:
          Ps = [ P.copy('user_vars') for P in Ps ]
        for P in Ps:
          P.set_user_vars(**var_mapping)
  return Ps

def _test_grokfunc( P, *a, **kw ):
  v={ 'grok1': 'grok1value',
      'grok2': 'grok2value',
    }
  return v

# actions that work on the whole list of in-play URLs
many_to_many = {
      'sort':         lambda Ps, key=lambda P: P._, reverse=False: \
                        sorted(Ps, key=key, reverse=reverse),
      'last':         lambda Ps: Ps[-1:],
    }

one_to_many = {
      'hrefs':        lambda P: url_hrefs(P._),
      'srcs':         lambda P: url_srcs(P._),
      'xml':          lambda P, match: url_xml_find(P._, match),
      'xmltext':      lambda P, match: XML(P._).findall(match),
    }

# actions that work on individual Pilfer instances, returning strings
one_to_one = {
      '..':           lambda P: URL(P._, None).parent,
      'delay':        lambda P, delay: (P._, sleep(float(delay)))[0],
      'domain':       lambda P: URL(P._, None).domain,
      'hostname':     lambda P: URL(P._, None).hostname,
      'print':        lambda P, **kw: (P._, P.print_url_string(P._, **kw))[0],
      'query':        lambda P, *a: url_query(P._, *a),
      'quote':        lambda P: quote(P._),
      'unquote':      lambda P: unquote(P._),
      'save':         lambda P, *a, **kw: (P._, P.save_url(P._, *a, **kw))[0],
      's':            substitute,
      'title':        lambda P: P._.page_title,
      'type':         lambda P: url_io(P._.content_type, ""),
      'xmlattr':      lambda P, attr: [ A for A in (ElementTree.XML(P._).get(attr),) if A is not None ],
    }

one_test = {
      'has_title':    lambda P: P._.page_title is not None,
      'reject_re':    lambda P, regexp: not regexp.search(P._),
      'same_domain':  lambda P: notNone(P._.referer, "%r.referer" % (P._,)) and P._.domain == P._.referer.domain,
      'same_hostname':lambda P: notNone(P._.referer, "%r.referer" % (P._,)) and P._.hostname == P._.referer.hostname,
      'same_scheme':  lambda P: notNone(P._.referer, "%r.referer" % (P._,)) and P._.scheme == P._.referer.scheme,
      'select_re':    lambda P, regexp: regexp.search(P._),
    }

re_COMPARE = re.compile(r'(_|[a-z]\w*)==')
re_UNCOMPARE=re.compile(r'(_|[a-z]\w*)!=')
re_CONTAINS= re.compile(r'(_|[a-z]\w*)\(([^()]*)\)')
re_ASSIGN  = re.compile(r'(_|[a-z]\w*)=')
re_TEST    = re.compile(r'(_|[a-z]\w*)~')
re_GROK    = re.compile(r'([a-z]\w*(\.[a-z]\w*)*)\.([_a-z]\w*)', re.I)

def action_func_raw(action, do_trace):
  ''' Accept a string `action` and return a tuple of:
        function, func_sig, result_is_Pilfer
      This is primarily used by action_func below, but also called
      by subparses such as selectors applied to the values of named
      variables.
      result_is_Pilfer: the returned function returns a Pilfer object
        instead of a simple result such as a Boolean or a string.
  '''
  # save original form of action string
  action0 = action
  args = []
  kwargs = {}
  if action.startswith('!'):
    # ! shell command to generate items based off current item
    # receive text lines, stripped
    function, func_sig = action_shcmd(action[1:])
    return function, args, kwargs, func_sig, False
  if action.startswith('|'):
    # | shell command to pipe though
    # receive text lines, stripped
    function, func_sig = action_pipecmd(action[1:])
    return function, args, kwargs, func_sig, False
  # comparison
  # varname==
  m = re_COMPARE.match(action)
  if m:
    function, func_sig = action_compare(m.group(1), action[m.end():])
    return function, args, kwargs, func_sig, False
  # uncomparison
  # varname!=
  m = re_UNCOMPARE.match(action)
  if m:
    function, func_sig = action_uncompare(m.group(1), action[m.end():])
    return function, args, kwargs, func_sig, False
  # contains
  # varname(value,value,...)
  m = re_CONTAINS.match(action)
  if m:
    function, func_sig = action_in_list(m.group(1), action[m.end():])
    return function, args, kwargs, func_sig, False
  # assignment
  # varname=
  m = re_ASSIGN.match(action)
  if m:
    function, func_sig = action_assign(m.group(1), action[m.end():])
    return function, args, kwargs, func_sig, True
  # test of variable value
  # varname~selector
  m = re_TEST.match(action)
  if m:
    function, func_sig = action_test(m.group(1), action[m.end():], do_trace)
    return function, args, kwargs, func_sig, False
  # catch "a.b.c" and convert to "grok:a.b.c"
  m = re_GROK.match(action)
  if m:
    action = 'grok:' + action
  # operator name or "s//"
  function = None
  func_name, offset = get_identifier(action)
  if func_name:
    with Pfx(func_name):
      # an identifier
      if func_name == 's':
        # s/this/that/
        result_is_Pilfer = False
        if offset == len(action):
          raise ValueError("missing delimiter")
        delim = action[offset]
        delim2pos = action.find(delim, offset+1)
        if delim2pos < offset + 1:
          raise ValueError("missing second delimiter (%r)" % (delim,))
        regexp = action[offset+1:delim2pos]
        if not regexp:
          raise ValueError("empty regexp")
        delim3pos = action.find(delim, delim2pos+1)
        if delim3pos < delim2pos+1:
          raise ValueError("missing third delimiter (%r)" % (delim,))
        repl_format = action[delim2pos+1:delim3pos]
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
        debug("s: regexp=%r, replacement=%r, repl_all=%s, repl_icase=%s", regexp, repl_format, repl_all, repl_icase)
        kwargs['regexp'] = re.compile(regexp, flags=re_flags)
        kwargs['replacement'] = repl_format
        kwargs['replace_all'] = repl_all
      elif func_name in ("copy", "divert", "pipe"):
        # copy:pipe_name[:selector]
        # divert:pipe_name[:selector]
        # pipe:pipe_name[:selector]
        func_sig, function, result_is_Pilfer = action_divert_pipe(func_name, action, offset, do_trace)
      elif func_name == 'grok' or func_name == 'grokall':
        # grok:a.b.c.d[:args...]
        # grokall:a.b.c.d[:args...]
        result_is_Pilfer = True
        func_sig, function = action_grok(func_name, action, offset)
      elif func_name == 'for':
        # for:var=value,...
        # for:varname:{start}..{stop}
        # warning: implies 'per'
        func_sig, function, result_is_Pilfer = action_for(func_name, action, offset)
      elif func_name in ('see', 'seen', 'unseen'):
        # see[:seenset,...[:value]]
        # seen[:seenset,...[:value]]
        # unseen[:seenset,...[:value]]
        result_is_Pilfer = False
        func_sig, function = action_sight(func_name, action, offset)
      elif func_name == 'unique':
        # unique
        result_is_Pilfer = False
        func_sig, function = action_unique(func_name, action, offset)
      elif action == 'first':
        result_is_Pilfer = False
        is_first = [True]
        @returns_bool
        def function(item):
          if is_first[0]:
            is_first[0] = False
            return True
          return False
        func_sig = FUNC_SELECTOR
      elif action == 'new_save_dir':
        # create a new directory based on {save_dir} and update save_dir to match
        result_is_Pilfer = True
        @returns_Pilfer
        def function(P):
          return P.copy_with_vars(save_dir=new_dir(P.save_dir))
        func_sig = FUNC_ONE_TO_ONE
      # some other function: gather arguments
      elif offset < len(action):
        result_is_Pilfer = False
        marker = action[offset]
        if marker == ':':
          # followed by :kw1=value,kw2=value,...
          kwtext = action[offset+1:]
          if func_name == "print":
            # print is special - just a format string relying on current state
            kwargs['string'] = kwtext
          else:
            for kw in kwtext.split(','):
              if '=' in kw:
                kw, v = kw.split('=', 1)
                kwargs[kw] = v
              else:
                args.append(kw)
        else:
          raise ValueError("unrecognised marker %r" % (marker,))
    if not function:
      function, func_sig, result_is_Pilfer = function_by_name(func_name)
    else:
      if func_sig is None:
        raise RuntimeError("function is set (%r) but func_sig is None" % (function,))
  # select URLs matching regexp
  # /regexp/
  # named groups in the regexp get applied, per URL, to the variables
  elif action.startswith('/'):
    if action.endswith('/'):
      regexp = action[1:-1]
    else:
      regexp = action[1:]
    regexp = re.compile(regexp)
    if regexp.groupindex:
      # a regexp with named groups
      result_is_Pilfer = True
      @yields_Pilfer
      def function(P):
        U = P._
        m = regexp.search(U)
        if m:
          varmap = m.groupdict()
          if varmap:
            P = P.copy_with_vars(**varmap)
          yield P
      func_sig = FUNC_ONE_TO_MANY
    else:
      # regexp with no named groups: a plain selector
      result_is_Pilfer = False
      function = lambda P: regexp.search(P._)
      func_sig = FUNC_SELECTOR
  # select URLs not matching regexp
  # -/regexp/
  elif action.startswith('-/'):
    if action.endswith('/'):
      regexp = action[2:-1]
    else:
      regexp = action[2:]
    regexp = re.compile(regexp)
    if regexp.groupindex:
      raise ValueError("named groups may not be used in regexp rejection patterns")
    result_is_Pilfer = False
    function = lambda P: not regexp.search(P._)
    func_sig = FUNC_SELECTOR
  # parent
  # ..
  elif action == '..':
    result_is_Pilfer = False
    function = lambda P: P._.parent
    func_sig = FUNC_ONE_TO_ONE
  # select URLs ending in particular extensions
  elif action.startswith('.'):
    if action.endswith('/i'):
      exts, case = action[1:-2], False
    else:
      exts, case = action[1:], True
    exts = exts.split(',')
    result_is_Pilfer = False
    function = lambda P: has_exts( P._, exts, case_sensitive=case )
    func_sig = FUNC_SELECTOR
  # select URLs not ending in particular extensions
  elif action.startswith('-.'):
    if action.endswith('/i'):
      exts, case = action[2:-2], False
    else:
      exts, case = action[2:], True
    exts = exts.split(',')
    result_is_Pilfer = False
    function = lambda P: not has_exts( P._, exts, case_sensitive=case )
    func_sig = FUNC_SELECTOR
  else:
    raise ValueError("unknown function %r" % (func_name,))

  function.__name__ = "action(%r)" % (action0,)
  return function, args, kwargs, func_sig, result_is_Pilfer

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

def action_func(action, do_trace, raw=False):
  ''' Accept a string `action` and return a tuple of:
        func_sig, function
      `func_sig` and `function` are used with Later.pipeline.
      If `raw`, return a tuple of:
        func_sig, function, result_is_Pilfer
      prior to the final step of wrapping functions.
      result_is_Pilfer: the returned function returns a Pilfer object
        instead of a simple result such as a Boolean or a string.
  '''
  # parse action into function and kwargs
  with Pfx("%s", action):
    function, args, kwargs, func_sig, result_is_Pilfer = action_func_raw(action, do_trace)
    # The pipeline itself passes Pilfer objects, whose ._ attribute is the current value.
    #
    # All functions accept a leading Pilfer argument but most emit only
    # a value result (or just a Boolean for selectors).
    # A few emit a Pilfer because they modify it or produce a copy.
    # If "result_is_Pilfer" is true, we expect the latter.
    # Otherwise we wrap FUNC_ONE_TO_ONE and FUNC_ONE_TO_MANY to
    # emit a Pilfer with their outputs.
    # FUNC_MANY_TO_MANY functions have their own convoluted wrapper.
    #
    func0 = function
    if result_is_Pilfer and func_sig not in (FUNC_ONE_TO_ONE, FUNC_ONE_TO_MANY, FUNC_MANY_TO_MANY):
      raise RuntimeError("result_is_Pilfer is true but func_sig == %r" % (func_sig,))
    # convert FUNC_SELECTOR to FUNC_ONE_TO_MANY
    if func_sig == FUNC_SELECTOR:
      func0 = function
      @yields_Pilfer
      def function(P):
        if func0(P, *args, **kwargs):
          yield P
      function.__name__ = "one_to_many(%s)" % (funcname(func0),)
      func_sig = FUNC_ONE_TO_MANY
      result_is_Pilfer = True
    func1 = function
    if func_sig == FUNC_ONE_TO_ONE:
      if result_is_Pilfer:
        function = lambda P: func1(P, *args, **kwargs)
        function = returns_Pilfer(function)
      else:
        @returns_Pilfer
        def function(P):
          U = P._
          U2 = func1(P, *args, **kwargs)
          if isinstance(U2, Pilfer):
            raise TypeError("unexpected Pilfer from %s: %r" % (funccite(func1), U2))
          if U2 != U:
            P = P.copy_with_vars(_=U2)
          return P
    elif func_sig == FUNC_ONE_TO_MANY:
      if result_is_Pilfer:
        @yields_Pilfer
        def function(P):
          for P2 in func1(P, *args, **kwargs):
            yield P2
      else:
        @yields_Pilfer
        def function(P):
          for U in func1(P, *args, **kwargs):
            yield P.copy_with_vars(_=U)
    elif func_sig == FUNC_MANY_TO_MANY:
      if result_is_Pilfer:
        function = lambda Ps: func1(Ps, *args, **kwargs)
      else:
        # Many-to-many functions are different.
        # We make a mapping from P._ to P for each Ps
        # and re-attach the P components by reverse mapping from the U results;
        # unrecognised Us get associated with Ps[0].
        #
        def function(Ps):
          if not isinstance(Ps, list):
            Ps = list(Ps)
          if Ps:
            # preserve the first Pilfer context to attach to unknown items
            P0 = Ps[0]
            idmap = dict( [ ( id(P), P ) for P in Ps ] )
          else:
            P0 = None
            idmap = {}
          # call the inner function
          Us = func1(Ps, *args, **kwargs)
          # return copies of a suitable original Pilfer
          return [ idmap.get(id(U), P0).copy_with_vars(_=U) for U in Us ]
    else:
      raise RuntimeError("unhandled func_sig %r" % (func_sig,))

    @logexc
    def trace_function(*a, **kw):
      with Pfx(action):
        try:
          retval = function(*a, **kw)
        except Exception as e:
          exception("TRACE: EXCEPTION: %s", e)
          raise
        return retval

    trace_function.__name__ = "trace_action(%r)" % (action,)
    return func_sig, trace_function

def function_by_name(func_name):
  ''' Look up `func_name` in mappings of named functions.
      Return (function, func_sig, result_is_Pilfer).
  '''
  # look up function by name in mappings
  if func_name in many_to_many:
    # many-to-many functions get passed straight in
    result_is_Pilfer = True
    function = many_to_many[func_name]
    if function.__name__ == '<lambda>':
      function.__name__ = '<lambda %r>' % func_name
    function = yields_Pilfer(function)
    func_sig = FUNC_MANY_TO_MANY
  elif func_name in one_to_many:
    result_is_Pilfer = False
    function = one_to_many[func_name]
    if function.__name__ == '<lambda>':
      function.__name__ = '<lambda %r>' % func_name
    function = yields_str(function)
    func_sig = FUNC_ONE_TO_MANY
  elif func_name in one_to_one:
    result_is_Pilfer = False
    function = one_to_one[func_name]
    if function.__name__ == '<lambda>':
      function.__name__ = '<lambda %r>' % func_name
    function = returns_str(function)
    func_sig = FUNC_ONE_TO_ONE
  elif func_name in one_test:
    result_is_Pilfer = False
    function = one_test[func_name]
    if function.__name__ == '<lambda>':
      function.__name__ = '<lambda %r>' % func_name
    function = returns_bool(function)
    func_sig = FUNC_SELECTOR
  else:
    raise ValueError("unknown action")
  return function, func_sig, result_is_Pilfer

def action_divert_pipe(func_name, action, offset, do_trace):
  # copy:pipe_name[:selector]
  # divert:pipe_name[:selector]
  # pipe:pipe_name[:selector]
  #
  # Divert or copy selected items to the named pipeline
  # or filter selected items through an instance of the named pipeline.
  if offset == len(action):
    raise ValueError("missing marker")
  marker = action[offset]
  offset += 1
  pipe_name, offset = get_identifier(action, offset)
  if not pipe_name:
    raise ValueError("no pipe name")
  if offset >= len(action):
    sel_function = lambda P: True
    sel_function.__name__ = 'True(%r)' % (action,)
    sel_args = []
    sel_kwargs = {}
  else:
    if marker != action[offset]:
      raise ValueError("expected second marker to match first: expected %r, saw %r"
                       % (marker, action[offset]))
    sel_function, sel_args, sel_kwargs, sel_func_sig, result_is_Pilfer = action_func_raw(action[offset+1:], do_trace=do_trace)
    if sel_func_sig != FUNC_SELECTOR:
      raise ValueError("expected selector function but found: func_sig=%s %r func=%r" % (sel_func_sig, action[offset+1:],sel_function))
    if result_is_Pilfer:
      raise RuntimeError("result_is_Pilfer should be FALSE!")
    sel_function.__name__ = "%r.select(%r)" % (action, action[offset+1:])
  if func_name == "divert":
    # function to divert selected items to a single named pipeline
    func_sig = FUNC_ONE_TO_MANY
    result_is_Pilfer = True
    @logexc
    @yields_Pilfer
    def function(P):
      ''' Divert selected Pilfers to the named pipeline.
      '''
      if sel_function(P, *sel_args, **sel_kwargs):
        try:
          pipe = P.diversion(pipe_name)
        except KeyError:
          error("no pipe named %r", pipe_name)
        else:
          pipe.put(P)
      else:
        yield P
    function.__name__ = "divert_func(%r)" % (action,)
  elif func_name == "copy":
    func_sig = FUNC_ONE_TO_ONE
    result_is_Pilfer = True
    @logexc
    def function(P):
      ''' Copy selected Pilfers to the named pipeline.
      '''
      if sel_function(P, *sel_args, **sel_kwargs):
        try:
          pipe = P.diversion(pipe_name)
        except KeyError:
          error("no pipe named %r", pipe_name)
        else:
          pipe.put(P)
      return P
    function.__name__ = "copy_func(%r)" % (action,)
  elif func_name == "pipe":
    # gather all items and feed to an instance of the specified pipeline
    func_sig = FUNC_MANY_TO_MANY
    result_is_Pilfer = True
    @logexc_gen
    @yields_Pilfer
    def function(items):
      if items:
        P = items[0]
        pipeline = None
        first = True
        with P.later.more_capacity(1):
          for item in items:
            debug("pipe: sel_function=%r, item=%r", sel_function, item)
            status = sel_function(item, *sel_args, **sel_kwargs)
            debug("pipe: sel_function=%r, item=%r: status=%r", sel_function, item, status)
            if status:
              if pipeline is None:
                pipeQ = IterableQueue()
                pipeline = item.pipe_through(pipe_name, pipeQ)
              pipeQ.put(item)
            else:
              yield item
          if pipeline:
            pipeQ.close()
            for item in pipeline.outQ:
              yield item
    function = logexc(function)
    function.__name__ = "pipe_func(%r)" % (action,)
  else:
    raise ValueError("expected \"divert\" or \"pipe\", got func_name=%r" % (func_name,))
  return func_sig, function, result_is_Pilfer

def action_per(action, argv):
  ''' Function to perform a "per": send each item down its own instance of a pipeline.
  '''
  debug("action_per: argv=%r", argv)
  argv = list(argv)
  pipespec = PipeSpec("per:[%s]" % (','.join(argv)), argv)
  @yields_Pilfer
  def function(P):
    debug("action_per func %r per(%r)", function.__name__, P)
    with P.later.more_capacity(1):
      pipeline = P.pipe_through(pipespec, (P,))
      debug("pipe: pipe_though(%s) => %r", pipespec, pipeline)
      for item in pipeline.outQ:
        debug("pipe: postpipe: yield %r", item)
        yield item
  function.__name__ = "%s(%s)" % (action, '|'.join(argv))
  return FUNC_ONE_TO_MANY, function

def action_sight(func_name, action, offset):
  # see[:seenset,...[:value]]
  # seen[:seenset,...[:value]]
  # unseen[:seenset,...[:value]]
  seensets = ('_',)
  value = '{_}'
  if offset < len(action):
    if action[offset] != ':':
      raise ValueError("bad marker after %r, expected ':', found %r", func_name, action[offset])
    seensets, offset = get_other_chars(action, offset+1, ':')
    seensets = seensets.split(',')
    if not seensets:
      seensets = ('_',)
    if offset < len(action):
      if action[offset] != ':':
        raise RuntimeError("parse should have a second colon after %r", action[:offset])
      value = action[offset+1:]
      if not value:
        value = '{_}'
  if func_name == 'see':
    func_sig = FUNC_ONE_TO_ONE
    @returns_str
    def function(P):
      U = P._
      see_value = P.format_string(value, U)
      for seenset in seensets:
        P.see(see_value, seenset)
      return U
  elif func_name == 'seen':
    func_sig = FUNC_SELECTOR
    @returns_bool
    def function(P):
      U = P._
      see_value = P.format_string(value, U)
      return any( [ P.seen(see_value, seenset) for seenset in seensets ] )
  elif func_name == 'unseen':
    func_sig = FUNC_SELECTOR
    @returns_bool
    def function(P):
      U = P._
      see_value = P.format_string(value, U)
      return not any( [ P.seen(see_value, seenset) for seenset in seensets ] )
  else:
    raise RuntimeError("action_sight called with unsupported action %r", func_name)
  return func_sig, function

def action_unique(func_name, action, offset):
  # unique
  #
  seen = set()
  @yields_str
  def function(P):
    U = P._
    if U not in seen:
      seen.add(U)
      yield U
  return FUNC_ONE_TO_MANY, function

def action_for(func_name, action, offset):
  # for:varname=values
  #
  func_sig = FUNC_ONE_TO_MANY
  result_is_Pilfer = True
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
    values = action[offset+1:]
    @yields_Pilfer
    def function(P):
      U = P._
      # expand "values", split on whitespace, iterate with new Pilfer
      value_list = P.format_string(values, U).split()
      for value in value_list:
        yield P.copy_with_vars(**{varname: value})
  elif marker == ':':
    # for:varname:{start}..{stop}
    start, stop = action[offset+1:].split('..', 1)
    @yields_Pilfer
    def function(P):
      U = P._
      # expand "values", split on whitespace, iterate with new Pilfer
      istart = int(P.format_string(start, U))
      istop = int(P.format_string(stop, U))
      for value in range(istart, istop+1):
        yield P.copy_with_vars(**{varname: str(value)})
  else:
    raise ValueError("unrecognised marker after varname: %r", marker)
  return func_sig, function, result_is_Pilfer

def action_grok(func_name, action, offset):
  # grok:a.b.c.d[:args...]
  # grokall:a.b.c.d[:args...]
  #
  # Import "d" from the python module "a.b.c".
  # d() should return a mapping of varname to value.
  #
  # For grok, call d(P, kwargs) and apply the
  # returned mapping to P.user_vars.
  #
  # From grokall, call d( ( P, ...), kwargs) and apply
  # the returned mapping to each P.user_vars.
  #
  is_grokall = func_name == "grokall"
  if offset == len(action):
    raise ValueError("missing marker")
  marker = action[offset]
  offset += 1
  m = re_GROK.match(action[offset:])
  if not m:
    raise ValueError("expected a.b.c.d name at \"%s\"" % (action[offset:],))
  grok_module = m.group(1)
  grok_funcname = m.group(3)
  offset += m.end()
  if offset < len(action):
    if marker != action[offset]:
      raise ValueError("expected second marker to match first: expected %r, saw %r"
                       % (marker, action[offset]))
    offset += 1
    raise RuntimeError("arguments to %s not yet implemented" % (func_name,))
  if is_grokall:
    # grokall: process all the items and yield new items
    func_sig = FUNC_MANY_TO_MANY
    @yields_Pilfer
    def function(items, *a, **kw):
      for item in grokall(grok_module, grok_funcname, items, *a, **kw):
        yield item
  else:
    # grok: process an item
    func_sig = FUNC_ONE_TO_ONE
    @returns_Pilfer
    def function( P, *a, **kw):
      return grok(grok_module, grok_funcname, P, *a, **kw)
  return func_sig, function

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
            subp = Popen(['/bin/sh', '-c', 'sh -uex; '+v], stdin=fd0, stdout=PIPE, close_fds=True)
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
          subp = Popen(['/bin/sh', '-c', 'sh -uex; '+v], stdin=PIPE, stdout=PIPE, close_fds=True)
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

def action_in_list(var, listspec):
  ''' Return (function, func_sig) for a variable value comparison against a list.
  '''
  list_values = listspec.split(',')
  @returns_bool
  def function(P):
    U = P._
    M = FormatMapping(P, U)
    try:
      vvalue = M[var]
    except KeyError:
      error("unknown variable %r", var)
      raise
    for value in list_values:
      cvalue = M.format(value)
      if vvalue == cvalue:
        return True
    return False
  return function, FUNC_SELECTOR

def action_compare(var, value):
  ''' Return (function, func_sig) for a variable value comparison.
  '''
  @returns_bool
  def function(P):
    U = P._
    M = FormatMapping(P, U)
    try:
      vvalue = M[var]
    except KeyError:
      error("unknown variable %r", var)
      raise
    cvalue = M.format(value)
    return vvalue == cvalue
  return function, FUNC_SELECTOR

def action_uncompare(var, value):
  ''' Return (function, func_sig) for a variable value comparison where not equal.
  '''
  @returns_bool
  def function(P):
    U = P._
    M = FormatMapping(P, U)
    try:
      vvalue = M[var]
    except KeyError:
      error("unknown variable %r", var)
      raise
    cvalue = M.format(value)
    return vvalue != cvalue
  return function, FUNC_SELECTOR

def action_test(var, selector, do_trace):
  ''' Return (function, func_sig) for a selector applied to the variable `var`.
  '''
  sel_func_sig, sel_function = action_func(selector, do_trace=do_trace)
  if sel_func_sig != FUNC_SELECTOR:
    raise ValueError("expected selector function but found: %r" % (selector,))
  def function(P):
    U = P._
    M = FormatMapping(P, U)
    try:
      vvalue = M[var]
    except KeyError:
      error("unknown variable %r", var)
      return False
    ##X("TEST: var=%s, P=%s, vvalue=%s, sel_function=%s", var, P, vvalue, sel_function)
    result = sel_function( (P, vvalue) )
    return result
  return function, FUNC_SELECTOR

def action_assign(var, value):
  ''' Return (function, func_sig) for a variable value assignment.
  '''
  def function(P):
    U = P._
    varvalue = P.format_string(value, U)
    P2 = P.copy_with_vars(**{var: varvalue})
    return P2
  return function, FUNC_ONE_TO_ONE

class PipeSpec(O):
  ''' A pipeline specification: a name and list of actions.
  '''

  def __init__(self, name, argv):
    O.__init__(self)
    self.name = name
    self.argv = argv

  @logexc
  def pipe_funcs(self, action_map, do_trace):
    ''' Compute a list of functions to implement a pipeline.
        It is important that this list is constructed anew for each
        new pipeline instance because many of the functions rely
        on closures to track state.
    '''
    with Pfx(self.name):
      pipe_funcs, errors = argv_pipefuncs(self.argv, action_map, do_trace)
    return pipe_funcs, errors

def load_pilferrcs(pathname):
  ''' Load PilferRC instances rom the supplied `pathname`, recursing if this is a directory.
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
      subrcs = sorted( name for name in os.listdir(pathname)
                       if not name.startswith('.') and name.endswith('.rc')
                     )
      for subrc in subrcs:
        rcpath = os.path.join(pathname, subrc)
        rcs.extend(load_pilferrcs(rcpath))
    else:
      warning("neither a file nor a directory, ignoring")
  return rcs

class PilferRC(O):

  def __init__(self, filename):
    ''' Initialise the PilferRC instance. Load values from `filename` if not None.
    '''
    O.__init__(self)
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
            self.action_map[action_name] = shlex.split(cfg.get('actions', action_name))
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
  import sys
  sys.exit(main(sys.argv))
