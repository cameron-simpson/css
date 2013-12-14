#!/usr/bin/python

from __future__ import print_function
import sys
if sys.hexversion < 0x02060000: from sets import Set as set
import os
import os.path
import errno
import getopt
from functools import partial
import logging
from subprocess import Popen
from threading import Thread
import time
from cs.debug import DEBUG
from cs.inttypes import Flags
from cs.threads import Lock, RLock, Channel, locked_property
from cs.later import Later
from cs.queues import NestingOpenCloseMixin
from cs.asynchron import Result, report as report_LFs, \
        Asynchron, ASYNCH_PENDING, ASYNCH_RUNNING, ASYNCH_CANCELLED, ASYNCH_READY
import cs.logutils
from cs.logutils import Pfx, info, error, debug, D
from cs.obj import O
from .parse import SPECIAL_MACROS, Macro, MacroExpression, \
                   parseMakefile, parseMacroExpression

SHELL = '/bin/sh'

# Later priority values
# actions come first, to keep the queue narrower
PRI_ACTION = 0
PRI_MAKE   = 1
PRI_PREREQ = 2

MakeDebugFlags = Flags('debug', 'flags', 'make', 'parse')

class Maker(NestingOpenCloseMixin, O):
  ''' Main class representing a set of dependencies to make.
  '''

  def __init__(self, makecmd, parallel=1):
    ''' Initialise a Maker.
        `makecmd`: used to define $(MAKE), typically sys.argv[0].
        `parallel`: the degree of parallelism of shell actions.
    '''
    if parallel < 1:
      raise ValueError("expected positive integer for parallel, got: %s" % (parallel,))
    O.__init__(self)
    self._lock = Lock()
    NestingOpenCloseMixin.__init__(self)
    self._O_omit.extend(['macros', 'targets', 'rules', 'namespaces'])
    self.parallel = parallel
    self._makeQ = None
    self.debug = MakeDebugFlags()
    self.debug.debug = False    # logging.DEBUG noise
    self.debug.flags = False    # watch debug flag settings
    self.debug.make = False     # watch make decisions
    self.debug.parse = False    # watch Makefile parsing
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
    self._namespaces = [{ 'MAKE': makecmd.replace('$', '$$') }]
    ##T = Thread(target=self._ticker, args=())
    ##T.daemon = True
    ##D("DISPATCH TICKER")
    ##T.start()

  def __str__(self):
    return "<MAKER>"

  def report(self, fp=None):
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

  def prepare(self):
    ''' Called after adjusting parameters.
    '''
    self._makeQ = Later(self.parallel, name=cs.logutils.cmd)
    self._makeQ.logTo("myke-later.log")

  def shutdown(self):
    self._makeQ.close()
    self._makeQ = None

  def __enter__(self):
    ''' Context manager entry.
        Prepare the _makeQ.
    '''
    if self._makeQ is None:
      self.prepare()
    self._makeQ.open()
    NestingOpenCloseMixin.__enter__(self)
    return self

  def __exit__(self, exc_type, exc_val, exc_tb):
    ''' Exit handler.
        Close the _makeQ.
    '''
    self.debug_make("%s.close()", self)
    self._makeQ.close()
    return False

  @property
  def namespaces(self):
    ''' The namespaces for this Maker: the built namespaces plus the special macros.
    '''
    return self._namespaces + [ SPECIAL_MACROS ]

  @property
  def makefiles(self):
    ''' The list of makefiles to consult, a tuple.
        It is not possible to add more makefiles after accessing this property.
    '''
    _makefiles = self._makefiles
    if not _makefiles:
      _makefiles = []
      makerc = os.environ.get( (cs.logutils.cmd+'rc').upper() )
      if makerc and os.path.exists(makerc):
        _makefiles.append(makerc)
      makefile = os.path.basename(cs.logutils.cmd).title() + 'file'
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

  def making(self, target):
    ''' Add this target to the set of "in progress" targets.
    '''
    self.debug_make("note target \"%s\" as active", target.name)
    with self._active_lock:
      self.active.add(target)

  def made(self, target, status):
    ''' Remove this target from the set of "in progress" targets.
    '''
    self.debug_make("note target \"%s\" as inactive (status=%s)", target.name, status)
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
    ''' Submit a function to be run after the supplied LateFunctions `LFs`.
    '''
    self.debug_make("after %s call %s(*%r, **%r)" % (LFs, func, a, kw))
    return self._makeQ.after(LFs, func, *a, **kw)

  def make(self, targets):
    ''' Synchronous call to make targets in series.
    '''
    ok = True
    mdebug = self.debug_make
    with Pfx("%s.make(%s)", self, " ".join(targets)):
      for target in targets:
        if isinstance(target, str):
          T = self[target]
        else:
          T = target
        T.require()
        if T.get():
          mdebug("MAKE %s: OK", T)
        else:
          mdebug("MAKE FAILED for %s", T)
          ok = False
          if self.fail_fast:
            mdebug("ABORT MAKE")
            break
    mdebug("MAKER.MAKE(%s): %s", targets, ok)
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
                "invalid debug flag, know: %s"
                % (",".join( sorted( [F for F in dir(self.debug) if F.isalpha() ] ) ),))
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
    opts, args = getopt.getopt(args, 'deikmnpqrstuvxENRj:D:S:f:')
    for opt, value in opts:
      with Pfx(opt):
        if opt == '-d':
          # debug mode
          self.setDebug('make', True)
        elif opt == '-D':
          for flag in [ w.strip().lower() for w in value.split(',') ]:
            if len(w) == 0:
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
    ''' Load the specified Makefiles.
        Each top level Makefile named gets its own namespace prepended
        to the namespaces list. In this way later top level Makefiles'
        definitions override ealier ones while still detecting conflicts
        within a particular Makefile.
	Also, the default_target property is set to the first
	encountered target if not yet set.
    '''
    for makefile in makefiles:
      self.debug_parse("load makefile: %s", makefile)
      first_target = None
      ns = {}
      self._namespaces.insert(0, ns)
      for O in parseMakefile(self, makefile, parent_context):
        with Pfx(O.context):
          if isinstance(O, Target):
            # record this Target in the Maker
            T = O
            self.debug_parse("add target %s", T)
            if '%' in T.name:
              # record this Target as a rule
              self.rules[T.name] = T
            else:
              self.targets[T.name] = T
              if first_target is None:
                first_target = T
          elif isinstance(O, Macro):
            self.debug_parse("add macro %s", O)
            ns[O.name] = O
          else:
            raise ValueError(
                    "parseMakefile({}): unsupported parse item received: {}{!r}"
                      .format(makefile, type(O), O)
                  )
      if first_target is not None:
        self.default_target = first_target

class TargetMap(O):
  ''' A mapping interface to the known targets.
      Makes targets as needed if inferrable.
      Raise KeyError for missing Targets which are not inferrable.
  '''

  def __init__(self, maker):
    ''' Initialise the TargetMap.
        `maker` is the Maker using this TargetMap.
    '''
    self._O_omit = ['maker', 'targets']
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
            T.result = True
          else:
            error("can't infer a Target to make %r" % (name,))
            T.result = False
          targets[name] = T
    return targets[name]

  def _newTarget(self, maker, name, context, prereqs=(), postprereqs=(), actions=()):
    ''' Construct a new Target.
    '''
    return Target(maker, name, context, prereqs, postprereqs, actions)

  def __setitem__(self, name, target):
    ''' Record new target in map.
        Check that the name matches.
        Reject duplicate names.
    '''
    if name != target.name:
      raise ValueError("tried to record Target as %r, but target.name = %r"
                       % (name, target.name))
    with self._lock:
      if name in self.targets:
        raise ValueError("redefinition of Target %r, previous definition from %s"
                         % (name, self.targets[name].context))
      self.targets[name] = target

class Target(Result):

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
	Mykefile parse _after_ defining those Targets. So we do not modify it the class;
        instead we extend .pending_actions when .require() is called the first time,
        just as we for a :make directive.
    '''

    Result.__init__(self)
    self._O_omit.extend(['actions', 'maker', 'namespaces'])
    self.maker = maker
    self.context = context
    self.name = name
    self.shell = SHELL
    self._prereqs = prereqs
    self._postprereqs = postprereqs
    self.actions = actions
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
    self.out_of_date = False
    self.is_new = False
    self.was_missing = self.mtime is None

  def __str__(self):
    return "{}[{}]".format(self.name, self.madeness())
    ##return "{}[{}]:{}:{}".format(self.name, self.state, self._prereqs, self._postprereqs)

  def madeness(self):
    ''' Report the status of this target as text.
    '''
    state = self.state
    if state == ASYNCH_PENDING:
      return "unconsidered"
    if state == ASYNCH_RUNNING:
      return "making"
    if state == ASYNCH_CANCELLED:
      return "cancelled"
    if state != ASYNCH_READY:
      raise RuntimeError("%s.madeness: unexpected state %s" % (self, state))
    return "made" if self.result else "FAILED"

  @property
  def namespaces(self):
    ''' The namespaces for this Target: the special per-Target macros,
        the Maker's namespaces, the Maker's macros and the special macros.
    '''
    return ( [ { '@':     lambda c, ns: self.name,
                 '/':     lambda c, ns: ' '.join(self.prereqs),
                 '?':     lambda c, ns: ' '.join(self.new_prereqs),
                 # TODO: $< et al
               },
             ]
           + self.maker.namespaces
           + [
               self.maker.macros,
               SPECIAL_MACROS,
             ]
           )

  @property
  def prereqs(self):
    ''' Return the prerequisite target names.
    '''
    prereqs = self._prereqs
    if isinstance(prereqs, MacroExpression):
      self._prereqs = prereqs(self.context, self.namespaces).split()
    return self._prereqs

  @property
  def new_prereqs(self):
    ''' Return the new prerequisite target names.
    '''
    if self.was_missing:
      return self.prereqs
    Ps = []
    for Pname in self.prereqs:
      P = self.maker[Pname]
      if not P.ready:
        raise RuntimeError("%s: prereq %r not ready", self.name, Pname)
      if self.older_than(P):
        Ps.append(Pname)
      else:
        D("NOT NEW %r", Pname)
    return Ps

  @locked_property
  @DEBUG
  def mtime(self):
    try:
      s = os.stat(self.name)
    except OSError:
      return None
    return s.st_mtime

  def older_than(self, other):
    if self.was_missing:
      return True
    if isinstance(other, str):
      other = self.maker[other]
    if other.is_new:
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
    Asynchron.cancel(self)

  def require(self):
    ''' Require this Target to be made.
    '''
    with self._lock:
      if self.pending:
        self.state = ASYNCH_RUNNING
        self.LFs = []   # pending LFs returning True/False
        self.pending_targets = list(self.prereqs)
        self.pending_actions = list(self.actions)
        # queue the first unit of work
        self.maker.defer("%s._make_partial" % (self,), self._make_partial)

  @DEBUG
  def _apply_prereq(self, LF):
    ''' Apply the consequences of the complete prereq LF.
    '''
    with Pfx("%s._apply_prereqs(LF=%s)", self, LF):
      mdebug = self.maker.debug_make
      if not LF.ready:
        raise RuntimeError("not ready")
      if LF.result:
        mdebug("OK")
        try:
          is_new = LF.is_new
        except AttributeError:
          # presuming not a Target
          pass
        else:
          if is_new:
            mdebug("out of date because is_new(LF)")
            self.out_of_date = True
          else:
            LFmtime = getattr(LF, 'mtime', None)
            if LFmtime is not None:
              mtime = self.mtime
              if mtime is None or LFmtime >= mtime:
                mdebug("out of date because older than LF")
                self.out_of_date = True
      else:
        mdebug("FAIL")
        self.result = False

  @DEBUG
  def _make_partial(self):
    ''' The inner/recursive/deferred function from _make.
        Perform the next unit of work in making this Target.
        If we complete without blocking, put True or False onto self.made.
        Otherwise queue a background function to block and resume.
    '''
    with Pfx(self.name):
      M = self.maker
      mdebug = M.debug_make

      LFs = self.LFs
      if LFs:
        mdebug("collect LFs=%s", LFs)
        self.LFS = []
        for LF in LFs:
          with Pfx(LF):
            self._apply_prereq(LF)
            if not LF.result:
              mdebug("FAILed")
              return

      LFs = []
      targets = self.pending_targets
      self.pending_targets = []
      for T in targets:
        with Pfx(str(T)):
          T = M[T]
          if T.ready:
            self._apply_prereq(T)
            if T.result:
              mdebug("OK")
            else:
              mdebug("FAILed")
              self.result = False
              return
          else:
            # require T and note it for consideration next time
            mdebug("not ready, requiring it...")
            T.require()
            LFs.append(T)

      if not LFs:
        # no pending targets, what about actions?
        # if we're out of date or missing,
        # queue an action and mark ourselves is_new
        # if so, queue the first one
        if self.out_of_date or self.mtime is None:
          mdebug("NEED TO MAKE %s (out_of_date=%r, mtime=%r)", self.name, self.out_of_date, self.mtime)
          self.is_new = True
          actions = self.pending_actions
          if actions:
            A = actions.pop(0)
            mdebug("queue action: %s", A)
            LFs.append(A.act_later(self))
          else:
            mdebug("no actions")
        else:
          mdebug("not out of date (mtime=%r)", self.mtime)

      if LFs:
        self.LFs = LFs
        mdebug("tasks still to do, requeuing")
        self.maker.after(LFs, None, self._make_partial)
      else:
        # all done, record success
        mdebug("SUCCESS")
        self.result = True

class Action(O):

  def __init__(self, context, variant, line, silent=False):
    self.context = context
    self.variant = variant
    self.line = line
    self.mexpr, _ = parseMacroExpression(context, line)
    self.silent = silent
    self._lock = Lock()

  def __str__(self):
    return "<Action %s %s:%d>" % (self.variant, self.context.filename, self.context.lineno)

  __repr__ = __str__

  @property
  def prline(self):
    return self.line.rstrip().replace('\n', '\\n')

  def act_later(self, target):
    ''' Request that this Action occur on behalf of the Target `target`.
	Return an Asynchron which returns the success or failure
	of the action.
    '''
    R = Result()
    ALF = target.maker.defer("%s:act[%s]" % (self,target,), self._act, R, target)
    return R

  @DEBUG
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
          subTs = [ M[subtarget] for subtarget in subtargets ]
          def _act_after_make():
            ok = True
            mdebug = M.debug_make
            for T in subTs:
              if T.result:
                mdebug("submake \"%s\" OK", T)
              else:
                ok = False
                mdebug("submake \"%s\" FAIL", T)
            return ok
          for T in subTs:
            mdebug("submake \"%s\"", T)
            T.require()
          target.maker.after(subTs, R, _act_after_make)
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

if __name__ == '__main__':
  from . import main, default_cmd
  sys.stderr.flush()
  sys.exit(main([default_cmd] + sys.argv[1:]))
