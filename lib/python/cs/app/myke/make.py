#!/usr/bin/python
#

import sys
import os.path
import getopt
from functools import partial
import logging
from subprocess import Popen
from thread import allocate_lock
import cs.misc
from cs.later import Later
from cs.logutils import Pfx, info, error, debug
from .parse import parseMakefile, Macro, parseMacroExpression, MacroExpression

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
      parallel = 1
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
    self._targets = {}
    self._targets_lock = allocate_lock()
    self._macros = {}
    self._macros_lock = allocate_lock()

  def __str__(self):
    return '%s<Maker>' % (cs.misc.cmd,)
  def _queue(self, func, name, priority):
    return self._makeQ.submit(func, name=name, priority=priority)

  @property
  def makefiles(self):
    ''' The list of makefiles to consult, a tuple.
        It is not possible to add more makefiles are accessing this property.
    '''
    _makefiles = self._makefiles
    if not _makefiles:
      self._makefiles = _makefiles = ( os.path.basename(cs.misc.cmd).title() + 'file', )
    elif type(_makefiles) is not tuple:
      self._makefiles = _makefiles = tuple(_makefiles)
    return _makefiles

  def debug_make(self, msg, *a, **kw):
    if self.debug.make:
      info(msg, *a, **kw)

  def debug_parse(self, msg, *a, **kw):
    if self.debug.parse:
      info(msg, *a, **kw)

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
        T = targets[target] = Target(target, self)
    return T

  def setDebug(self, flag, value):
    ''' Set or clear the named debug option.
    '''
    with Pfx("setDebug(%s, %s)" % (repr(flag), repr(value))):
      if not flag.isalpha() or not hasattr(self.debug, flag):
        raise AttributeError, "invalid debug flag"
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
              error("bad flag %s: %s", repr(flag), e)
              badopts = True
        elif opt == '-f':
          self._makefiles.append(value)
        elif opt == '-n':
          self.no_action = True
        else:
          error("unimplemented")
          badopts = True
    return args, badopts

  def loadMakefiles(self):
    for makefile in self.makefiles:
      with Pfx(makefile):
        first_target = None
        for O in parseMakefile(self, makefile, [self._macros]):
          if isinstance(O, Target):
            self.debug_parse("add target %s", O)
            self._targets[O.name] = O
            if first_target is None:
              first_target = O
            O.namespaces = self._macros
          elif isinstance(O, Macro):
            self.debug_parse("add macro %s", O)
            self._macros[O.name] = O
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
    self.namespaces = None
    self.shell = SHELL
    self._prereqs = prereqs
    self._postprereqs = postprereqs
    self.maker = None
    self.actions = actions
    self.state = "unmade"
    self._statusLF = None
    self._lock = allocate_lock()

  def __str__(self):
    return "{}[{}]:{}:{}".format(self.name, self.state, self._prereqs, self._postprereqs)

  def make(self, maker):
    ''' Request that this target be made.
        Check the status property to find out how things went; it will block if necessary.
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
        self._prereqs = prereqs.eval(self.namespaces).split()
    return self._prereqs

  def _make(self):
    ''' Make the target. Private function submtted to the make queue.
    '''
    with Pfx(self.name):
      self.maker.debug_make("commence make: %d actions, prereqs = %s", len(self.actions), self.prereqs)
      ok = True
      for dep in self.prereqs:
        dep.make(self.maker)    # request item
        self.maker.debug_make("wait for status of %s", dep)
        T_ok = dep.status
        self.maker.debug_make("status of %s is %s", dep, T_ok)
        if not T_ok:
          ok = False
          if self.maker.fail_fast:
            self.maker.debug_make("abort")
            break
      if not ok:
        self.maker.debug_make("prereqs bad, skip actions")
        return False
      for action in self.actions:
        self.maker.debug_make("dispatch action: %s", action)
        A_ok = action.act(self)
        self.maker.debug_make("action status = %s", A_ok)
        if not A_ok:
          return False
      return True

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
    prline = self.line.replace('\n', '\\n')
    return "<Action %s %s>" % (self.variant, prline)

  def act(self, target):
    debug("start _act...")
    M = target.maker
    v = self.variant
    if v == 'shell':
      shcmd = self.mexpr.eval(target.namespaces)
      if not self.silent:
        print shcmd
      if M.no_action:
        return True
      M.debug_make("shell command: %s", shcmd)
      argv = (target.shell, '-c', shcmd)
      debug("Popen(%s,..)", argv)
      P = Popen(argv, close_fds=True)
      retcode = P.wait()
      return retcode == 0

    if v == 'make':
      subtargets = self.mexpr.eval().split()
      for submake in subtargets:
        self.maker[submake].make()
      for submake in submakes:
        if not self.maker[submake].status:
          return False
      return True

    raise NotImplementedError, "unsupported variant: %s" % (self.variant,)

if __name__ == '__main__':
  from . import main, default_cmd
  sys.stderr.flush()
  sys.exit(main([default_cmd]+sys.argv[1:]))
