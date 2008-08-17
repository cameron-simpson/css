#!/usr/bin/python
#
# Thread convenience facilities.
#       - Cameron Simpson <cs@zip.com.au> 18nov2007
#

from __future__ import with_statement
import sys
from thread import allocate_lock
from threading import Semaphore, Thread
from Queue import Queue
from collections import deque
from cs.misc import seq, debug, isdebug, tb, cmderr, warn, reportElapsedTime, logFnLine

class AdjustableSemaphore:
  ''' A semaphore whose value may be tuned after instantiation.
  '''
  def __init__(self, value=1, name="AdjustableSemaphore"):
    self.__sem=Semaphore(value)
    self.__value=value
    self.__name=name
    self.__lock=allocate_lock()
  def __enter__(self):
    return reportElapsedTime("%s(%d).__enter__: acquire" \
                               % (self.__name,self.__value),
                             self.acquire)
  def __exit__(self,exc_type,exc_value,traceback):
    self.release()
    return False
  def release(self):
    self.__sem.release()
  def acquire(self,blocking=True):
    ''' The acquire() method calls the base acquire() method if not blocking.
        If blocking is true, the base acquire() is called inside a lock to
        avoid competing with a reducing adjust().
    '''
    if not blocking:
      return self.__sem.acquire(blocking)
    with self.__lock:
      self.__sem.acquire(blocking)
    return True
  def adjust(self,newvalue):
    ''' The adjust(newvalue) method calls release() or acquire() an
        appropriate number of times.  If newvalue lowers the semaphore
        capacity then adjust() may block until the overcapacity is
        released.
    '''
    assert newvalue > 0
    with self.__lock:
      delta=newvalue-self.__value
      if delta > 0:
        while delta > 0:
          self.__sem.release()
          delta-=1
      else:
        while delta < 0:
          reportElapsedTime("AdjustableSemaphore(%s): acquire excess capacity"%self.__name,self.__sem.acquire,True)
          delta+=1
      self.__value=newvalue

class Channel:
  ''' A zero-storage data passage.
  '''
  def __init__(self):
    self.__readable=allocate_lock()
    self.__readable.acquire()
    self.__writable=allocate_lock()
    self.__writable.acquire()

  def get(self):
    ''' Read a value from the Channel.
        Blocks until someone writes to the Channel.
    '''
    self.__writable.release()
    self.__readable.acquire()
    value=self._value
    delattr(self,'_value')
    return value

  def put(self,value):
    ''' Write a value to the Channel.
        Blocks until a corresponding read occurs.
    '''
    self.__writable.acquire()
    self._value=value
    self.__readable.release()

  def __iter__(self):
    ''' Iterator for daemons that operate on tasks arriving via this Channel.
    '''
    while True:
      yield self.get()

  def call(self,value,backChannel):
    ''' Asynchronous call to daemon via channel.
        Daemon should reply by .put()ing to the backChannel.
    '''
    self.put((value,backChannel))

  def call_s(self,value):
    ''' Synchronous call to daemon via channel.
    '''
    ch=getChannel()
    self.call(value,ch)
    result=ch.get()
    returnChannel(ch)
    return result

class IterableQueue(Queue): 
  ''' A Queue obeying the iterator protocol.
      Note: Iteration stops when a None comes off the Queue.
  '''
  def __init__(self,*args,**kw):
    Queue.__init__(self,*args,**kw)
    self.__closed=False
  def put(self,item,*args,**kw):
    assert not self.__closed, "put() on closed IterableQueue"
    assert item is not None, "put(None) on IterableQueue"
    return Queue.put(self,item,*args,**kw)
  def _closeAtExit(self):
    if not self.__closed:
      self.close()
  def close(self):
    ##logFnLine("%s.close()"%(self,),frame=sys._getframe(1))
    if self.__closed:
      logFnLine("close() on closed IterableQueue")
    else:
      self.__closed=True
      Queue.put(self,None)
  def __iter__(self):
    return self
  def next(self):
    item=self.get()
    if item is None:
      Queue.put(self,None)      # for another iterator
      raise StopIteration
    return item

class Cato9:
  ''' A cat-o-nine-tails Queue-like object, fanning out put() items
      to an arbitrary number of handlers.
  '''
  def __init__(self,*args,**kwargs):
    self.__qs={}
    self.__q=IterableQueue(maxsize)
    self.__lock=allocate_lock()
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
    self.__lock=allocate_lock()
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

class FuncQueue:
  ''' A Queue of function calls to make, processed serially.
      New functions queued as .put((func,args)) or as .qfunc(func,args...).
      Queue shut down with .close().
  '''
  def __init__(self,size=None,parallelism=1):
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
    for retQ, func, args, kwargs in self.__Q:
      ##debug("func=%s, args=%s, kwargs=%s"%(func,args,kwargs))
      ret=func(*args,**kwargs)
      if retQ is not None:
        retQ.put(ret)

  def close(self,dojoin=False):
    self.__Q.close()
    if dojoin:
      self.join()

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
__channelsLock=allocate_lock()
def getChannel():
  ''' Obtain a Channel object.
  '''
  with __channelsLock:
    if len(__channels) == 0:
      ch=_Channel()
      if isdebug: warn("getChannel: allocated %s" % ch)
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
__queuesLock=allocate_lock()
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
      if isdebug:
        import traceback
        name="Q1:[%s]" % (traceback.format_list(traceback.extract_stack()[-3:-1])[0].strip().replace("\n",", "))
      else:
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
    self.__preQLock=allocate_lock()
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

__nullCH=None
__nullCHlock=allocate_lock()
def nullCH():
  with __nullCHlock:
    if __nullCH is None:
      __nullCH=NullCH()
  return __nullCH
class NullCH(Queue):
  def __init__(self):
    Queue.__init__(self,8)
    self.__closing=False
    sink=Thread(target=self.__runQueue)
    sink.setDaemon(True)
    sink.start()
  def __runQueue(self):
    while True:
      self.get()

class DictMonitor(dict):
  def __init__(self,I={}):
    dict.__init__(self,I)
    self.__lock=allocate_lock()
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

def bgCall(func,args,ch=None):
  ''' Spawn a thread to call the supplied function with the supplied
      args. Return a channel on which the function return value may be read. 
      A channel may be supplied by the caller; if not then the returned
      channel must be release with returnChannel().
  '''
  if ch is None:
    ch=getChannel()
  bg=Thread(target=_bgFunc,args=(func,args,ch))
  ##bg.setDaemon(True)
  bg.setName("bgCall(func=%s, args=%s)" % (func, args))
  bg.start()
  return ch
def _bgFunc(func,args,ch):
  result=func(*args)
  ch.put(result)
def _bgReturn(args,ch):
  ch.put(args)
def bgReturn(result,ch=None):
  ''' Return an asynchronous result.
      Takes the result, returns a channel from which to read it back.
      A channel may be supplied by the caller; if not then the returned
      channel must be release with returnChannel().
  '''
  return bgCall(_bgReturn,(result,))

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
    self.count=0
    self.lock=allocate_lock()
  def open(self):
    ''' Increment the open count.
    '''
    with self.lock:
      self.__nocOpen+=1
  def __enter__(self):
    self.open()
  def close(self):
    ''' Decrement the open count.
        If the count goes to zero, call self.shutdown().
    '''
    with self.lock:
      count=self.count
      assert count > 0, "self.count (%s) <= 0" % count
      count-=1
      if count == 0:
        self.shutdown()
      self.count=count
  def __exit__(self, exc_type, exc_value, traceback):
    self.close()
    return False
