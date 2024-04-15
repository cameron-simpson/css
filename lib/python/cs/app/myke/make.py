#!/usr/bin/env python3

''' Make make processes.
'''

from contextlib import contextmanager
from dataclasses import dataclass, field
import errno
from itertools import zip_longest
import logging
import os
from os.path import (
    basename,
    dirname,
    exists as existspath,
    isabs as isabspath,
    join as joinpath,
    realpath,
    splitext,
)
from subprocess import Popen
import sys
from threading import RLock
import time
from types import SimpleNamespace as NS
from typing import Any, List, Optional

from typeguard import typechecked

from cs.cmdutils import BaseCommandOptions
from cs.deco import default_params, promote, Promotable
from cs.excutils import logexc
from cs.fsm import FSM
from cs.inttypes import Flags
from cs.later import Later
from cs.lex import get_identifier, get_white
from cs.logutils import debug, info, error, exception, D
import cs.pfx
from cs.pfx import pfx, Pfx, pfx_method
from cs.queues import MultiOpenMixin
from cs.result import Result
from cs.threads import (
    HasThreadState,
    Lock,
    NRLock,
    ThreadState,
    locked,
    locked_property,
)

from . import DEFAULT_MAKE_COMMAND
from .parse import (
    SPECIAL_MACROS,
    FileContext,
    Macro,
    MacroExpression,
    ParseError,
    scan_makefile,
)

SHELL = '/bin/sh'

# Later priority values
# actions come first, to keep the queue narrower
PRI_ACTION = 0
PRI_MAKE = 1
PRI_PREREQ = 2

class Action(NS):
  ''' A make action.
      This corresponds to a line in the Mykefile and may be used
      by multiple `Target`s.
  '''

  def __init__(self, context, variant, line, silent=False):
    self.context = context
    self.variant = variant
    self.line = line
    self.mexpr = MacroExpression.from_text(context, line)
    self.silent = silent
    self._lock = NRLock()

  def __str__(self):
    return f'{self.__class__.__name__}({self.variant}:{self.context.filename}:{self.context.lineno})'

  __repr__ = __str__

  @property
  def prline(self):
    ''' Printable form of this Action.
    '''
    return self.line.rstrip().replace('\n', '\\n')

  @uses_Maker
  @typechecked
  def act_later(self, target: Target, *, maker: Maker) -> Result:
    ''' Request that this `Action` occur on behalf of the `target`.
        Return a `Result` which returns the success or failure
        of the action.
    '''
    R = Result(name="%s.action(%s)" % (target, self))
    maker.defer("%s:act[%s]" % (
        self,
        target,
    ), self._act, R, target)
    return R

  @uses_Maker
  @typechecked
  def _act(self, R: Result, target: Target, *, maker: Maker):
    ''' Perform this Action on behalf of the Target `target`.
        Arrange to put the result onto `R`.
    '''
    with Pfx("%s.act(target=%s)", self, target):
      try:
        debug("start act...")
        v = self.variant

        if v == 'shell':
          debug("shell command")
          shcmd = self.mexpr(self.context, target.namespaces)
          if maker.no_action or not self.silent:
            print(shcmd)
          if maker.no_action:
            mdebug("OK (maker.no_action)")
            R.put(True)
            return
          R.put(self._shcmd(target, shcmd))
          return

        if v == 'make':
          subtargets = self.mexpr(self.context, target.namespaces).split()
          mdebug("targets = %s", subtargets)
          subTs = [maker[subtarget] for subtarget in subtargets]

          def _act_after_make():
            # analyse success of targets, update R
            ok = True
            for T in subTs:
              if T.result:
                mdebug('submake "%s" OK', T)
              else:
                ok = False
                mdebug('submake "%s" FAIL"', T)
            R.put(ok)

          for T in subTs:
            mdebug('submake "%s"', T)
            T.require()
          maker.after(subTs, _act_after_make)
          return

        raise NotImplementedError("unsupported variant: %s" % (self.variant,))
      except Exception as e:
        error("action failed: %s", e)
        R.put(False)

  def _shcmd(self, target, shcmd):
    with Pfx("%s.act: shcmd=%r", self, shcmd):
      argv = (target.shell, '-c', shcmd)
      mdebug("Popen(%s,..)", argv)
      P = Popen(argv, close_fds=True)
      retcode = P.wait()
      mdebug("retcode = %d", retcode)
      return retcode == 0

MakeDebugFlags = Flags('debug', 'flags', 'make', 'parse')

@dataclass
class Maker(BaseCommandOptions, MultiOpenMixin, HasThreadState):
  ''' Main class representing a set of dependencies to make.
  '''

  perthread_state = ThreadState()

  DEFAULT_PARALLELISM = 1

  def _make_debug_flags():
    debug = MakeDebugFlags()
    debug.debug = False  # logging.DEBUG noise
    debug.flags = False  # watch debug flag settings
    debug.make = False  # watch make decisions
    debug.parse = False  # watch Makefile parsing
    return debug

  name: str = field(default_factory=lambda: cs.pfx.cmd)
  makecmd: str = DEFAULT_MAKE_COMMAND
  parallel: int = DEFAULT_PARALLELISM
  debug: Any = field(default_factory=_make_debug_flags)
  fail_fast: bool = True
  no_action: bool = False
  default_target: Optional["Target"] = None
  _makefiles: list = field(default_factory=list)
  appendfiles: list = field(default_factory=list)
  macros: dict = field(default_factory=dict)
  targets: "TargetMap" = field(default_factory=lambda: TargetMap())
  rules: dict = field(default_factory=dict)
  precious: set = field(default_factory=set)
  active: set = field(default_factory=set)
  # there's no Lock type I can name
  activity_lock: Any = field(default_factory=Lock)
  basic_namespaces: list = field(default_factory=list)
  cmd_ns: dict = field(default_factory=dict)

  def __str__(self):
    return (
        '%s:%s(parallel=%s,fail_fast=%s,no_action=%s,default_target=%s)' % (
            self.__class__.__name__, self.name, self.parallel, self.fail_fast,
            self.no_action, self.default_target.name
        )
    )

  def __enter_exit__(self):
    ''' Run both the inherited context managers.
    '''
    for _ in zip_longest(
        MultiOpenMixin.__enter_exit__(self),
        HasThreadState.__enter_exit__(self),
    ):
      yield

  @contextmanager
  def startup_shutdown(self):
    ''' Set up the `Later` work queue.
    '''
    self._makeQ = Later(self.parallel, self.name)
    try:
      with self._makeQ:
        yield
    finally:
      self._makeQ.wait()

  def report(self, f=None):
    ''' Report the make queue status.
    '''
    D("REPORT...")
    if f is None:
      f = sys.stderr
    f.write(str(self))
    f.write(': ')
    f.write(repr(self._makeQ))
    f.write('\n')
    D("REPORTED")

  def _ticker(self):
    while True:
      time.sleep(5)
      self.report()

  @property
  def namespaces(self):
    ''' The namespaces for this Maker: the built namespaces plus the special macros.
    '''
    return self.basic_namespaces + [
        dict(MAKE=self.makecmd.replace('$', '$$')), SPECIAL_MACROS
    ]

  def insert_namespace(self, ns):
    ''' Insert a macro namespace in front of the existing namespaces.
    '''
    self.basic_namespaces.insert(0, ns)

  @property
  def makefiles(self):
    ''' The list of makefiles to consult, a tuple.
        It is not possible to add more makefiles after accessing this property.
    '''
    _makefiles = self._makefiles
    if not _makefiles:
      _makefiles = []
      makerc_envvar = (splitext(basename(cs.pfx.cmd))[0] + 'rc').upper()
      makerc = os.environ.get(makerc_envvar)
      if makerc and existspath(makerc):
        _makefiles.append(makerc)
      makefile = basename(cs.pfx.cmd).title() + 'file'
      _makefiles.append(makefile)
      self._makefiles = _makefiles
    if type(_makefiles) is not tuple:
      self._makefiles = _makefiles = tuple(_makefiles)
    return _makefiles

  def add_appendfile(self, filename):
    ''' Add another Mykefile as from the :append directive, to be
        sourced after the main sequence of Mykefiles.
    '''
    self.appendfiles.append(filename)

  def debug_make(self, msg, *a, **kw):
    ''' Issue an INFO log message if the "make" debugging flag is set.
    '''
    if self.debug.make:
      info(msg, *a, **kw)

  def debug_parse(self, msg, *a, **kw):
    ''' Issue an INFO log message if the "parse" debugging flag is set.
    '''
    if self.debug.parse:
      info(msg, *a, **kw)

  def target_active(self, target):
    ''' Add this target to the set of "in progress" targets.
    '''
    self.debug_make('note target "%s" as active', target.name)
    with self.activity_lock:
      self.active.add(target)

  def target_inactive(self, target):
    ''' Remove this target from the set of "in progress" targets.
    '''
    self.debug_make(
        "note target %r as inactive (%s)", target.name, target.fsm_state
    )
    with self.activity_lock:
      self.active.remove(target)

  def cancel_all(self):
    ''' Cancel all "in progress" targets.
    '''
    self.debug_make("cancel_all!")
    with self.activity_lock:
      Ts = list(self.active)
    for T in Ts:
      T.cancel()

  def defer(self, func, *a, **kw):
    ''' Submt a function that will run from the queue later.
        Return the LateFunction.
    '''
    self.debug_make("defer %s(*%r, **%r)" % (func, a, kw))
    MLF = self._makeQ.defer(func, *a, **kw)
    return MLF

  def after(self, LFs, func, *a, **kw):
    ''' Submit a function to be run after the supplied LateFunctions `LFs`,
        return a Result instance for collection.
    '''
    if not isinstance(LFs, list):
      LFs = list(LFs)
    self.debug_make("after %s call %s(*%r, **%r)" % (LFs, func, a, kw))
    R = Result("Maker.after(%s):%s" % (",".join(str(LF) for LF in LFs), func))
    self._makeQ.after(LFs, R, func, *a, **kw)
    return R

  def make(self, targets):
    ''' Make `targets` and yield them as they complete.
    '''
    with Pfx("%s.make", type(self).__name__):
      ok = True
      for target in targets:
        with Pfx(target):
          if isinstance(target, str):
            T = self[target]
          else:
            T = target
          T.require()
          if T.get():
            self.debug_make("MAKE %s: OK", T)
          else:
            self.debug_make("MAKE %s: FAILED", T)
            ok = False
            if self.fail_fast:
              self.debug_make("ABORT MAKE")
              break
      self.debug_make("%r: %s", targets, ok)
      return ok

  def __getitem__(self, name):
    ''' Return the specified Target.
    '''
    return self.targets[name]

  @pfx
  def setDebug(self, flag, value):
    ''' Set or clear the named debug option.
    '''
    with Pfx("setDebug(%r, %r)", flag, value):
      if not flag.isalpha() or not hasattr(self.debug, flag):
        raise AttributeError(
            "invalid debug flag, know: %s" %
            (",".join(sorted([F for F in dir(self.debug) if F.isalpha()])),)
        )
      if self.debug.flags:
        info("debug.%s = %s", flag, value)
      setattr(self.debug, flag, value)
      if flag == 'debug':
        # tweak global logging level also
        logger = logging.getLogger()
        log_level = logger.getEffectiveLevel()
        if value:
          if log_level > logging.DEBUG:
            logger.setLevel(logging.DEBUG)
          else:
            if log_level < logging.INFO:
              logger.setLevel(logging.INFO)

  def load_makefiles(self, makefiles, parent_context=None):
    ''' Load the specified Makefiles; return success.

        Each top level Makefile named gets its own namespace prepended
        to the namespaces list. In this way later top level Makefiles'
        definitions override ealier ones while still detecting conflicts
        within a particular Makefile.
        Also, the default_target property is set to the first
        encountered target if not yet set.
    '''
    from .parse import scan_makefile
    ok = True
    for makefile in makefiles:
      self.debug_parse("load makefile: %s", makefile)
      first_target = None
      ns = {}
      self.insert_namespace(ns)
      for parsed_object in scan_makefile(self, makefile, parent_context):
        with Pfx(parsed_object.context):
          if isinstance(parsed_object, Exception):
            error("exception: %s", parsed_object)
            ok = False
          elif isinstance(parsed_object, Macro):
            self.debug_parse("add macro %s", parsed_object)
            ns[parsed_object.name] = parsed_object
          elif isinstance(parsed_object, Target):
            # record this Target in the Maker
            T = parsed_object
            self.debug_parse("add target %s", T)
            if '%' in T.name:
              # record this Target as a rule
              self.rules[T.name] = T
            else:
              self.targets[T.name] = T
              if first_target is None:
                first_target = T
          else:
            raise RuntimeError(
                f'unsupported parse item received: {r(parsed_object)}'
            )
      if first_target is not None:
        self.default_target = first_target
    return ok



uses_Maker = default_params(maker=Maker.default)

@uses_Maker
def mdebug(msg: str, *a, maker: Maker):
  return maker.make_debug(msg, *a)

class TargetMap(dict):
  ''' A thread safe mapping interface to the known `Target`s.
      Makes empty targets as needed.
  '''

  def __init__(self):
    ''' Initialise the `TargetMap`.
    '''
    self._lock = RLock()  # __setitem__ can call __getitem__

  def __getitem__(self, name: str) -> "Target":
    ''' Return the Target for `name`.
        Raises KeyError if the Target is unknown and not inferrable.
    '''
    with self._lock:
      return super().__getitem__(name)

  def __missing__(self, name) -> "Target":
    ''' A missing `Target` gets made with no context or other stuff.
    '''
    return Target(name, None, (), (), [])

  def __setitem__(self, name: str, target: "Target"):
    ''' Record new target in map.
        Check that the name matches.
        Reject duplicate names.
    '''
    if name != target.name:
      raise ValueError(
          f'tried to record Target as {name!r}, but target.name = {target.name!r}'
      )
    with self._lock:
      if name in self:
        raise KeyError('Target for {name!r} already known: {self[name]}')
      super().__setitem__(name, target)

class Target(FSM, Promotable):
  ''' A make target.
  '''

  FSM_TRANSITIONS = {
      'UNCHECKED': {
          'require': 'MAKE_PREREQS',
      },
      'MAKE_PREREQS': {
          'failed': 'FAILED',
          'updated': 'OUT_OF_DATE',
          'unchanged': 'CHECK_EXISTS',
      },
      'CHECK_EXISTS': {
          'exists': 'DONE',
          'missing': 'OUT_OF_DATE',
      },
      'OUT_OF_DATE': {
          'cancel': 'CANCELLED',
          'completed': 'DONE',
          'failed': 'FAILED',
      },
      # build cancelled
      'CANCELLED': {},
      # built anew
      'UPDATED': {},
      # exists and prereqs not updated
      'OK': {},
      # build failed
      'FAILED': {},
  }

  @typechecked
  def __init__(
      self,
      name: str,
      context: FileContext,
      prereqs,
      postprereqs,
      actions: List["Action"],
  ):
    ''' Initialise a new target.

        Parameters:
        - `name`: the name of the target.
        - `maker`: the Maker with which this Target is associated.
        - `context`: the file context, for citations.
        - `prereqs`: macro expression to produce prereqs.
        - `postprereqs`: macro expression to produce post-inference prereqs.
        - `actions`: a list of actions to build this `Target`

        The same actions list is shared amongst all `Target`s defined
        by a common clause in the Mykefile, and extends during the
        Mykefile parse _after_ defining those `Target`s. So we do not
        modify it the class; instead we extend `.pending_actions`
        when `.require()` is called the first time, just as we do for a
        `:make` directive.
    '''
    self.name = name
    self.context = context
    self.shell = SHELL
    self._prereqs = prereqs
    self._postprereqs = postprereqs
    self.actions = actions
    self.failed = False
    # build state:
    #
    # Out Of Date:
    #  This target does not exist in the filesystem, or one of its
    #  dependents is newly made or exists and is newer.
    #  After successfully building each prerequisite, if the prereq
    #  was new or the prereq exists and is newer than this Target,
    #  then this target is marker out of date.
    #  When all prereqs have been successfully build, if this Target
    #  is out of date then it is marked as new and any actions queued.
    #

  def __str__(self):
    return f'{self.name}[{self.fsm_state}]'

  @classmethod
  def from_str(cls, name, *, maker: Maker) -> "Target":
    ''' Return the `Target` named `name`.
    '''
    return maker[name]

  @classmethod
  @typechecked
  def from_mexpr(
      cls,
      mexpr: MacroExpression,
      context: FileContext,
      *,
      prereqs: MacroExpression,
      postprereqs: MacroExpression,
      actions: List[Action],
      maker: Maker,
  ) -> Iterable["Target"]:
    ''' Generator yielding `Target`s for each name obtained from `mexpr`.
    '''
    actions = tuple(actions)
    for target in mexpr(context, maker.namespaces).split():
      yield Target(
          target,
          context,
          prereqs=prereqs,
          postprereqs=postprereqs,
          actions=actions,
      )

  ##########################
  # mapping/dict/set methods

  def __hash__(self):
    return hash(self.name)

  def __eq__(self, other):
    if self.name == other.name:
      if self is not other:
        raise RuntimeError(
            f'distinct Targets have the same name {self.name!r}'
        )
      return True
    return False

  @locked
  def succeed(self):
    ''' Mark target as successfully made.
    '''
    mdebug("OK")
    if self.ready:
      if not self.result:
        raise RuntimeError(
            "%s.succeed: already completed FAILED" % (self.name,)
        )
      return
    self.failed = False
    self.result = True

  @locked
  def fail(self, msg=None):
    ''' Mark Target as failed.
    '''
    if msg is None:
      msg = "FAILED"
    mdebug(msg)
    if self.ready:
      if self.result:
        raise RuntimeError("%s.fail: already completed OK" % (self.name,))
      return
    self.failed = True
    self.result = False

  @property
  @uses_Maker
  def namespaces(self, *, maker: Maker):
    ''' The namespaces for this `Target`: the special per-`Target` macros,
        the `Maker`'s namespaces, the `Maker`'s macros and the special macros.
    '''
    return (
        [
            {
                '@': lambda c, ns: self.name,
                '/': lambda c, ns: ' '.join(self.prereqs),
                '?': lambda c, ns: ' '.join(self.new_prereqs),
                # TODO: $< et al
            },
        ] + maker.namespaces + [
            maker.macros,
            SPECIAL_MACROS,
        ]
    )

  @property
  def prereqs(self):
    ''' Return the prerequisite target names.
    '''
    prereqs = self._prereqs
    if isinstance(prereqs, MacroExpression):
      prereqs_mexpr = prereqs
      self._prereqs = prereqs_mexpr(self.context, self.namespaces).split()
    return self._prereqs

  @property
  @uses_Maker
  def new_prereqs(self, *, maker: Maker):
    ''' Return the new prerequisite target names.
    '''
    if self.was_missing:
      # target missing: use all prereqs
      return self.prereqs
    Ps = []
    for Pname in self.prereqs:
      P = maker[Pname]
      if not P.ready:
        raise RuntimeError("%s: prereq %r not ready", self.name, Pname)
      if self.older_than(P):
        Ps.append(Pname)
    return Ps

  @locked_property
  def mtime(self):
    ''' Modification time of this Target, `None` if missing or inaccessable.
    '''
    try:
      s = os.stat(self.name)
    except OSError:
      return None
    return s.st_mtime

  def older_than(self, other: "Target"):
    ''' Test whether we are older than another Target.
    '''
    if self.was_missing:
      return True
    if isinstance(other, str):
      other = self.maker[other]
    if not other.ready:
      raise RuntimeError(
          "Target %r not ready, accessed from Target %r", other, self
      )
    if other.out_of_date:
      return True
    m = other.mtime
    if m is None:
      return False
    return self.mtime < m

  def cancel(self):
    ''' Cancel this `Target`.
        Actions will cease as soon as decorum allows.
    '''
    Result.cancel(self)
    mdebug("%s: CANCEL", self)

  @pfx_method
  @uses_Maker
  def require(self, *, maker: Maker):
    ''' Require this Target to be made.
    '''
    with self._lock:
      if self.is_pending:
        # commence make of this Target
        maker.target_active(self)
        self.notify(maker.target_inactive)
        self.dispatch()
        self.was_missing = self.mtime is None
        self.pending_actions = list(self.actions)
        Ts = []
        for Pname in self.prereqs:
          T = maker[Pname]
          Ts.append(T)
          T.require()

          # fire fail action immediately
          def f(T):
            if T.result:
              pass
            else:
              self.fail("REQUIRE(%s): FAILED by prereq %s" % (self, T))

          T.notify(f)
        # queue the first unit of work
        if Ts:
          maker.after(Ts, self._make_after_prereqs, Ts)
        else:
          self._make_after_prereqs(Ts)

  @logexc
  def _make_after_prereqs(self, Ts):
    ''' Invoked after the initial prerequisites have been run.
        Compute out_of_date etc, then run _make_next.
    '''
    with Pfx("%s: after prereqs (Ts=%s)", self.name,
             ",".join(str(T) for T in Ts)):
      self.out_of_date = False
      # it is possible we may have been marked as failed already
      # because that has immediate effect
      if self.ready:
        return
      for T in Ts:
        if not T.ready:
          raise RuntimeError("not ready")
        self._apply_prereq(T)
      if self.failed:
        return
      if self.was_missing or self.out_of_date:
        # proceed to normal make process
        self.Rs = []
        return self._make_next()
      # prereqs ok and up to date: make complete
      self.succeed()

  def _apply_prereq(self, T):
    ''' Apply the consequences of the completed prereq T.
    '''
    with Pfx("%s._apply_prereqs(T=%s)", self, T):
      if not T.ready:
        raise RuntimeError("not ready")
      if not T.result:
        mdebug("FAILED")
        self.fail()
      else:
        mdebug("MADE OK")
        if T.out_of_date:
          mdebug("out of date because T was out of date")
          self.out_of_date = True
        elif self.older_than(T):
          mdebug("out of date because T is newer")
          self.out_of_date = True

  @logexc
  @uses_Maker
  def _make_next(self, *, maker: Maker):
    ''' The inner/recursive/deferred function from _make; only called if out of date.
        Perform the next unit of work in making this Target.
        If we complete without blocking, put True or False onto self.made.
        Otherwise queue a background function to block and resume.
    '''
    with Pfx("_make_next(%r)", self.name):
      if not self.was_missing and not self.out_of_date:
        raise RuntimeError("not missing or out of date!")
      # evaluate the result of Actions or Targets we have just waited for
      for R in self.Rs:
        with Pfx("checking %s", R):
          if isinstance(R, Target):
            self._apply_prereq(R)
          elif R.result:
            pass
          else:
            self.fail()
          if self.ready:
            break
      if self.failed:
        # failure, cease make
        return

      Rs = self.Rs = []
      actions = self.pending_actions
      if actions:
        A = actions.pop(0)
        mdebug("queue action: %s", A)
        Rs.append(A.act_later(self))
      else:
        mdebug("no actions remaining")

      if Rs:
        mdebug(
            "tasks still to do, requeuing: Rs=%s",
            ",".join(str(_) for _ in Rs)
        )
        maker.after(Rs, self._make_next)
      else:
        # all done, record success
        self.succeed()
