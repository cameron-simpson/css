#!/usr/bin/python
#

import sys
import os.path
from subprocess import Popen
from thread import allocate_lock
from cs.later import Later
from cs.logutils import Pfx, info, error
from .parse import parseMakefile, Macro, parseMacroExpression

# actions come first, to keep the queue narrower
PRI_ACTION = 0
PRI_MAKE   = 1
PRI_PREREQ = 2

class Maker(object):
  ''' Main class representing a set of dependencies to make.
  '''

  def __init__(self, parallel=None):
    ''' Initialise a Maker.
    '''
    if parallel is None:
      parallel = 1
    self.failFast = True
    self._makeQ = Later(parallel)
    self._makefile = None
    self._targets = {}
    self._targets_lock = allocate_lock()
    self._macros = {}
    self._macros_lock = allocate_lock()

  @property
  def makefile(self):
    if self._makefile is None:
      import cs.misc
      self._makefile = os.path.basename(cs.misc.cmd).title() + 'file'
    return self._makefile

  def make(self, targets):
    ''' Make a bunch of targets.
    '''
    Tlist = []
    for target in targets:
      if type(target) is str:
        T = self.targets.get(target)
        if not target:
          error("don't know how to make %s", target)
      else:
        T = target
      T.make(self)
      Tlist.append(T)
    ok = True
    for T in Tlist:
      if not T.status:
        error("make %s fails", T)
        ok = False
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

  def loadMakefile(self, makefile=None):
    if makefile is None:
      makefile = self.makefile
    with Pfx("load %s" % (makefile,)):
      for O in parseMakefile(makefile, [self._macros]):
        if isinstance(O, Target):
          info("add target %s", O)
          self._targets[O.name] = O
        elif isinstance(O, Macro):
          info("add macro %s", O)
          self._macros[O.name] = O
        else:
          raise ValueError, "parseMakefile({}): unsupported parse item received: {}{}".format(makefile, type(O), repr(O))

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
    self._prereqs = prereqs
    self._postprereqs = postprereqs
    self.maker = None
    self.actions = []
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
  def status(self):
    ''' Return the madeness of this target, True for successfully
        made, False for failure to make.
        Internally it dispatches a LateFunction to make the target
        at need.
    '''
    return self._status_func()

  @property
  def _status_func(self):
    with self._lock:
      if self._statusLF is None:
        self._statusLF = self.maker._makeQ.submit(self._make, name="make "+self.name, priority=PRI_MAKE)
    return self._statusLF

  @property
  def prereqs(self):
    ''' Return the prerequisite target names.
    '''
    with self._lock:
      prereqs = self._prereqs
      if isinstance(_prereqs, MacroExpression):
        self._prereqs = prereqs.eval().split()
    return self._prereqs

  def _make(self):
    ''' Make the target. Private function submtted to the make queue.
    '''
    with Pfx(self.name):
      ok = True
      for dep in self.prereqs:
        dep.make(self.maker)    # request item
        if not dep.status:
          ok = False
          if self.maker.failFast:
            break
      if not ok:
        return False
      for action in self.actions:
        LF = action._submit(self.maker, self)
        if not LF:
          return False
      return True

class Action(object):

  def __init__(self, context, variant, line):
    self.context = context
    self.variant = variant
    self.line = line
    self.mexpr = parseMacroExpression(context, line)
    self._lock = allocate_lock()

  def _submit(self, maker, target):
    ''' Submit instance of this action for a specific target.
        Should really only be called by Target._make().
    '''
    return self.maker._makeQ.submit(partial(self._act, target), name="{}: {}: {}".format(target.name, self.variant, self.line), priority=PRI_ACTION)

  def _act(self, target):
    v = self.variant
    if v == 'shell':
      shcmd = self.mexpr.eval()
      argv = (target.shell, '-c', shcmd)
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

  @property
  def status(self):
    with self._lock:
      if self._statusLF is None:
        self._statusLF = self.maker._makeQ.submit(self._run, name="action: "+self.name, priority=PRI_ACTION)
    return self._statusLF()

  def _run(self):
    raise NotImplementedError

if __name__ == '__main__':
  from . import main, default_cmd
  print >>sys.stderr, "argv =", repr(sys.argv)
  sys.stderr.flush()
  sys.exit(main([default_cmd]+sys.argv[1:]))
