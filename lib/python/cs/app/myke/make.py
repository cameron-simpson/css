#!/usr/bin/python
#

import sys
if sys.hexversion < 0x02060000: from sets import Set as set
import os
import os.path
import errno
import getopt
from functools import partial
import logging
from subprocess import Popen
from thread import allocate_lock
import cs.misc
from cs.later import Later, report as report_LFs, CallableValue
from cs.logutils import Pfx, info, error, debug, D
from cs.threads import Channel
from .parse import SPECIAL_MACROS, Macro, MacroExpression, \
                   parseMakefile, parseMacroExpression

SHELL = '/bin/sh'

# actions come first, to keep the queue narrower
PRI_ACTION = 0
PRI_MAKE   = 1
PRI_PREREQ = 2

class Flags(object):
  pass

class Maker(object):
  ''' Main class representing a set of dependencies to make.
  '''

  def __init__(self, parallel=1):
    ''' Initialise a Maker.
        `parallel`: the degree of parallelism of shell actions.
    '''
    if parallel < 1:
      raise ValueError("expected positive integer for parallel, got: %s" % (parallel,))
    self.parallel = parallel
    self.debug = Flags()
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
    self._targets = {}
    self._targets_lock = allocate_lock()
    self.precious = set()
    self.active = set()
    self.active_lock = allocate_lock()
    self._namespaces = []

  def __enter__(self):
    ''' Context manager entry.
        Prepare the _makeQ.
    '''
    self._makeQ = Later(self.parallel, name=cs.misc.cmd)
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
      makerc = os.environ.get( (cs.misc.cmd+'rc').upper() )
      if makerc and os.path.exists(makerc):
        _makefiles.append(makerc)
      makefile = os.path.basename(cs.misc.cmd).title() + 'file'
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
    with self.active_lock:
      self.active.add(target)

  def made(self, target, status):
    ''' Remove this target from the set of "in progress" targets.
    '''
    self.debug_make("note target \"%s\" as inactive (status=%s)", target.name, status)
    with self.active_lock:
      self.active.remove(target)

  def cancel_all(self):
    ''' Cancel all "in progress" targets.
    '''
    self.debug_make("cancel_all!")
    with self.active_lock:
      Ts = list(self.active)
    for T in Ts:
      T.cancel()

  def defer(self, func, *a, **kw):
    self.debug_make("defer %s(*%r, **%r)" % (func, a, kw))
    return self._makeQ.defer(func, *a, **kw)

  def bg(self, func, *a, **kw):
    self.debug_make("bg %s(*%r, **%r)" % (func, a, kw))
    return self._makeQ.bg(func, *a, **kw)

  def make(self, targets):
    ''' Make a bunch of targets.
    '''
    mdebug = self.debug_make
    with Pfx("%s.make(%s)" % (self, " ".join(targets))):
      ok = True
      LFs = []
      for target in targets:
        mdebug("make(%s)" % (target,))
        if isinstance(target, str):
          T = self[target]
        else:
          T = target
        LFs.append(T.make(as_func=True))
      mdebug("collect make statuses...")
      for LF in report_LFs(LFs):
        T_ok = LF()
        assert T_ok is True or T_ok is False
        mdebug("status = %s", T_ok)
        if not T_ok:
          error("FAILed")
          ok = False
          if self.fail_fast:
            break
    return ok

  def __getitem__(self, target):
    ''' Return the specified Target.
    '''
    targets = self._targets
    with self._targets_lock:
      if target in targets:
        T = targets[target]
      else:
        raise ValueError("unknown Target \"%s\"" % (target,))
        ##T = targets[target] = Target(target, self)
    return T

  def setDebug(self, flag, value):
    ''' Set or clear the named debug option.
    '''
    with Pfx("setDebug(%r, %r)" % (flag, value)):
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
      with Pfx("%s" % (opt,)):
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
            except AttributeError, e:
              error("bad flag %r: %s", flag, e)
              badopts = True
        elif opt == '-f':
          self._makefiles.append(value)
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
    '''
    for makefile in makefiles:
      self.debug_parse("load makefile: %s", makefile)
      first_target = None
      ns = {}
      self._namespaces.insert(0, ns)
      for O in parseMakefile(self, makefile, parent_context):
        if isinstance(O, Target):
          T = O
          self.debug_parse("add target %s", T)
          self._targets[T.name] = T
          if first_target is None:
            first_target = T
        elif isinstance(O, Macro):
          self.debug_parse("add macro %s", O)
          ns[O.name] = O
        else:
          raise ValueError, "parseMakefile({}): unsupported parse item received: {}{}".format(makefile, type(O), repr(O))
      if first_target is not None:
        self.default_target = first_target

class Target(object):

  def __init__(self, maker, name, context, prereqs, postprereqs, actions):
    ''' Initialise a new target.
        `maker`: the Maker with which this Target is associated.
        `context`: the file context, for citations.
        `name`: the name of the target.
        `prereqs`: macro expression to produce prereqs.
        `postprereqs`: macro expression to produce post prereqs.
    '''
    self.maker = maker
    self.context = context
    self.name = name
    self.shell = SHELL
    self._prereqs = prereqs
    self._postprereqs = postprereqs
    self.cancelled = False
    self.actions = actions
    self.state = "unmade"
    self._status = None
    self._lock = allocate_lock()

  def __str__(self):
    return "{}[{}]:{}:{}".format(self.name, self.state, self._prereqs, self._postprereqs)

  @property
  def namespaces(self):
    return ( [ { '@':     lambda c, ns: self.name,
                 '/':     lambda c, ns: [ P.name for P in self.prereqs ],
                 # TODO: $? et al
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

  def cancel(self):
    ''' Cancel this Target.
        Actions will cease as soon as decorum allows.
    '''
    self.maker.debug_make("%s: CANCEL", self)
    self.cancelled = True

  def make(self, as_func=False):
    ''' Request that this target be made.
        Return its status.
        This will be either True or False if the make is complete.
        If the make is incomplete it will return a PendingFunction.
        Return the LateFunction that will report the madeness.
        Check the .status property to find out how things went;
        it will block if necessary.
    '''
    with self._lock:
      status = self._status
      if self._status is None:
        status = self._status = self._make()
    if as_func and (status is True or status is False):
      # not just a lambda because the caller may want to use Later.report()
      status = CallableValue(status)
    return status

  @property
  def status(self):
    ''' Return the make status of this Target.
        This will be either True or False if the make is complete.
        If the make is incomplete it will return a PendingFunction.
    '''
    # avoid recursion between this and make()
    status = self._status
    D("0: status = %r" % (status,))
    if status is None:
      status = self._status = self.make()
      D("1: status = %r" % (status,))
    # collapse status if make complete
    if status is not True and status is not False:
      D("status = %r" % (status,))
      if status.ready:
        status = self._status = status()
    return status

  @property
  def ok(self):
    ''' Return the madeness of this target, True for successfully
        made, False for failure to make.
        This will block if necessary for the make to complete.
    '''
    status = self.status
    if status is not True and status is not False:
      self.maker.debug_make("%s: wait for make...", self)
      status = status()
    return status

  def _make(self, retq=None):
    ''' Commence making this Target.
        If `retq` is None this is the outermost call:
          Return True or False if the make can complete without blocking.
          Otherwise return a PendingFunction that will complete later.
          This endeavours to do as much as possible without queuing
          a PendingFunction in order to minimise the number of threads
          in play, hence the unusual return signature.
        Otherwise this is an inner recursive/deferred PendingFunction:
          If we complete without blocking, put True or False onto retq.
          Otherwise queue a background function to block.
    '''
    M = self.maker
    mdebug = M.debug_make

    if retq is None:
      self.LFs = []
      self.pending_targets = list(self.prereqs)
      self.pending_actions = list(self.actions)

    # process pending tasks
    # - collect outstanding results and inspect
    # - if ok, queue more targets or an action
    # - repeat until not ok or nothing pending
    ok = True
    with Pfx("make %s" % (self,)):
      while ok:
        # wait for requirements, if any
        LFs = self.LFs
        if LFs:
          self.LFs = []
          for LF in report_LFs(LFs):
            ok = LF()
            if not ok:
              mdebug("requirement FAILed")
              if M.fail_fast:
                M.cancel_all()
              break
            if self.cancelled:
              mdebug("CANCELLED")
              ok = False
              break

        if not ok:
          break

        # requirements complete, proceed with outstanding stuff
        # any outstanding items to queue for making?
        targets = self.pending_targets
        if targets:
          self.pending_targets = []
          for dep in targets:
            with Pfx(dep):
              self.LFs.append(M[dep].makeX(as_func=True))
        else:
          # any outstanding actions?
          actions = self.pending_actions
          if actions:
            self.LFs.append(actions.pop(0).act(self, as_func=True))

        # We're trying to minimise the number of threads in play.
        # Therefore, any "ready" LateFunctions get gathered here.
        # We apply the same failure code as earlier, but skip the
        # poll of self.cancelled.
        LFs = self.LFs
        self.LFs = []
        for LF in LFs:
          if LF.ready:
            ok = LF()
            if not ok:
              mdebug("requirement FAILed")
              if M.fail_fast:
                M.cancel_all()
              break
          else:
            self.LFs.append(LF)

        # failure, abort
        if not ok:
          break

        # nothing pending?
        if not self.LFs:
          break

        # we must delay for the unready items
        outer = retq is None
        if outer:
          # make a Channel to collect the result
          retq = Channel()
        # queue a blocking function
        with Pfx("QUEUE BLOCKING BG FUNCTION"):
          M.bg(self._make, retq)
        if outer:
          # return a LateFunction to collect from the Channel
          with Pfx("QUEUE COLLECTING BG FUNCTION"):
            LF = M.bg(retq)
          return LF
        return

      # we have a result - ok or not
      if retq is None:
        return ok

      # report the status upstream
      retq.put(ok)

class Action(object):

  def __init__(self, context, variant, line, silent=False):
    self.context = context
    self.variant = variant
    self.line = line
    self.mexpr, _ = parseMacroExpression(context, line)
    self.silent = silent
    self._lock = allocate_lock()

  def __str__(self):
    prline = self.line.rstrip().replace('\n', '\\n')
    return "<Action %s %s>" % (self.variant, prline)

  def act(self, target, as_func=False):
    ''' Request that this action occur.
        `target`: the Target reqesting this action.
        Return its status.
        This will be either True or False if the action completed without
        blocking. If the action blocked it will return a PendingFunction.
    '''
    with Pfx("%s.act(target=%s, as_func=%s)" % (self, target, as_func)):
      debug("start act...")
      M = target.maker
      mdebug = M.debug_make
      v = self.variant
      if v == 'shell':
        debug("shell command")
        shcmd = self.mexpr(self.context, target.namespaces)
        if not self.silent:
          print shcmd
        if M.no_action:
          mdebug("OK (maker.no_action)")
          return CallableValue(True) if as_func else True
        return M.defer(self._shcmd, target, shcmd)

      if v == 'make':
        subtargets = self.mexpr.eval().split()
        mdebug("targets = %s", subtargets)
        for submake in subtargets:
          self.maker[submake].make()
        for submake in submakes:
          status = self.maker[submake].status
          mdebug("%s submake %s status = %s", ("OK" if status else "FAILED"), submake, status)
          if not status:
            return CallableValue(False) if as_func else False
        mdebug("OK all submakes, return True")
        return CallableValue(True) if as_func else True

      raise NotImplementedError, "unsupported variant: %s" % (self.variant,)

  def _shcmd(self, target, shcmd):
    with Pfx("%s.act: shcmd=%r" % (self, shcmd)):
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
  sys.exit(main([default_cmd]+sys.argv[1:]))
