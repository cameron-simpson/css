#!/usr/bin/python
#
# Thread convenience facilities.
#       - Cameron Simpson <cs@zip.com.au> 18nov2007
#

from __future__ import with_statement

DISTINFO = {
    'description': "threading and communication/synchronisation conveniences",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
        ],
    'requires': ['cs.seq', 'cs.excutils', 'cs.debug', 'cs.logutils', 'cs.obj', 'cs.queues', 'cs.py.func', 'cs.py3'],
}

from collections import namedtuple
from copy import copy
import inspect
from itertools import chain
import sys
import time
import threading
from threading import Semaphore, Condition, current_thread
from collections import deque
if sys.hexversion < 0x02060000: from sets import Set as set
from cs.seq import seq
from cs.excutils import transmute
from cs.debug import Lock, RLock, Thread
from cs.logutils import LogTime, error, warning, debug, exception, OBSOLETE, D
from cs.obj import O
from cs.queues import IterableQueue, Channel, NestingOpenCloseMixin, not_closed
from cs.py.func import funcname
from cs.py3 import raise3, Queue, PriorityQueue

class WorkerThreadPool(NestingOpenCloseMixin, O):
  ''' A pool of worker threads to run functions.
  '''

  def __init__(self, name=None):
    if name is None:
      name = "WorkerThreadPool-%d" % (seq(),)
    debug("WorkerThreadPool.__init__(name=%s)", name)
    self.name = name
    self._lock = Lock()
    O.__init__(self)
    NestingOpenCloseMixin.__init__(self)
    self.idle = deque()
    self.all = []

  def __str__(self):
    return "WorkerThreadPool:%s" % (self.name,)
  __repr__ = __str__

  def shutdown(self):
    ''' Shut down the pool.
        Close all the request queues.
        Join all the worker threads.
        It is an error to call close() more than once.
    '''
    for HT, HQ in self.all:
      HQ.close()
    for HT, HQ in self.all:
      if HT is not current_thread():
        HT.join()

  @not_closed
  def dispatch(self, func, retq=None, deliver=None, pfx=None, daemon=None):
    ''' Dispatch the callable `func` in a separate thread.
        On completion the result is the sequence:
          func_result, None, None, None
        On an exception the result is the sequence:
          None, exec_type, exc_value, exc_traceback
        If `retq` is not None, the result is .put() on retq.
        If `deliver` is not None, deliver(result) is called.
        If the parameter `pfx` is not None, submit pfx.partial(func);
          see the cs.logutils.Pfx.partial method for details.
    '''
    if self.closed:
      raise ValueError("%s: closed, but dispatch() called" % (self,))
    if pfx is not None:
      func = pfx.partial(func)
    idle = self.idle
    with self._lock:
      debug("dispatch: idle = %s", idle)
      if len(idle):
        # use an idle thread
        Hdesc = idle.pop()
        debug("dispatch: reuse %s", Hdesc)
      else:
        debug("dispatch: need new thread")
        # no available threads - make one
        args = []
        HT = Thread(target=self._handler, args=args, name=("%s:worker" % (self.name,)))
        ##HT.daemon = True
        RQ = IterableQueue(name="%s:IQ%d" % (self.name, seq()))
        Hdesc = (HT, RQ)
        self.all.append(Hdesc)
        args.append(Hdesc)
        if daemon is not None:
          debug("%s: new worker thread: set daemon=%s", self, daemon)
          HT.daemon = daemon
        debug("%s: start new worker thread (daemon=%s)", self, HT.daemon)
        HT.start()
      Hdesc[1].put( (func, retq, deliver) )

  def _handler(self, Hdesc):
    ''' The code run by each handler thread.
        Read a function `func`, return queue `retq` and delivery
        function `deliver` from the function queue.
        Run func().
        On completion the result is the sequence:
          func_result, None
        On an exception the result is the sequence:
          None, exc_info
        If retq is not None, the result is .put() on retq.
        If deliver is not None, deliver(result) is called.
        If both are None and an exception occurred, it gets raised.
    '''
    HT, RQ = Hdesc
    for func, retq, deliver in RQ:
      oname = HT.name
      HT.name = "%s:RUNNING:%s" % (oname, func)
      try:
        debug("%s: worker thread: running task...", self)
        result = func()
        debug("%s: worker thread: ran task: result = %s", self, result)
      except:
        result = None
        exc_info = sys.exc_info()
        log_func = exception if isinstance(exc_info[1], (TypeError, NameError, AttributeError)) else debug
        log_func("%s: worker thread: ran task: exception! %r", self, sys.exc_info())
        # don't let exceptions go unhandled
        # if nobody is watching, raise the exception and don't return
        # this handler to the pool
        if retq is None and deliver is None:
          error("%s: worker thread: reraise exception", self)
          raise3(*exc_info)
        debug("%s: worker thread: set result = (None, exc_info)", self)
      else:
        exc_info = None
      HT.name = oname
      func = None     # release func+args
      with self._lock:
        self.idle.append( Hdesc )
        ##D("_handler released thread: idle = %s", self.idle)
      tup = (result, exc_info)
      if retq is not None:
        debug("%s: worker thread: %r.put(%s)...", self, retq, tup)
        retq.put(tup)
        debug("%s: worker thread: %r.put(%s) done", self, retq, tup)
        retq = None
      if deliver is not None:
        debug("%s: worker thread: deliver %s...", self, tup)
        deliver(tup)
        debug("%s: worker thread: delivery done", self)
        deliver = None
      # forget stuff
      result = None
      exc_info = None
      tup = None
      debug("%s: worker thread: proceed to next function...", self)

class AdjustableSemaphore(object):
  ''' A semaphore whose value may be tuned after instantiation.
  '''

  def __init__(self, value=1, name="AdjustableSemaphore"):
    self.limit0 = value
    self.__sem = Semaphore(value)
    self.__value = value
    self.__name = name
    self.__lock = Lock()

  def __str__(self):
    return "%s[%d]" % (self.__name, self.limit0)

  def __enter__(self):
    with LogTime("%s(%d).__enter__: acquire", self.__name, self.__value):
      self.acquire()

  def __exit__(self,exc_type,exc_value,traceback):
    self.release()
    return False

  def release(self):
    self.__sem.release()

  def acquire(self, blocking=True):
    ''' The acquire() method calls the base acquire() method if not blocking.
        If blocking is true, the base acquire() is called inside a lock to
        avoid competing with a reducing adjust().
    '''
    if not blocking:
      return self.__sem.acquire(blocking)
    with self.__lock:
      self.__sem.acquire(blocking)
    return True

  def adjust(self, newvalue):
    ''' Set capacity to `newvalue` by calling release() or acquire() an appropriate number of times.
	If `newvalue` lowers the semaphore capacity then adjust()
	may block until the overcapacity is released.
    '''
    if newvalue <= 0:
      raise ValueError("invalid newvalue, should be > 0, got %s" % (newvalue,))
    self.adjust_delta(newvalue - self.__value)

  def adjust_delta(self, delta):
    ''' Adjust capacity by `delta` by calling release() or acquire() an appropriate number of times.
        If `delta` lowers the semaphore capacity then adjust() may block
        until the overcapacity is released.
    '''
    newvalue = self.__value + delta
    with self.__lock:
      if delta > 0:
        while delta > 0:
          self.__sem.release()
          delta -= 1
      else:
        while delta < 0:
          with LogTime("AdjustableSemaphore(%s): acquire excess capacity", self.__name):
            self.__sem.acquire(True)
          delta += 1
      self.__value = newvalue

''' A pool of Channels.
'''
__channels=[]
__channelsLock=Lock()
def getChannel():
  ''' Obtain a Channel object.
  '''
  with __channelsLock:
    if len(__channels) == 0:
      ch=_Channel()
      debug("getChannel: allocated %s" % ch)
    else:
      ch=__channels.pop(-1)
  return ch
def returnChannel(ch):
  debug("returnChannel: releasing %s" % ch)
  with __channelsLock:
    assert ch not in __channels
    __channels.append(ch)

''' A pool of _Q1 objects (single use Queue(1)s).
'''
__queues=[]
__queuesLock=Lock()
def Q1(name=None):
  ''' Obtain a _Q1 object (single use Queue(1), self disposing).
  '''
  with __queuesLock:
    if len(__queues) == 0:
      Q=_Q1(name=name)
    else:
      Q=__queues.pop(-1)
      Q._reset(name=name)
  return Q
def _returnQ1(Q):
  assert Q.empty()
  assert Q.didget
  assert Q.didput
  with __queuesLock:
    __queues.append(Q)

class _Q1(Queue):
  ''' Q _Q1 is a single use, resetable Queue(1).
      The Queue returns itself to a pool on get(), ready for reuse.
  '''
  def __init__(self,name=None):
    Queue.__init__(self,1)
    self._reset(name=name)
  def _reset(self,name):
    if name is None:
      name="Q1:%d" % id(self)
    self.name=name
    self.didput=False
    self.didget=False
  def __str__(self):
    return self.name
  def put(self,item):
    # racy (maybe not - GIL?) but it's just a sanity check
    assert not self.didput
    self.didput=True
    Queue.put(self,item)
  def get(self):
    assert not self.didget
    self.didget=True
    item=Queue.get(self)
    _returnQ1(self)
    return item

class Get1(O):
  ''' A single use storage container with a .get() method,
      so it looks like a Channel or a Q1.
      It is intended for functions with an asynchronous calling interface
      (i.e. a function that returns a "channel" from which to read the result)
      but synchronous internals - the result is obtained and wrapped in a
      Get1() for retrieval.
      Note: it does _not_ have any blocking behaviour or locks.
  '''
  def __init__(self, value):
    self.value = value
  def put(self, value):
    self.value = value
  def get(self):
    return self.value

class PreQueue(Queue):
  ''' A Queue with push-back and iteration.
      Bug: the push back doesn't play nice with pending get()s.
  '''
  def __init__(self,size=None):
    Queue.__init__(self,size)
    self.__preQ=[]
    self.__preQLock=Lock()
  def get(self,block=True,timeout=None):
    with self.__preQLock:
      if len(self.__preQ) > 0:
        return self.__preQ.pop(-1)
    return Queue.get(self,block,timeout)
  def next(self):
    return self.get()
  def unget(self,item):
    # TODO: if pending gets, call put()? How to tell?
    with self.__preQLock:
      self.__preQ.append(item)
  def __iter__(self):
    while True:
      yield self.get()

class DictMonitor(dict):
  def __init__(self,I={}):
    dict.__init__(self,I)
    self.__lock=Lock()
  def __getitem__(self,k):
    with self.__lock:
      v=dict.__getitem__(self,k)
    return v
  def __delitem__(self,k):
    with self.__lock:
      v=dict.__delitem__(self,k)
    return v
  def __setitem__(self,k,v):
    with self.__lock:
      dict.__setitem__(self,k,v)
  def keys(self):
    with self.__lock:
      ks = dict.keys(self)
    return ks

class FuncMultiQueue(object):
  def __init__(self, *a, **kw):
    raise Error("FuncMultiQueue OBSOLETE, use cs.later.Later instead")

def locked(func):
  ''' A decorator for monitor functions that must run within a lock.
      Relies upon a ._lock attribute for locking.
  '''
  def lockfunc(self, *a, **kw):
    if self._lock.acquire(0):
      self._lock.release()
    else:
      debug("@locked(self._lock=%r <%s>, func=%r)...", self._lock, self._lock.__class__, func)
    with self._lock:
      return func(self, *a, **kw)
  lockfunc.__name__ = "@locked(%s)" % (funcname(func),)
  return lockfunc

def locked_property(func, lock_name='_lock', prop_name=None, unset_object=None):
  ''' A thread safe property whose value is cached.
      The lock is taken if the value needs to computed.
  '''
  if prop_name is None:
    prop_name = '_' + func.__name__
  @transmute(AttributeError)
  def getprop(self):
    ''' Attempt lockless fetch of property first.
        Use lock if property is unset.
    '''
    p = getattr(self, prop_name, unset_object)
    if p is unset_object:
      try:
        lock = getattr(self, lock_name)
      except AttributeError as e:
        error("no .%s attribute", lock_name)
        raise
      with lock:
        p = getattr(self, prop_name, unset_object)
        if p is unset_object:
          ##debug("compute %s...", prop_name)
          p = func(self)
          setattr(self, prop_name, p)
        else:
          ##debug("inside lock, already computed %s", prop_name)
          pass
    else:
      ##debug("outside lock, already computed %s", prop_name)
      pass
    return p
  return property(getprop)

def via(cmanager, func, *a, **kw):
  ''' Return a callable that calls the supplied `func` inside a
      with statement using the context manager `cmanager`.
      This intended use case is aimed at deferred function calls.
  '''
  def f():
    with cmanager:
      return func(*a, **kw)
  return f

if __name__ == '__main__':
  import cs.threads_tests
  cs.threads_tests.selftest(sys.argv)
