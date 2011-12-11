#!/usr/bin/python
#

from thread import allocate_lock

# actions come first, to keep the queue narrower
PRI_ACTION = 0
PRI_MAKE   = 1
PRI_PREREQ = 2

class Maker(object):
  ''' Main class representing a set of dependencies to make.
  '''

  def __init__(self):
    ''' Initialise a Maker.
    '''
    self.failFast = True
    self._targets = {}
    self._targets_lock = allocate_lock()

  def make(self, targets):
    ''' Make a bunch of targets.
    '''
    ok = False
    for target in targets:
      T = self.T

  def __getitem__(self, target):
    targets = self._targets
    with self._targets_lock:
      if target in targets:
        T = targets[target]
      else:
        T = targets[target] = Target(target, self)
    return T

class Target(object):

  def __init__(self, target, maker):
    ''' Initialise a new target.
        `target`: the name of the target.
        `maker`: the context Maker.
    '''
    self.name = target
    self.maker = maker
    self.prereqs = set()
    self.actions = []
    self._statusLF = None
    self._lock = allocate_lock()

  @property
  def status(self):
    ''' Return the madeness of this target, True for successfully
        made, False for failure to make.
        Internally it dispatches a LateFunction to make the target
        at need.
    '''
    with self._lock:
      if self._statusLF is None:
        self._statusLF = self.maker._makeQ.submit(self._make, name="make "+self.name, priority=PRI_MAKE)
    return self._statusLF()

  def _make(self):
    ''' Make the target.
    '''
    with Pfx(self.name):
      ok = True
      for dep in self.prereqs:
        if not dep.status:
          ok = False
          if self.maker.failFast:
            break
      if not ok:
        return False
      for action in self.actions:
        if not action.status:
          return False
      return True

class Action(object):

  def __init__(self, maker):
    self.maker = maker
    self._lock = allocate_lock()

  @property
  def status(self):
    with self._lock:
      if self._statusLF is None:
        self._statusLF = self.maker._makeQ.submit(self._run, name="action: "+self.name, priority=PRI_ACTION)
    return self._statusLF()

  def _run(self):
    raise NotImplementedError
