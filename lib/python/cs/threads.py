#!/usr/bin/python
#
# Thread convenience facilities.
#       - Cameron Simpson <cs@zip.com.au> 18nov2007
#

from __future__ import with_statement
from collections import namedtuple
from copy import copy
from functools import partial
import inspect
from itertools import chain
import sys
import time
import threading
from threading import Semaphore, Timer, Condition
from collections import deque
if sys.hexversion < 0x02060000: from sets import Set as set
from cs.seq import seq
from cs.excutils import transmute
import logging
from cs.debug import Lock, RLock, Thread
import cs.logutils
from cs.logutils import Pfx, LogTime, error, warning, debug, exception, OBSOLETE, D
from cs.obj import O
from cs.asynchron import report
from cs.queues import IterableQueue, Channel, NestingOpenCloseMixin
from cs.py3 import raise3, Queue, PriorityQueue

class WorkerThreadPool(NestingOpenCloseMixin, O):
  ''' A pool of worker threads to run functions.
  '''

  def __init__(self, name=None, open=False):
    if name is None:
      name = "WorkerThreadPool-%d" % (seq(),)
    debug("WorkerThreadPool.__init__(name=%s)", name)
    self.name = name
    self._lock = Lock()
    O.__init__(self)
    NestingOpenCloseMixin.__init__(self, open=open)
    self.idle = deque()
    self.all = []

  def __repr__(self):
    return '<WorkerThreadPool "%s">' % (self.name,)

  def shutdown(self):
    ''' Shut down the pool.
        Close all the request queues.
        Join all the worker threads.
        It is an error to call close() more than once.
    '''
    for H, HQ in self.all:
      HQ.close()
    for H, HQ in self.all:
      H.join()

  def dispatch(self, func, retq=None, deliver=None, pfx=None):
    ''' Dispatch the callable `func` in a separate thread.
        On completion the result is the sequence:
          func_result, None, None, None
        On an exception the result is the sequence:
          None, exec_type, exc_value, exc_traceback
        If `retq` is not None, the result is .put() on retq.
        If `deliver` is not None, deliver(result) is called.
        If the parameter `pfx` is not None, submit pfx.func(func);
          see cs.logutils.Pfx's .func method for details.
    '''
    if self.closed:
      raise ValueError("%s: closed, but dispatch() called" % (self,))
    if pfx is not None:
      func = pfx.func(func)
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
        H = Thread(target=self._handler, args=args)
        H.daemon = True
        Hdesc = (H, IterableQueue(name="%s:IQ%d" % (self.name, seq()), open=True))
        self.all.append(Hdesc)
        args.append(Hdesc)
        debug("%s: start new worker thread", self)
        H.start()
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
    debug("%s: worker thread starting", self)
    reqQ = Hdesc[1]
    for func, retq, deliver in reqQ:
      debug("%s: worker thread: received task", self)
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
          debug("%s: worker thread: reraise exception", self)
          raise3(*exc_info)
        debug("%s: worker thread: set result = (None, exc_info)", self)
      else:
        exc_info = None
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
    ''' The adjust(newvalue) method calls release() or acquire() an
        appropriate number of times.  If newvalue lowers the semaphore
        capacity then adjust() may block until the overcapacity is
        released.
    '''
    if newvalue <= 0:
      raise ValueError("invalid newvalue, should be > 0, got %s" % (newvalue,))
    with self.__lock:
      delta = newvalue-self.__value
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

class JobCounter:
  ''' A class to count and wait for outstanding jobs.
      As jobs are queued, JobCounter.inc() is called.
      When everything is dispatched, calling JobCounter.whenDone()
      queues a function to execute on completion of all the jobs.
  '''
  def __init__(self,name):
    self.__name=name
    self.__lock=Lock()
    self.__sem=Semaphore(0)
    self.__n=0
    self.__onDone=None

  def inc(self):
    ''' Note that there is another job for which to wait.
    '''
    debug("%s: inc()" % self.__name)
    with self.__lock:
      self.__n+=1

  def dec(self):
    ''' Report the completion of a job.
    '''
    debug("%s: dec()" % self.__name)
    self.__sem.release()

  def _wait1(self):
    ''' Wait for a single job to complete.
        Return False if no jobs remain.
        Report True if a job remained and we waited.
    '''
    debug("%s: wait1()..." % self.__name)
    with self.__lock:
      if self.__n == 0:
        debug("%s: wait1(): nothing to wait for" % self.__name)
        return False
    self.__sem.acquire()
    with self.__lock:
      self.__n-=1
    debug("%s: wait1(): waited" % self.__name)
    return True

  def _waitAll(self):
    while self._wait1():
      pass

  def _waitThenDo(self,*args,**kw):
    debug("%s: _waitThenDo()..." % self.__name)
    self._waitAll()
    debug("%s: _waitThenDo(): waited: calling __onDone()..." % self.__name)
    return self.__onDone[0](*self.__onDone[1],**self.__onDone[2])

  def doInstead(self,func,*args,**kw):
    with self.__lock:
      assert self.__onDone is not None
      self.__onDone=(func,args,kw)

  def whenDone(self,func,*args,**kw):
    ''' Queue an action to occur when the jobs are done.
    '''
    with self.__lock:
      assert self.__onDone is None
      self.__onDone=(func,args,kw)
      Thread(target=self._waitThenDo,args=args,kwargs=kw).start()

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

class TimerQueue(object):
  ''' Class to run a lot of "in the future" jobs without using a bazillion
      Timer threads.
  '''
  def __init__(self, name=None):
    if name is None:
      name = 'TimerQueue-%d' % (seq(),)
    self.name = name
    self.Q = PriorityQueue()    # queue of waiting jobs
    self.pending = None         # or (Timer, when, func)
    self.closed = False
    self._lock = Lock()
    self.mainRunning = False
    self.mainThread = Thread(target=self._main)
    self.mainThread.start()

  def __str__(self):
    return self.name

  def close(self, cancel=False):
    ''' Close the TimerQueue. This forbids further job submissions.
        If `cancel` is supplied and true, cancel all pending jobs.
	Note: it is still necessary to call TimerQueue.join() to
	wait for all pending jobs.
    '''
    self.closed = True
    if self.Q.empty():
      # dummy entry to wake up the main loop
      self.Q.put( (None, None, None) )
    if cancel:
      self._cancel()

  def _cancel(self):
    with self._lock:
      if self.pending:
        T, Twhen, Tfunc = self.pending
        self.pending[2] = None
        self.pending = None
        T.cancel()
      else:
        Twhen, Tfunc = None, None
    return Twhen, Tfunc

  def add(self, when, func):
    ''' Queue a new job to be called at 'when'.
        'func' is the job function, typically made with functools.partial.
    '''
    assert not self.closed, "add() on closed TimerQueue"
    self.Q.put( (when, seq(), func) )

  def join(self):
    ''' Wait for the main loop thread to finish.
    '''
    assert self.mainThread is not None, "no main thread to join"
    self.mainThread.join()

  def _main(self):
    ''' Main loop:
        Pull requests off the queue; they will come off in time order,
        so we always get the most urgent item.
        If we're already delayed waiting for a previous request,
          halt that request's timer and compare it with the new job; push the
          later request back onto the queue and proceed with the more urgent
          one.
        If it should run now, run it.
        Otherwise start a Timer to run it later.
        The loop continues processing items until the TimerQueue is closed.
    '''
    with Pfx("TimerQueue._main()"):
      assert not self.mainRunning, "main loop already active"
      self.mainRunning = True
      while not self.closed:
        when, n, func = self.Q.get()
        debug("got when=%s, n=%s, func=%s", when, n, func)
        if when is None:
          # it should be the dummy item
          assert self.closed
          assert self.Q.empty()
          break
        with self._lock:
          if self.pending:
            # Cancel the pending Timer
            # and choose between the new job and the job the Timer served.
            # Requeue the lesser job and do or delay-via-Timer the more
            # urgent one.
            T, Twhen, Tfunc = self.pending
            self.pending[2] = None  # prevent the function from running if racy
            T.cancel()
            self.pending = None     # nothing pending now
            T = None                # let go of the cancelled timer
            if when < Twhen:
              # push the pending function back onto the queue, but ahead of
              # later-queued funcs with the same timestamp
              requeue = (Twhen, 0, Tfunc)
            else:
              # push the dequeued function back - we prefer the pending one
              requeue = (when, n, func)
              when = Twhen
              func = Tfunc
            self.Q.put(requeue)
          # post: self.pending is None and the Timer is cancelled
          assert self.pending is None

        now = time.time()
        delay = when - now
        if delay <= 0:
          # function due now - run it
          try:
            retval = func()
          except:
            exception("func %s threw exception", func)
          else:
            debug("func %s returns %s", func, retval)
        else:
          # function due later - run it from a Timer
          def doit(self):
            # pull off our pending task and untick it
            Tfunc = None
            with self._lock:
              if self.pending:
                T, Twhen, Tfunc = self.pending
              self.pending = None
            # run it if we haven't been told not to
            if Tfunc:
              try:
                retval = Tfunc()
              except:
                exception("func %s threw exception", Tfunc)
              else:
                debug("func %s returns %s", Tfunc, retval)
          with self._lock:
            T = Timer(delay, partial(doit, self))
            self.pending = [ T, when, func ]
            T.start()
      self.mainRunning = False

class FuncMultiQueue(object):
  def __init__(self, *a, **kw):
    raise Error("FuncMultiQueue OBSOLETE, use cs.later.Later instead")

def locked(func):
  ''' A decorator for monitor functions that must run within a lock.
      Relies upon a ._lock attribute for locking.
  '''
  def lockfunc(self, *a, **kw):
    with self._lock:
      return func(self, *a, **kw)
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
      with getattr(self, lock_name):
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

_RunTreeOp = namedtuple('RunTreeOp', 'func fork_input fork_state')

RUN_TREE_OP_MANY_TO_MANY = 0
RUN_TREE_OP_ONE_TO_MANY = 1
RUN_TREE_OP_ONE_TO_ONE = 2
RUN_TREE_OP_SELECT = 3

def _conv_one_to_many(func):
  def converted(items, *a, **kw):
    for item in items:
      for result in func(item, *a, **kw):
        yield result
  return converted

def _conv_one_to_one(func):
  ''' Convert a one-to-one function to a many to many.
  '''
  def converted(items, *a, **kw):
    results = []
    for item in items:
      yield func(item, *a, **kw)
  return converted

def _conv_select(func):
  ''' Convert a test-one function to one-to-many.
  '''
  def converted(items, *a, **kw):
    for item in items:
      if func(item, *a, **kw):
        yield item
  return converted

def RunTreeOp(func, fork_input, fork_state, func_sig=None):
  if func_sig is None:
    func_sig = RUN_TREE_OP_MANY_TO_MANY

  ok = True

  if func is not None and not callable(func):
    error("RunTreeOp: bad func: %r", func)
    ok = False
  if func_sig == RUN_TREE_OP_MANY_TO_MANY:
    pass
  elif func_sig == RUN_TREE_OP_ONE_TO_MANY:
    func = _conv_one_to_many(func)
  elif func_sig == RUN_TREE_OP_ONE_TO_ONE:
    func = _conv_one_to_one(func)
  elif func_sig == RUN_TREE_OP_SELECT:
    func = _conv_select(func)
  else:
    error("RunTreeOp: invalid function signature")
    ok = False

  if not ok:
    raise ValueError("invalid RunTreeOp() call")

  return _RunTreeOp(func, fork_input, fork_state)

def runTree(input, operators, state, funcQ):
  ''' Descend an operation tree expressed as:
        `input`: an input object
        `operators`: an iterable of RunTreeOp instances
	  NB: if an item of the iterator is callable, presume it
              to be a bare function and convert it to
                RunTreeOp(func, False, False).
        `state`: a state object to assist `func`.
        `funcQ`: a cs.later.Later function queue to dispatch functions,
                 or equivalent
      Returns the final output.
      This is the core algorithm underneath the cs.app.pilfer operation.

      Each operator `op` has the following attributes:
            op.func     A  many-to-many function taking a iterable of input
			items and returning an iterable of output
			items with the signature:
                          func(inputs, state)
            op.fork_input
			Submit distinct calls to func( (item,), ...) for each
                        item in input instead of passing input to a single call.
                        `func` is still a many-to-many in signature but is
                        handed 1-tuples of the input items to allow parallel
                        execution.
            op.fork_state
                        Make a copy of the state object for this and
                        subsequent operations instead of using the original.
  '''
  return runTree_inner(input, iter(operators), state, funcQ).get()

def runTree_inner(input, ops, state, funcQ, retq=None):
  ''' Submit function calls to evaluate `func` as specified.
      Return a LateFunction to collect the final result.
      `func` is a many-to-many function.
  '''
  debug("runTree_inner(input=%s, ops=%s, state=%s, funcQ=%s, retq=%s)...",
    input, ops, state, funcQ, retq)

  try:
    op = next(ops)
  except StopIteration:
    if retq is None:
      # initial runTree_inner: return a gettable
      return Get1(input)
    retq.put(input)
    # redundant return of retq for consistency
    return retq

  # prepare a Channel for the result if not yet set
  if retq is None:
    retq = Channel()

  func, fork_input, fork_state = op.func, op.fork_input, op.fork_state
  if fork_input:
    # iterable of single item iterables
    inputs = ( (item,) for item in input )
  else:
    # 1-iterable of all-items iterables
    inputs = (input,)

  LFs = []
  for input in inputs:
    substate = copy(state) if fork_state else state
    def submit_func(func, input, substate):
      return list(func(input, substate))
    LF = funcQ.defer(submit_func, func, input, substate)
    LFs.append(LF)

  # Now submit a function to collect the results.
  # Each result is a list, courtesy of the submit_func wrapper above.
  # Winnow the empty results, and discard remain ops if there are
  # no overall results.
  # If there are no more ops, put the outputs onto the retq.
  def collate_and_requeue(LFs, ops):
    with Pfx("collate_and_requeue %d LFs", len(LFs)):
      debug("LFs=%s", LFs)
      results = []
      for LF in report(LFs):
        result, exc_info = LF.join()
        if exc_info:
          exception("exception: %r", exc_info)
          import traceback
          traceback.print_tb(exc_info[2], None, sys.stderr)
        elif result:
          results.append(result)
        else:
          debug("empty result, discarding")
      if not results:
        # short circuit if the result set becomes empty
        debug("no results, discarding further ops: %s", list(ops))
        ops = iter(())
      # resubmit with new state etc
      debug("func_get: queue another call to runTree_inner")
      funcQ.defer(runTree_inner, chain(*results), ops, state, funcQ, retq)

  funcQ.defer(collate_and_requeue, LFs, ops)

  # the first runTree_inner gets to return the retq for result collection
  return retq

if __name__ == '__main__':
  import cs.threads_tests
  cs.threads_tests.selftest(sys.argv)
