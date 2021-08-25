#!/usr/bin/env python3

''' Make make processes.
'''

import errno
import getopt
import logging
import os
import os.path
from os.path import dirname, realpath
from subprocess import Popen
import sys
import time
from types import SimpleNamespace as NS
from cs.debug import RLock
from cs.excutils import logexc
from cs.inttypes import Flags
from cs.later import Later
from cs.lex import get_identifier, get_white
from cs.logutils import debug, info, error, exception, D
from cs.pfx import Pfx
from cs.py.func import prop
from cs.queues import MultiOpenMixin
from cs.result import Result, ResultState
from cs.threads import Lock, locked, locked_property
import cs.logutils
import cs.pfx
from .parse import (SPECIAL_MACROS, Macro, MacroExpression, readMakefileLines, ParseError)

SHELL = '/bin/sh'

# Later priority values
# actions come first, to keep the queue narrower
PRI_ACTION = 0
PRI_MAKE = 1
PRI_PREREQ = 2

MakeDebugFlags = Flags('debug', 'flags', 'make', 'parse')

class Maker(MultiOpenMixin):
  ''' Main class representing a set of dependencies to make.
  '''

  def __init__(self, makecmd, parallel=1, name=None):
    ''' Initialise a Maker.
        `makecmd`: used to define $(MAKE), typically sys.argv[0].
        `parallel`: the degree of parallelism of shell actions.
    '''
    if parallel < 1:
      raise ValueError(
          "expected positive integer for parallel, got: %s" % (parallel,)
      )
    if name is None:
      name = cs.pfx.cmd
    MultiOpenMixin.__init__(self)
    self.parallel = parallel
    self.name = name
    self.debug = MakeDebugFlags()
    self.debug.debug = False  # logging.DEBUG noise
    self.debug.flags = False  # watch debug flag settings
    self.debug.make = False  # watch make decisions
    self.debug.parse = False  # watch Makefile parsing
    self.fail_fast = True
    self.no_action = False
    self.default_target = None
    self._makefiles = []
    self.appendfiles = []
    self.macros = {}
    # autocreating mapping interface to Targets
    self.targets = TargetMap(self)
    self.rules = {}
    self.precious = set()
    self.active = set()
    self._active_lock = Lock()
    self._namespaces = [{'MAKE': makecmd.replace('$', '$$')}]

  def __str__(self):
    return (
        '%s:%s(parallel=%s,fail_fast=%s,no_action=%s,default_target=%s)' % (
            type(self).__name__, self.name, self.parallel, self.fail_fast,
            self.no_action, self.default_target
        )
    )

  def startup(self):
    ''' Set up the `Later` work queue.
    '''
    self._makeQ = Later(self.parallel, self.name)
    self._makeQ.open()

  def shutdown(self):
    ''' Shut down the make queue and wait for it.
    '''
    self._makeQ.close()
    self._makeQ.wait()

  def report(self, fp=None):
    ''' Report the make queue status.
    '''
    D("REPORT...")
    if fp is None:
      fp = sys.stderr
    fp.write(str(self))
    fp.write(': ')
    fp.write(repr(self._makeQ))
    fp.write('\n')
    D("REPORTED")

  def _ticker(self):
    while True:
      time.sleep(5)
      self.report()

  @prop
  def namespaces(self):
    ''' The namespaces for this Maker: the built namespaces plus the special macros.
    '''
    return self._namespaces + [SPECIAL_MACROS]

  def insert_namespace(self, ns):
    ''' Insert a macro namespace in front of the existing namespaces.
    '''
    self._namespaces.insert(0, ns)

  @prop
  def makefiles(self):
    ''' The list of makefiles to consult, a tuple.
        It is not possible to add more makefiles after accessing this property.
    '''
    _makefiles = self._makefiles
    if not _makefiles:
      _makefiles = []
      makerc_envvar = (
          os.path.splitext(os.path.basename(cs.pfx.cmd))[0] + 'rc'
      ).upper()
      makerc = os.environ.get(makerc_envvar)
      if makerc and os.path.exists(makerc):
        _makefiles.append(makerc)
      makefile = os.path.basename(cs.pfx.cmd).title() + 'file'
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
    self.debug_make("note target \"%s\" as active", target.name)
    with self._active_lock:
      self.active.add(target)

  def target_inactive(self, target):
    ''' Remove this target from the set of "in progress" targets.
    '''
    self.debug_make(
        "note target %r as inactive (%s)", target.name, target.state
    )
    with self._active_lock:
      self.active.remove(target)

  def cancel_all(self):
    ''' Cancel all "in progress" targets.
    '''
    self.debug_make("cancel_all!")
    with self._active_lock:
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
    ok = True
    with Pfx("%s.make(%s)", self, " ".join(targets)):
      for target in targets:
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

  def getopt(self, args, options=None):
    ''' Parse command line options.
        Returns (args, badopts) being remaining command line arguments
        and the error state (unparsed or invalid options encountered).
    '''
    badopts = False
    opts, args = getopt.getopt(args, 'dD:eEf:ij:kmNnpqrRsS:tuvx')
    for opt, value in opts:
      with Pfx(opt):
        if opt == '-d':
          # debug mode
          self.setDebug('make', True)
        elif opt == '-D':
          for flag in [w.strip().lower() for w in value.split(',')]:
            if len(flag) == 0:
              # silently skip empty flag items
              continue
            if flag.startswith('-'):
              value = False
              flag = flag[1:]
            else:
              value = True
            try:
              self.setDebug(flag, value)
            except AttributeError as e:
              error("bad flag %r: %s", flag, e)
              badopts = True
        elif opt == '-f':
          self._makefiles.append(value)
        elif opt == '-j':
          try:
            value = int(value)
          except ValueError as e:
            error("invalid -j value: %s", e)
            badopts = True
          else:
            if value < 1:
              error("invalid -j value: %d, must be >= 1", value)
              badopts = True
            else:
              self.parallel = int(value)
        elif opt == '-k':
          self.fail_fast = False
        elif opt == '-n':
          self.no_action = True
        else:
          error("unimplemented")
          badopts = True
    return args, badopts

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

  def parse(self, fp, parent_context=None, missing_ok=False):
    ''' Read a Mykefile and yield Macros and Targets.
    '''
    from .make import Target, Action
    action_list = None  # not in a target
    for context, line in readMakefileLines(self, fp, parent_context=parent_context,
                                           missing_ok=missing_ok):
      with Pfx(str(context)):
        if isinstance(line, OSError):
          e = line
          if e.errno == errno.ENOENT or e.errno == errno.EPERM:
            if missing_ok:
              continue
            e.context = context
            yield e
            break
          raise e
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
                    if not os.path.isabs(include_file):
                      include_file = os.path.join(
                          realpath(dirname(fp.name)), include_file
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
                    word for word in mexpr(context, self.namespaces).split() if word
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
                A = Action(context, 'shell', line[offset:], silent=action_silent)
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
  '''

  def __init__(self, maker):
    ''' Initialise the TargetMap.
        `maker` is the Maker using this TargetMap.
    '''
    self.maker = maker
    self.targets = {}
    self._lock = RLock()

  def __getitem__(self, name):
    ''' Return the Target for `name`.
        Raises KeyError if the Target is unknown and not inferrable.
    '''
    targets = self.targets
    if name not in targets:
      with self._lock:
        if name not in targets:
          T = self._newTarget(self.maker, name, context=None)
          if os.path.exists(name):
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

  def __init__(self, maker, name, context, prereqs, postprereqs, actions):
    ''' Initialise a new target.
          `maker`: the Maker with which this Target is associated.
          `context`: the file context, for citations.
          `name`: the name of the target.
          `prereqs`: macro expression to produce prereqs.
          `postprereqs`: macro expression to produce post-inference prereqs.
          `actions`: a list of actions to build this Target
          The same actions list is shared amongst all Targets defined
          by a common clause in the Mykefile, and extends during the
          Mykefile parse _after_ defining those Targets. So we do not
          modify it the class; instead we extend .pending_actions
          when .require() is called the first time, just as we do for a
          :make directive.
    '''

    Result.__init__(self, name=name, lock=RLock())
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
    return "{}[{}]".format(self.name, self.state)
    ##return "{}[{}]:{}:{}".format(self.name, self.state, self._prereqs, self._postprereqs)

  def mdebug(self, msg, *a):
    ''' Emit a debug message.
    '''
    return self.maker.debug_make(msg, *a)

  @locked
  def succeed(self):
    ''' Mark target as successfully made.
    '''
    self.mdebug("OK")
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
    self.mdebug(msg)
    if self.ready:
      if self.result:
        raise RuntimeError("%s.fail: already completed OK" % (self.name,))
      return
    self.failed = True
    self.result = False

  @prop
  def namespaces(self):
    ''' The namespaces for this Target: the special per-Target macros,
        the Maker's namespaces, the Maker's macros and the special macros.
    '''
    return (
        [
            {
                '@': lambda c, ns: self.name,
                '/': lambda c, ns: ' '.join(self.prereqs),
                '?': lambda c, ns: ' '.join(self.new_prereqs),
                # TODO: $< et al
            },
        ] + self.maker.namespaces + [
            self.maker.macros,
            SPECIAL_MACROS,
        ]
    )

  @prop
  def prereqs(self):
    ''' Return the prerequisite target names.
    '''
    prereqs = self._prereqs
    if isinstance(prereqs, MacroExpression):
      prereqs_mexpr = prereqs
      self._prereqs = prereqs_mexpr(self.context, self.namespaces).split()
    return self._prereqs

  @prop
  def new_prereqs(self):
    ''' Return the new prerequisite target names.
    '''
    if self.was_missing:
      # target missing: use all prereqs
      return self.prereqs
    Ps = []
    for Pname in self.prereqs:
      P = self.maker[Pname]
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
    self.maker.debug_make("%s: CANCEL", self)
    Result.cancel(self)

  def require(self):
    ''' Require this Target to be made.
    '''
    with Pfx("%r.require()", self.name):
      with self._lock:
        if self.state == ResultState.pending:
          # commence make of this Target
          self.maker.target_active(self)
          self.notify(self.maker.target_inactive)
          self.state = ResultState.running
          self.was_missing = self.mtime is None
          self.pending_actions = list(self.actions)
          Ts = []
          for Pname in self.prereqs:
            T = self.maker[Pname]
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
            self.maker.after(Ts, self._make_after_prereqs, Ts)
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
      mdebug = self.maker.debug_make
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
  def _make_next(self):
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
        self.mdebug("queue action: %s", A)
        Rs.append(A.act_later(self))
      else:
        self.mdebug("no actions remaining")

      if Rs:
        self.mdebug(
            "tasks still to do, requeuing: Rs=%s",
            ",".join(str(_) for _ in Rs)
        )
        self.maker.after(Rs, self._make_next)
      else:
        # all done, record success
        self.succeed()

class Action(NS):
  ''' A make ation.
  '''

  def __init__(self, context, variant, line, silent=False):
    self.context = context
    self.variant = variant
    self.line = line
    self.mexpr = MacroExpression.from_text(context, line)
    self.silent = silent
    self._lock = Lock()

  def __str__(self):
    return "<Action %s %s:%d>" % (
        self.variant, self.context.filename, self.context.lineno
    )

  __repr__ = __str__

  @prop
  def prline(self):
    ''' Printable form of this Action.
    '''
    return self.line.rstrip().replace('\n', '\\n')

  def act_later(self, target):
    ''' Request that this Action occur on behalf of the Target `target`.
        Return a Result which returns the success or failure
        of the action.
    '''
    R = Result(name="%s.action(%s)" % (target, self))
    target.maker.defer("%s:act[%s]" % (
        self,
        target,
    ), self._act, R, target)
    return R

  def _act(self, R, target):
    ''' Perform this Action on behalf of the Target `target`.
        Arrange to put the result onto `R`.
    '''
    with Pfx("%s.act(target=%s)", self, target):
      try:
        debug("start act...")
        M = target.maker
        mdebug = M.debug_make
        v = self.variant

        if v == 'shell':
          debug("shell command")
          shcmd = self.mexpr(self.context, target.namespaces)
          if M.no_action or not self.silent:
            print(shcmd)
          if M.no_action:
            mdebug("OK (maker.no_action)")
            R.put(True)
            return
          R.put(self._shcmd(target, shcmd))
          return

        if v == 'make':
          subtargets = self.mexpr(self.context, target.namespaces).split()
          mdebug("targets = %s", subtargets)
          subTs = [M[subtarget] for subtarget in subtargets]

          def _act_after_make():
            # analyse success of targets, update R
            ok = True
            mdebug = M.debug_make
            for T in subTs:
              if T.result:
                mdebug("submake \"%s\" OK", T)
              else:
                ok = False
                mdebug("submake \"%s\" FAIL", T)
            R.put(ok)

          for T in subTs:
            mdebug("submake \"%s\"", T)
            T.require()
          M.after(subTs, _act_after_make)
          return

        raise NotImplementedError("unsupported variant: %s" % (self.variant,))
      except Exception as e:
        error("action failed: %s", e)
        R.put(False)

  def _shcmd(self, target, shcmd):
    with Pfx("%s.act: shcmd=%r", self, shcmd):
      mdebug = target.maker.debug_make
      argv = (target.shell, '-c', shcmd)
      mdebug("Popen(%s,..)", argv)
      P = Popen(argv, close_fds=True)
      retcode = P.wait()
      mdebug("retcode = %d", retcode)
      return retcode == 0
