#!/usr/bin/python
#
# Thread convenience facilities.
#       - Cameron Simpson <cs@zip.com.au> 18nov2007
#

from __future__ import with_statement
from collections import namedtuple
from copy import copy
from functools import partial
import sys
import time
from threading import Lock
from threading import Semaphore, Thread, Timer
if sys.hexversion < 0x03000000:
  from Queue import Queue, PriorityQueue, Full, Empty
else:
  from queue import Queue, PriorityQueue, Full, Empty
from collections import deque
if sys.hexversion < 0x02060000: from sets import Set as set
from cs.misc import seq
from cs.excutils import transmute
from cs.logutils import Pfx, LogTime, error, warning, debug, exception, OBSOLETE, D
from cs.misc import seq

class WorkerThreadPool(object):
  ''' A pool of worker threads to run functions.
  '''

  def __init__(self, name=None):
    if name is None:
      name = "WorkerThreadPool-%d" % (seq(),)
    debug("WorkerThreadPool.__init__(name=%s)", name)
    self.name = name
    self.closed = False
    self.idle = deque()
    self.all = []
    self._lock = Lock()

  def __str__(self):
    return self.name

  def __repr__(self):
    return '<WorkerThreadPool "%s">' % (self.name,)

  def close(self):
    ''' Close the pool.
        Close all the request queues.
        Join all the worker threads.
        It is an error to call close() more than once.
    '''
    if self.closed:
      warning("%s: repeated close", self)
    self.closed = True
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
        Hdesc = (H, IterableQueue())
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
        result = func(), None
        debug("%s: worker thread: ran task: result = %s", self, result)
      except:
        func = None     # release func+args
        debug("%s: worker thread: ran task: exception! %r", self, sys.exc_info())
        # don't let exceptions go unhandled
        # if nobody is watching, raise the exception and don't return
        # this handler to the pool
        if retq is None and deliver is None:
          t, v, tb = sys.exc_info()
          debug("%s: worker thread: reraise exception", self)
          raise t, v, tb
          ##raise t(v).with_traceback(tb)
        debug("%s: worker thread: set result = (None, exc_info)", self)
        result = (None, sys.exc_info())
      finally:
        func = None     # release func+args
      with self._lock:
        self.idle.append( Hdesc )
        ##D("_handler released thread: idle = %s", self.idle)
      if retq is not None:
        debug("%s: worker thread: %r.put(result)...", self, retq)
        retq.put(result)
        debug("%s: worker thread: %r.put(result) done", self, retq)
        retq = None
      if deliver is not None:
        debug("%s: worker thread: deliver result...", self)
        deliver(result)
        debug("%s: worker thread: delivery done", self)
        deliver = None
      result = None
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

class Channel(object):
  ''' A zero-storage data passage.
      Unlike a Queue(1), put() blocks waiting for the matching get().
  '''
  def __init__(self):
    self.__readable = Lock()
    self.__readable.acquire()
    self.__writable = Lock()
    self.__writable.acquire()
    self.__get_lock = Lock()
    self.__put_lock = Lock()
    self.closed = False
    self.__lock = Lock()
    self._nreaders = 0

  def __str__(self):
    if self.__readable.locked():
      if self.__writable.locked():
        state="idle"
      else:
        state="get blocked waiting for put"
    else:
      if self.__writable.locked():
        state="put just happened, get imminent"
      else:
        state="ERROR"
    return "<cs.threads.Channel %s>" % (state,)

  def __call__(self, *a):
    ''' Call the Channel.
        With no arguments, do a .get().
        With an argument, do a .put().
    '''
    if a:
      return self.put(*a)
    return self.get()

  def get(self):
    ''' Read a value from the Channel.
        Blocks until someone put()s to the Channel.
    '''
    debug("CHANNEL: %s.get()", self)
    if self.closed:
      raise ValueError("%s.get() on closed Channel" % (self,))
    with self.__get_lock:
      with self.__lock:
        self._nreaders += 1
      self.__writable.release()   # allow someone to write
      self.__readable.acquire()   # await writer and prevent other readers
      value = self._value
      delattr(self,'_value')
      with self.__lock:
        self._nreaders -= 1
    debug("CHANNEL: %s.get() got %r", self, value)
    return value

  def put(self, value):
    ''' Write a value to the Channel.
        Blocks until a corresponding get() occurs.
    '''
    debug("CHANNEL: %s.put(%r)", self, value)
    if self.closed:
      raise ValueError("%s: closed, but put(%s)" % (self, value))
    with self.__put_lock:
      self.__writable.acquire()   # prevent other writers
      self._value = value
      self.__readable.release()   # allow a reader
    debug("CHANNEL: %s.put(%r) completed", self, value)

  def close(self):
    with self.__lock:
      if self.closed:
        warning("%s: .close() of closed Channel" % (self,))
      else:
        self.closed = True
    with self.__lock:
      nr = self._nreaders
    for i in range(nr):
      self.put(None)

  def __iter__(self):
    ''' Iterator for consumers that operate on tasks arriving via this Channel.
    '''
    while not self.closed:
      item = self.get()
      if item is None and self.closed:
        break
      yield item

##def call(self,value,backChannel):
##  ''' Asynchronous call to daemon via channel.
##      Daemon should reply by .put()ing to the backChannel.
##  '''
##  self.put((value,backChannel))
##
##def call_s(self,value):
##  ''' Synchronous call to daemon via channel.
##  '''
##  ch=getChannel()
##  self.call(value,ch)
##  result=ch.get()
##  returnChannel(ch)
##  return result

class IterableQueue(Queue):
  ''' A Queue implementing the iterator protocol.
      Note: Iteration stops when a None comes off the Queue.
      TODO: supply sentinel item, default None.
  '''

  sentinel = object()

  def __init__(self, *args, **kw):
    ''' Initialise the queue.
    '''
    Queue.__init__(self, *args, **kw)
    self.closed = False

  def get(self, *a):
    item = Queue.get(self, *a)
    if item is self.sentinel:
      Queue.put(self, self.sentinel)
      raise Empty
    return item

  def get_nowait(self):
    item = Queue.get_nowait(self)
    if item is self.sentinel:
      Queue.put(self, self.sentinel)
      raise Empty
    return item

  def put(self, item, *args, **kw):
    ''' Put an item on the queue.
    '''
    if self.closed:
      raise Full("put() on closed IterableQueue")
    if item is self.sentinel:
      raise ValueError("put(sentinel) on IterableQueue")
    return Queue.put(self, item, *args, **kw)

  def _closeAtExit(self):
    if not self.closed:
      self.close()

  def close(self):
    if self.closed:
      error("close() on closed IterableQueue")
    else:
      self.closed = True
      Queue.put(self, self.sentinel)

  def __iter__(self):
    ''' Iterable interface for the queue.
    '''
    return self

  def next(self):
    try:
      item = self.get()
    except Empty:
      raise StopIteration
    return item

class IterablePriorityQueue(PriorityQueue):
  ''' A PriorityQueue implementing the iterator protocol.
      Note: Iteration stops when a None comes off the Queue.
      TODO: supply sentinel item, default None.
  '''

  def __init__(self, *args, **kw):
    ''' Initialise the queue.
    '''
    PriorityQueue.__init__(self, *args, **kw)
    self.closed=False

  def put(self, item, *args, **kw):
    ''' Put an item on the queue.
    '''
    assert not self.closed, "put() on closed IterableQueue"
    assert item is not None, "put(None) on IterableQueue"
    return PriorityQueue.put(self, item, *args, **kw)

  def _closeAtExit(self):
    if not self.closed:
      self.close()

  def close(self):
    if self.closed:
      error("close() on closed IterableQueue")
    else:
      self.closed=True
      PriorityQueue.put(self,None)

  def __iter__(self):
    ''' Iterable interface for the queue.
    '''
    return self

  def next(self):
    item=self.get()
    if item is None:
      PriorityQueue.put(self,None)      # for another iterator
      raise StopIteration
    return item

class Cato9:
  ''' A cat-o-nine-tails Queue-like object, fanning out put() items
      to an arbitrary number of handlers.
  '''
  @OBSOLETE
  def __init__(self,*args,**kwargs):
    self.__qs={}
    self.__q=IterableQueue(maxsize)
    self.__lock=Lock()
    self.__closed=False
    Thread(target=self.__handle).start()
  def qsize(self):
    return self.__q.qsize()
  def put(self,item,block=True,timeout=None):
    assert not self.__closed
    self.__q.put(item,block,timeout)
  def put_nowait(self,item):
    assert not self.__closed
    self.__q.put_nowait(item)
  def close(self):
    ''' Close the queue.
    '''
    self.__closed=True
    with self.__lock:
      qs=self.__qs
      self.__qs={}
    for k in qs.keys():
      qs[k].close()
  def __handle(self):
    for item in self.__q:
      with self.__lock:
        for k in self.__qs.keys():
          self.__qs[k].put(item)
  def addHandler(self,handler):
    ''' Add a handler function to the queue, returning an identifying token.
        The handler will be called with each put() item.
    '''
    assert not self.__closed
    tok=seq()
    IQ=IterableQueue(1)
    with self.__lock:
      self.__qs[tok]=IQ
    Thread(target=self.__handler,args=(IQ,handler)).start()
    return tok
  def __handler(self,IQ,handler):
    for item in IQ:
      handler(item)
  def removeHandler(self,tok):
    ''' Remove the handler corresponding to the supplied token.
    '''
    with self.__lock:
      Q=self.__qs.pop(tok,None)
    if Q is not None:
      Q.close()

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

class NestingOpenClose(object):
  ''' A context manager class to assist with with-statement based
      automatic shutdown.
      A count of active open()s is kept, and on the last close()
      the object's .shutdown() method is called.
      Use via the with-statement calls open()/close() for __enter__()
      and __exit__().
      Multithread safe.
  '''
  def __init__(self):
    self.__count=0
    self.__lock=Lock()

  def open(self):
    ''' Increment the open count.
    '''
    with self.__lock:
      self.__count+=1
    return self

  def __enter__(self):
    self.open()

  def close(self):
    ''' Decrement the open count.
        If the count goes to zero, call self.shutdown().
    '''
    if self.__count != 0:
      with self.__lock:
        count = self.__count
        assert count > 0, "self.count (%s) <= 0" % (count,)
        count -= 1
        if count == 0:
          self.shutdown()
        self.__count = count

  def __exit__(self, exc_type, exc_value, traceback):
    self.close()
    return False

class FuncQueue(NestingOpenClose):
  ''' A Queue of function calls to make, processed serially.
      New functions queued as .put((func,args)) or as .qfunc(func,args...).
      Queue shut down with .close().
  '''
  def __init__(self,size=None,parallelism=1):
    NestingOpenClose.__init__(self)
    assert parallelism > 0
    self.__Q=IterableQueue(size)
    self.__closing=False
    self.__threads=[]
    for n in range(parallelism):
      T=Thread(target=self.__runQueue)
      T.start()
      self.__threads.append(T)

  def __runQueue(self):
    ''' A thread to process queue items serially.
        This exists to permit easy or default implementation of the
        cs.venti.store *_a() methods, and is better suited to fast
        low latency stores.
        A highly parallel or high latency store will want its own
        thread scheme to manage multiple *_a() operations.
    '''
    self.open()
    for retQ, func, args, kwargs in self.__Q:
      ##debug("func=%s, args=%s, kwargs=%s"%(func,args,kwargs))
      ret=func(*args,**kwargs)
      if retQ is not None:
        retQ.put(ret)
    self.close()

  def shutdown(self):
    self.__Q.close()

  def join(self):
    for T in self.__threads:
      T.join()

  def callback(self,retQ,func,args=(),kwargs=None):
    ''' Queue a function for dispatch.
        If retQ is not None, the function return value will be .put() on retQ.
    '''
    assert not self.__closing
    if kwargs is None:
      kwargs={}
    else:
      assert type(kwargs) is dict
    if retQ is None: retQ=Q1()
    self.__Q.put((retQ,func,args,kwargs))

  def callbg(self,func,args=(),kwargs=None):
    ''' Asynchronously call the supplied func via the FuncQueue.
        Returns a Q1 from which the result may be .get().
    '''
    retQ=Q1()
    self.callback(retQ,func,args,kwargs)
    return retQ

  def call(self,func,args=(),kwargs=None):
    ''' Synchronously call the supplied func via the FuncQueue.
        Return the function result.
    '''
    return self.callbg(func,args,kwargs).get()

  def dispatch(self,func,args=(),kwargs=None):
    ''' Asynchronously call the supplied func via the FuncQueue.
    '''
    self.callback(None,func,args,kwargs)

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

class Get1:
  ''' A single use storage container with a .get() method,
      so it looks like a Channel or a Q1.
      It is intended for functions with an asynchronous calling interface
      (i.e. a function that returns a "channel" from which to read the result)
      but synchronous internals - the result is obtained and wrapped in a
      Get1() for retrieval.
  '''
  def __init__(self,value):
    self.__value=value
  def put(self,value):
    self.__value=value
  def get(self):
    return self.__value

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

def locked_property(func, lock_name='_lock', prop_name=None, unset_object=None):
  ''' A property whose access is controlled by a lock if unset.
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
          ##warning("compute %s[%s].%s: %s", self, id(self), prop_name, type(p))
          setattr(self, prop_name, p)
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

def RunTreeOp(func, fork_input, fork_state):
  ok = True
  if func is not None and not callable(func):
    error("RunTreeOp: bad func: %r", func)
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
  if retq is None:
    retq = Channel()

  try:
    op = ops.next()
  except StopIteration:
    funcQ.defer(lambda: retq.put(input))
    return retq

  func, fork_input, fork_state = op.func, op.fork_input, op.fork_state
  if fork_input:
    # iterable of single item iterables
    inputs = ( (item,) for item in input )
  else:
    # 1-iterable of all-items iterables
    inputs = (input,)

  # submit function calls that put results onto a Queue
  Q = Queue()
  def func_put(Q, input, state):
    ''' Call `func` with `input` and `state`.
        Put results onto the Queue for collection.
    '''
    debug("func_put: call func...")
    results = func(input, state)
    debug("func_put: called func, got: %s", results)
    Q.put(results)
  nfuncs = 0
  for input in inputs:
    substate = copy(state) if fork_state else state
    funcQ.defer(func_put, Q, input, substate)
    nfuncs += 1

  # now submit a function to collect the results
  # if there are no more ops, put the outputs onto the retq
  def func_get(Q, nfuncs):
    debug("func_get(Q, nfuncs=%d)...", nfuncs)
    outputs = []
    while nfuncs > 0:
      debug("func_get: %d results to collect, Q.get()...", nfuncs)
      results = Q.get()
      debug("func_get: Q.get() got: %s", results)
      outputs.extend(results)
      nfuncs -= 1
    Q = None
    # resubmit with new state etc
    debug("func_get: queue another call to runTree_inner")
    funcQ.defer(runTree_inner, outputs, ops, state, funcQ, retq)
  funcQ.defer(func_get, Q, nfuncs)
  Q = None

  return retq

if __name__ == '__main__':
  import cs.threads_tests
  cs.threads_tests.selftest(sys.argv)
