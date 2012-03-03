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
from cs.later import Later, report as report_LFs
from cs.logutils import Pfx, info, error, debug
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

  def __init__(self, parallel=None):
    ''' Initialise a Maker.
    '''
    if parallel is None:
      parallel = 100
    self.parallel = parallel
    self.debug = Flags()
    self.debug.debug = False    # logging.DEBUG noise
    self.debug.flags = False    # watch debug flag settings
    self.debug.make = False     # watch make decisions
    self.debug.parse = False    # watch Makefile parsing
    self.fail_fast = True
    self.no_action = False
    self.default_target = None
    self._makeQ = None
    self._makefiles = []
    self.appendfiles = []
    self.macros = {}
    self._targets = {}
    self._targets_lock = allocate_lock()
    self.precious = set()
    self.active = set()
    self.active_lock = allocate_lock()
    self._namespaces = []

  def __str__(self):
    return '%s<Maker>' % (cs.misc.cmd,)

  def _queue(self, func, name, priority):
    return self._makeQ.submit(func, name=name, priority=priority)

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
    with self._active_lock:
      Ts = list(self.active)
    for T in Ts:
      T.cancel()

  def make(self, targets):
    ''' Make a bunch of targets.
    '''
    with Pfx("%s.make(%s)" % (self, targets)):
      with Later(self.parallel, name=cs.misc.cmd) as MQ:
        ok = True
        self._makeQ = MQ
        Tlist = []
        for target in targets:
          if type(target) is str:
            T = self._targets.get(target)
            if not T:
              error("don't know how to make %s", target)
              ok = False
              if self.fail_fast:
                break
              else:
                continue
          else:
            T = target
          T.make(self)
          Tlist.append(T)
        for T in Tlist:
          debug("%s: collect status...", T)
          T_ok = T.status
          self.debug_make("%s: status = %s", T, T_ok)
          if not T_ok:
            error("make %s fails", T)
            ok = False
            if self.fail_fast:
              break
      self._makeQ = None
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

  def __init__(self, name, context, prereqs, postprereqs, actions):
    ''' Initialise a new target.
        `context`: the file context, for citations.
        `name`: the name of the target.
        `prereqs`: macro expression to produce prereqs.
        `postprereqs`: macro expression to produce post prereqs.
    '''
    self.context = context
    self.name = name
    self.shell = SHELL
    self._prereqs = prereqs
    self._postprereqs = postprereqs
    self.cancelled = False
    self.maker = None
    self.actions = actions
    self.state = "unmade"
    self._statusLF = None
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

  def cancel(self):
    ''' Cancel this Target.
        Actions will cease as soon as decorum allows.
    '''
    self.maker.debug_make("CANCEL %s", self)
    self.cancelled = True

  def make(self, maker):
    ''' Request that this target be made.
        Return the LateFunction that will report the madeness.
        Check the .status property to find out how things went;
        it will block if necessary.
    '''
    with self._lock:
      if self.maker is None:
        self.maker = maker
    return self._status_func

  @property
  def _status_func(self):
    with self._lock:
      if self._statusLF is None:
        self.maker.debug_make("queue make(%s)", self.name)
        self._statusLF = self.maker._queue(self._make, name="make "+self.name, priority=PRI_MAKE)
    return self._statusLF

  @property
  def status(self):
    ''' Return the madeness of this target, True for successfully
        made, False for failure to make.
        Internally it dispatches a LateFunction to make the target
        at need.
    '''
    debug("%s: wait for status...", self)
    return self._status_func()

  @property
  def prereqs(self):
    ''' Return the prerequisite target names.
    '''
    with self._lock:
      prereqs = self._prereqs
      if isinstance(prereqs, MacroExpression):
        self._prereqs = prereqs(self.context, self.namespaces).split()
    return self._prereqs

  def _make(self):
    ''' Make the target. Private function submtted to the make queue.
    '''
    mdebug = self.maker.debug_make
    mdebug("%s: COMMENCING _make()...", self.name)
    self.maker.making(self)
    made = False
    with Pfx("make %s" % (self.name,)):
      mdebug("commence make: %d actions, prereqs = %s", len(self.actions), self.prereqs)
      dep_status_LFs = []
      for dep in self.prereqs:
        D = self.maker[dep]
        if self.cancelled:
          break
        mdebug("request dependency: %s", dep)
        dep_status_LFs.append(D.make(self.maker))
      if self.cancelled:
        D_ok = False
      else:
        D_ok = True
        mdebug("wait for dependencies...")
        for LF in report_LFs(dep_status_LFs):
          dep, status = LF()
          if not LF():
            d_ok = False
            mdebug("FAILED %s", dep)
            break
          mdebug("OK %s", dep)
          if self.cancelled:
            mdebug("CANCELLED: not waiting for more dependencies")
            break
        if self.cancelled:
          mdebug("CANCELLED: considering dependencies not ok")
          D_ok = False
        if not D_ok:
          mdebug("prerequisites FAILED, skip actions")
          if self.maker.fail_fast:
            mdebug("fail_fast: cancel all maker targets")
            self.maker.cancel_all()
        else:
          A_ok = True
          for action in self.actions:
            mdebug("dispatch action: %s", action)
            A_status = action.act(self)
            mdebug("%s action: %s", ("OK" if A_status else "FAILED"), action)
            if not A_status:
              A_ok = False
              break
            if self.cancelled:
              break
          if self.cancelled:
            A_ok = False
          if A_ok:
            made = True
      mdebug("OK" if made else "FAILED")
      if not made and self.name not in self.maker.precious:
          try:
            os.remove(self.name)
          except OSError, e:
            if e.errno != errno.ENOENT:
              error("%s", e)
          else:
            mdebug("removed %s")
      self.maker.made(self, made)
      return self, made

class Action(object):

  def __init__(self, context, variant, line, silent=False):
    self.context = context
    self.variant = variant
    self.line = line
    self.mexpr, _ = parseMacroExpression(context, line)
    self.silent = silent
    self._statusLF = None
    self._lock = allocate_lock()

  def __str__(self):
    prline = self.line.rstrip().replace('\n', '\\n')
    return "<Action %s %s>" % (self.variant, prline)

  def act(self, target):
    with Pfx(str(self)):
      debug("start _act...")
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
          return True
        argv = (target.shell, '-c', shcmd)
        mdebug("Popen(%s,..)", argv)
        P = Popen(argv, close_fds=True)
        retcode = P.wait()
        mdebug("retcode = %d", retcode)
        return retcode == 0

      if v == 'make':
        subtargets = self.mexpr.eval().split()
        mdebug("targets = %s", subtargets)
        for submake in subtargets:
          self.maker[submake].make()
        for submake in submakes:
          status = self.maker[submake].status
          mdebug("%s submake %s status = %s", ("OK" if status else "FAILED"), submake, status)
          if not status:
            return False
        mdebug("OK all submakes, return True")
        return True

      raise NotImplementedError, "unsupported variant: %s" % (self.variant,)

if __name__ == '__main__':
  from . import main, default_cmd
  sys.stderr.flush()
  sys.exit(main([default_cmd]+sys.argv[1:]))
