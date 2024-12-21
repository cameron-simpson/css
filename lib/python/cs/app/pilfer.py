#!/usr/bin/env python3
#
# Web page utility.
#       - Cameron Simpson <cs@cskk.id.au> 07jul2010
#

from collections import namedtuple
from configparser import ConfigParser
from contextlib import contextmanager
from dataclasses import dataclass, field
import os
import os.path
import errno
from getopt import GetoptError
import re
import shlex
from string import Formatter, whitespace
from subprocess import Popen, PIPE
import sys
from threading import Lock, RLock, Thread
from time import sleep
from typing import Iterable
from urllib.parse import quote, unquote
from urllib.error import HTTPError, URLError
from urllib.request import build_opener, HTTPBasicAuthHandler, HTTPCookieProcessor
try:
  import xml.etree.cElementTree as ElementTree
except ImportError:
  import xml.etree.ElementTree as ElementTree

from icontract import require
from typeguard import typechecked

from cs.app.flag import PolledFlags
from cs.cmdutils import BaseCommand
from cs.context import stackattrs
from cs.deco import promote
from cs.debug import ifdebug
from cs.env import envsub
from cs.excutils import logexc, LogExceptions
from cs.fileutils import mkdirn
from cs.later import Later, RetryError
from cs.lex import (
    cutprefix, cutsuffix, get_dotted_identifier, get_identifier, is_identifier,
    get_other_chars, get_qstr
)
import cs.logutils
from cs.logutils import (debug, error, warning, exception, trace, D)
from cs.mappings import MappingChain, SeenSet
from cs.obj import copy as obj_copy
import cs.pfx
from cs.pfx import Pfx
from cs.pipeline import pipeline, StageType
from cs.py.func import funcname
from cs.py.modules import import_module_name
from cs.queues import NullQueue
from cs.resources import MultiOpenMixin, RunStateMixin, uses_runstate
from cs.seq import seq
from cs.threads import locked
from cs.urlutils import URL, NetrcHTTPPasswordMgr
from cs.x import X

# parallelism of jobs
DEFAULT_JOBS = 4

# default flag status probe
DEFAULT_FLAGS_CONJUNCTION = '!PILFER_DISABLE'

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
        errors.append("no actions after %r" % (action,))
      else:
        tail_argv = list(argv)
        name = "%s:[%s]" % (action, ','.join(argv))
        pipespec = PipeSpec(name, argv)

        def per(P):
          with P:
            pipe = pipeline(
                pipespec.actions, inputs=(P,), name="%s(%s)" % (name, P)
            )
            with P.later.release():
              for P2 in pipe.outQ:
                yield P2

        pipe_funcs.append((StageType.ONE_TO_MANY, per))
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

@promote
def url_xml_find(U: URL, match):
  for found in url_io(U.xml_find_all, (), match):
    yield ElementTree.tostring(found, encoding='utf-8')

class Pilfer(MultiOpenMixin, RunStateMixin):
  ''' State for the pilfer app.

      Notable attributes include:
      * `flush_print`: flush output after print(), default `False`.
      * `user_agent`: specify user-agent string, default `None`.
      * `user_vars`: mapping of user variables for arbitrary use.
  '''

  @uses_later
  def __init__(self, item=None, later: Later = None):
    self._name = 'Pilfer-%d' % (seq(),)
    self.user_vars = {'_': item, 'save_dir': '.'}
    self.flush_print = False
    self.do_trace = False
    self.flags = PolledFlags()
    self._print_to = None
    self._print_lock = Lock()
    self.user_agent = None
    ##self._lock = Lock()
    self._lock = RLock()
    self.rcs = []  # chain of PilferRC libraries
    self.seensets = {}
    self.diversions_map = {}  # global mapping of names to divert: pipelines
    self.opener = build_opener()
    self.opener.add_handler(HTTPBasicAuthHandler(NetrcHTTPPasswordMgr()))
    self.opener.add_handler(HTTPCookieProcessor())
    self.later = later

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
    ''' `self._` as a `URL` object.
    '''
    return URL.promote(self._)

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
    ''' Test if the named `url` has been seen.
        The default seenset is named `'_'`.
    '''
    return url in self.seenset(seenset)

  def see(self, url, seenset='_'):
    ''' Mark a `url` as seen.
        The default seenset is named `'_'`.
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
    with Pfx(spec):
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

@promote
def url_query(U: URL, *a):
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

@uses_runstate
def url_io_iter(it, *, runstate):
  ''' Generator that calls `it.next()` until `StopIteration`, yielding
      its values.
      If the call raises URLError or HTTPError, report the error
      instead of aborting.
  '''
  while True:
    runstate.raiseif("url_io_iter(it=%s): cancelled", it)
    try:
      item = next(it)
    except StopIteration:
      break
    except (URLError, HTTPError) as e:
      warning("%s", e)
    else:
      yield item

@promote
@typechecked
def url_hrefs(U: URL) -> Iterable[URL]:
  ''' Yield the HREFs referenced by a URL.
      Conceals URLError, HTTPError.
  '''
  return url_io_iter(U.hrefs(absolute=True))

@promote
@typechecked
def url_srcs(U: URL) -> Iterable[URL]:
  ''' Yield the SRCs referenced by a URL.
      Conceals URLError, HTTPError.
  '''
  return url_io_iter(U.srcs(absolute=True))

# actions that work on the whole list of in-play URLs
# these return Pilfers
many_to_many = {
    'sort':
    lambda Ps, key=lambda P: P._, reverse=False:
    sorted(Ps, key=key, reverse=reverse),
    'last':
    lambda Ps: Ps[-1:],
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
    lambda P: notNone(P._.referer, "%r.referer" %
                      (P._,)) and P._.domain == P._.referer.domain,
    'same_hostname':
    lambda P: notNone(P._.referer, "%r.referer" %
                      (P._,)) and P._.hostname == P._.referer.hostname,
    'same_scheme':
    lambda P: notNone(P._.referer, "%r.referer" %
                      (P._,)) and P._.scheme == P._.referer.scheme,
    'select_re':
    lambda P, regexp: regexp.search(P._),
}

# regular expressions used when parsing actions
re_GROK = re.compile(r'([a-z]\w*(\.[a-z]\w*)*)\.([_a-z]\w*)', re.I)

def Action(action_text, do_trace):
  ''' Wrapper for parse_action: parse an action text and promote (sig, function) into an BaseAction.
  '''
  parsed = parse_action(action_text, do_trace)
  try:
    sig, function = parsed
  except TypeError:
    action = parsed
  else:
    action = ActionFunction(action_text, sig, function)
  return action

def pilferify11(func):
  ''' Decorator for 1-to-1 Pilfer=>nonPilfer functions to return a Pilfer.
  '''

  def pf(P, *a, **kw):
    return P.copy_with_vars(_=func(P, *a, **kw))

  pf.__name__ = "@pilferify11(%s)" % funcname(func)
  return pf

def pilferify1m(func):
  ''' Decorator for 1-to-many Pilfer=>nonPilfers functions to yield Pilfers.
  '''

  @promote
  def pf(P: Pilfer, *a, **kw):
    for value in func(P, *a, **kw):
      yield P.copy_with_vars(_=value)

  pf.__name__ = "@pilferify1m(%s)" % funcname(func)
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

  pf.__name__ = "@pilferifymm(%s)" % funcname(func)
  return pf

def pilferifysel(func):
  ''' Decorator for selector Pilfer=>bool functions to yield Pilfers.
  '''

  def pf(Ps, *a, **kw):
    for P in Ps:
      if func(P, *a, **kw):
        yield P

  pf.__name__ = "@pilferifysel(%s)" % funcname(func)
  return pf

def retriable(func):
  ''' A decorator for a function to probe the `Pilfer` flags
      and raise `RetryError` if unsatisfied.
  '''

  def retry_func(P, *a, **kw):
    ''' Call `func` after testing `P.test_flags()`.
    '''
    if not P.test_flags():
      raise RetryError('flag conjunction fails: %s' % (' '.join(P.flagnames)))
    return func(P, *a, **kw)

  retry_func.__name__ = 'retriable(%s)' % (funcname(func),)
  return retry_func

class BaseAction:
  ''' The base class for all actions.

      Each instance has the following attributes:
      * `srctext`: the text defining the action
      * `sig`: the action's function signature
  '''

  @typechecked
  def __init__(self, srctext: str, sig: StageType):
    self.srctext = srctext
    self.sig = sig

  def __str__(self):
    s = "%s(%s:%r" % (self.__class__.__name__, self.sig, self.srctext)
    if self.args:
      s += ",args=%r" % (self.args,)
    if self.kwargs:
      s += ",kwargs=%r" % (self.kwargs,)
    s += ")"
    return s

  def __call__(self, P):
    ''' Calling an BaseAction with an item creates a functor and passes the item to it.
    '''
    return self.functor()(P, *self.args, **self.kwargs)

class ActionFunction(BaseAction):

  def __init__(self, action0, sig, func):
    super().__init__(action0, sig)
    # stash a retriable version of the function
    func0 = func
    self.func = retriable(func)
    self.func.__name__ = "%s(%r,func=%s)" % (
        type(self).__name__, action0, funcname(func0)
    )

  def functor(self, L):
    return self.func

class ActionPipeTo(BaseAction):

  def __init__(self, action0, pipespec):
    super().__init__(action0, StageType.PIPELINE)
    self.pipespec = pipespec

  class _OnDemandPipeline(MultiOpenMixin):

    def __init__(self, pipespec, L):
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

class ActionShellFilter(BaseAction):

  def __init__(self, action0, shcmd, args, kwargs):
    super().__init__(action0, StageType.PIPELINE, args, kwargs)
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

        Parameters:
        * `no_flush`: do not flush input lines for the subprocess,
          block buffer instead
        * `discard`: discard .put items, close subprocess stdin
          immediately after startup
    '''
    self.shcmd = shcmd
    self.shproc = None
    self.outQ = outQ
    outQ.open()

  def _startproc(self, shcmd):
    self.shproc = Popen(shcmd, shell=True, stdin=PIPE, stdout=PIPE)

    def copy_out(fp, outQ):
      ''' Copy lines from the shell output, put new `Pilfer`s onto the `outQ`.
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

class ActionShellCommand(BaseAction):

  def __init__(self, action0, shcmd, args, kwargs):
    super().__init__(action0, StageType.PIPELINE, args, kwargs)
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

  @typechecked
  def function(P) -> Iterable[str]:
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
            # TODO: use cs.psutils.run
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

  return function, StageType.ONE_TO_MANY

def action_pipecmd(shcmd):
  ''' Return (function, func_sig) for pipeline through a shell command.
  '''
  shcmd = shcmd.strip()

  @typechecked
  def function(items) -> Iterable[str]:
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

  return function, StageType.MANY_TO_MANY

class PipeSpec(namedtuple('PipeSpec', 'name argv')):
  ''' A pipeline specification: a name and list of actions.
  '''

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
