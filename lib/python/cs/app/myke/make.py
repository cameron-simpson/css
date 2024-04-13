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
from cs.excutils import logexc
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
    ''' Synchronous call to make targets in series.
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

  def loadMakefiles(self, makefiles, parent_context=None):
    ''' Load the specified Makefiles; return success.

        Each top level Makefile named gets its own namespace prepended
        to the namespaces list. In this way later top level Makefiles'
        definitions override ealier ones while still detecting conflicts
        within a particular Makefile.
        Also, the default_target property is set to the first
        encountered target if not yet set.
    '''
    ok = True
    for makefile in makefiles:
      self.debug_parse("load makefile: %s", makefile)
      first_target = None
      ns = {}
      self.insert_namespace(ns)
      for parsed_object in self.parse(makefile, parent_context):
        with Pfx(parsed_object.context):
          if isinstance(parsed_object, Exception):
            error("exception: %s", parsed_object)
            ok = False
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
          elif isinstance(parsed_object, Macro):
            self.debug_parse("add macro %s", parsed_object)
            ns[parsed_object.name] = parsed_object
          else:
            raise ValueError(
                f"unsupported parse item received: {type(parsed_object)}{parsed_object!r}"
            )
      if first_target is not None:
        self.default_target = first_target
    return ok

  def parse(self, f, parent_context=None, missing_ok=False):
    ''' Read a Mykefile and yield Macros and Targets.
    '''
    from .make import Target, Action
    action_list = None  # not in a target
    for context, line in scan_makefile(
        self,
        f,
        parent_context=parent_context,
        missing_ok=missing_ok,
    ):
      with Pfx(context):
        try:
          if line.startswith(':'):
            # top level directive
            _, doffset = get_white(line, 1)
            word, offset = get_identifier(line, doffset)
            if not word:
              raise ParseError(context, doffset, "missing directive name")
            _, offset = get_white(line, offset)
            with Pfx(word):
              if word == 'append':
                if offset == len(line):
                  raise ParseError(context, offset, "nothing to append")
                mexpr, offset = MacroExpression.parse(context, line, offset)
                assert offset == len(line)
                for include_file in mexpr(context, self.namespaces).split():
                  if include_file:
                    if not isabspath(include_file):
                      include_file = joinpath(
                          realpath(dirname(f.name)), include_file
                      )
                    self.add_appendfile(include_file)
                continue
              if word == 'import':
                if offset == len(line):
                  raise ParseError(context, offset, "nothing to import")
                ok = True
                missing_envvars = []
                for envvar in line[offset:].split():
                  if envvar:
                    envvalue = os.environ.get(envvar)
                    if envvalue is None:
                      error("no $%s" % (envvar,))
                      ok = False
                      missing_envvars.append(envvar)
                    else:
                      yield Macro(
                          context, envvar, (), envvalue.replace('$', '$$')
                      )
                if not ok:
                  raise ValueError(
                      "missing environment variables: %s" % (missing_envvars,)
                  )
                continue
              if word == 'precious':
                if offset == len(line):
                  raise ParseError(
                      context, offset, "nothing to mark as precious"
                  )
                mexpr, offset = MacroExpression.parse(context, line, offset)
                self.precious.update(
                    word for word in mexpr(context, self.namespaces).split()
                    if word
                )
                continue
              raise ParseError(context, doffset, "unrecognised directive")

          if action_list is not None:
            # currently collating a Target
            if not line[0].isspace():
              # new target or unindented assignment etc - fall through
              # action_list is already attached to targets,
              # so simply reset it to None to keep state
              action_list = None
            else:
              # action line
              _, offset = get_white(line)
              if offset >= len(line) or line[offset] != ':':
                # ordinary shell action
                action_silent = False
                if offset < len(line) and line[offset] == '@':
                  action_silent = True
                  offset += 1
                A = Action(
                    context, 'shell', line[offset:], silent=action_silent
                )
                self.debug_parse("add action: %s", A)
                action_list.append(A)
                continue
              # in-target directive like ":make"
              _, offset = get_white(line, offset + 1)
              directive, offset = get_identifier(line, offset)
              if not directive:
                raise ParseError(
                    context, offset,
                    "missing in-target directive after leading colon"
                )
              A = Action(context, directive, line[offset:].lstrip())
              self.debug_parse("add action: %s", A)
              action_list.append(A)
              continue

          try:
            macro = Macro.from_assignment(context, line)
          except ValueError:
            pass
          else:
            yield macro
            continue

          # presumably a target definition
          # gather up the target as a macro expression
          target_mexpr, offset = MacroExpression.parse(context, stopchars=':')
          if not context.text.startswith(':', offset):
            raise ParseError(context, offset, "no colon in target definition")
          prereqs_mexpr, offset = MacroExpression.parse(
              context, offset=offset + 1, stopchars=':'
          )
          if offset < len(context.text) and context.text[offset] == ':':
            postprereqs_mexpr, offset = MacroExpression.parse(
                context, offset=offset + 1
            )
          else:
            postprereqs_mexpr = []

          action_list = []
          for target in target_mexpr(context, self.namespaces).split():
            yield Target(
                self,
                target,
                context,
                prereqs=prereqs_mexpr,
                postprereqs=postprereqs_mexpr,
                actions=action_list
            )
          continue

          raise ParseError(context, 0, 'unparsed line')
        except ParseError as e:
          exception("%s", e)

    self.debug_parse("finish parse")

class TargetMap(NS):
  ''' A mapping interface to the known targets.
      Makes targets as needed if inferrable.
      Raise KeyError for missing Targets which are not inferrable.
uses_Maker = default_params(maker=Maker.default)

@uses_Maker
def mdebug(msg: str, *a, maker: Maker):
  return maker.make_debug(msg, *a)

  '''

  def __init__(self):
    ''' Initialise the `TargetMap`.
    '''
    self._maker = None
    self.targets = {}
    self._lock = RLock()

  @property
  def maker(self):
    ''' The `Maker` to use to make `Target`s.
    '''
    return self._maker or Maker.default()

  def __getitem__(self, name):
    ''' Return the Target for `name`.
        Raises KeyError if the Target is unknown and not inferrable.
    '''
    targets = self.targets
    if name not in targets:
      with self._lock:
        if name not in targets:
          T = self._newTarget(self.maker, name, context=None)
          if existspath(name):
            self.maker.debug_make("%r: exists, no rules - consider made", name)
            T.out_of_date = False
            T.succeed()
          else:
            error("%r: does not exist, no rules (and nothing inferred)", name)
            T.fail()
          targets[name] = T
    return targets[name]

  def _newTarget(
      self, maker, name, context, prereqs=(), postprereqs=(), actions=()
  ):
    ''' Construct a new Target.
    '''
    return Target(maker, name, context, prereqs, postprereqs, actions)

  def __setitem__(self, name, target):
    ''' Record new target in map.
        Check that the name matches.
        Reject duplicate names.
    '''
    if name != target.name:
      raise ValueError(
          "tried to record Target as %r, but target.name = %r" %
          (name, target.name)
      )
    with self._lock:
      if name in self.targets:
        raise ValueError(
            "redefinition of Target %r, previous definition from %s" %
            (name, self.targets[name].context)
        )
      self.targets[name] = target

class Target(Result):
  ''' A make target.
  '''

  @typechecked
  def __init__(
      self,
      maker: Maker,
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
    self.maker = maker
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

  def mdebug(self, msg, *a):
    ''' Emit a debug message.
    '''
    return self.maker.debug_make(msg, *a)

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

  def older_than(self, other):
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
    ''' Cancel this Target.
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
