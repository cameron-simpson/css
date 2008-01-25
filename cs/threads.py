#!/usr/bin/python
#
# Thread convenience facilities.
#       - Cameron Simpson <cs@zip.com.au> 18nov2007
#

from __future__ import with_statement
from threading import Thread, BoundedSemaphore
from Queue import Queue
from cs.misc import debug, ifdebug, tb, cmderr, warn

class Channel:
  ''' A zero-storage data passage.
  '''
  def __init__(self):
    self.__readable=BoundedSemaphore(1)
    self.__readable.acquire()
    self.__writable=BoundedSemaphore(1)
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

class JobQueue:
  ''' A job queue.
      Q.queue(token) -> channel
      Q.dequeue(token, result) -> sends result to channel, releases channel
  '''
  def __init__(self,maxq=None,useQueue=True):
    self.q={}
    self.lock=BoundedSemaphore(1)
    self.maxq=maxq
    self.useQueue=useQueue
    if maxq is not None:
      assert maxq > 0
      self.maxsem=BoundedSemaphore(maxq)

  def enqueue(self,n,ch=None,useQueue=None):
    ''' Queue a token, return a channel to recent the job completion.
        Allocate a single-use channel for the result if none supplied.
    '''
    if self.maxq is not None:
      self.maxsem.acquire()
    if ch is None:
      if useQueue is None:
        useQueue=self.useQueue
      ch=Q1()
      debug("enqueue: using allocated channel %s" % ch)
    with self.lock:
      assert n not in self.q, "\"%s\" already queued"
      debug("enqueue: q[%d]=%s" % (n, ch))
      ##if ifdebug(): tb()
      self.q[n]=ch
    return ch
  
  def dequeue(self,n,result):
    ''' Dequeue a token, send the result and token down the channel allocated.
    '''
    with self.lock:
      ch = self.q[n]
      del self.q[n]
    if self.maxq is not None:
      self.maxsem.release()
    ch.put((n,result))

class FuncQueue(Queue):
  ''' A Queue of function calls to make, processed serially.
      New functions queued as .put((func,args)) or as .qfunc(func,args...).
      Queue shut down with .close().
  '''
  def __init__(self,size=None):
    Queue.__init__(self,size)
    self.__closing=False
    import atexit
    atexit.register(self.close)
    Thread(target=self.__runQueue).start()
  def qfunc(self,func,*args):
    self.put((func,args))
  def put(self,item):
    assert not self.__closing
    Queue.put(self,item)
  def close(self):
    if not self.__closing:
      self.put((None,None))
      self.__closing=True
      ##tb()
  def __runQueue(self):
    ''' A thread to process queue items serially.
        This exists to permit easy or default implementation of the
        cs.venti.store *_a() methods, and is better suited to fast
        low latency stores.
        A highly parallel or high latency store will want its own
        thread scheme to manage multiple *_a() operations.
    '''
    while not self.__closing or not self.empty():
      func, args = self.get(True,None)
      if func is None:
        self.__closing=True
        break
      func(*args)
    while not self.empty():
      func, args = self.get(True,None)
      if func is not None:
        cmderr("warning: drained FuncQueue item after close: %s" % ((func, args),))

''' A pool of Channels.
'''
__channels=[]
__channelsLock=BoundedSemaphore(1)
def getChannel():
  with __channelsLock:
    if len(__channels) == 0:
      ch=Channel()
      debug("getChannel: allocated %s" % ch)
      ##tb()
    else:
      ch=__channels.pop(-1)
  return ch
def _returnChannel(ch):
  debug("returnChannel: releasing %s" % ch)
  with __channelsLock:
    assert ch not in __channels
    __channels.append(ch)

''' A pool of _Q1 objects (single use Queue(1)s).
'''
__queues=[]
__queuesLock=BoundedSemaphore(1)
def Q1(name=None):
  ''' Obtain a _Q1 object (single use Queue(1), self disposing).
  '''
  with __queuesLock:
    if len(__queues) == 0:
      Q=_Q1()
    else:
      Q=__queues.pop(-1)
      Q._reset(name=name)
      Q.didget=False
      Q.didput=False
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
    self.reset(name=name)
  def _reset(self,name):
    if name is None:
      if ifdebug():
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

class PreQueue(Queue):
  ''' A Queue with push-back and iteration.
      Bug: the push back doesn't play nice with pending get()s.
  '''
  def __init__(self,size=None):
    Queue.__init__(self,size)
    self.__preQ=[]
    self.__preQLock=BoundedSemaphore(1)
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
__nullCHlock=BoundedSemaphore(1)
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
    self.lock=BoundedSemaphore(1)
  def __getitem__(self,k):
    with self.lock:
      v=dict.__getitem__(self,k)
    return v
  def __delitem__(self,k):
    with self.lock:
      v=dict.__delitem__(self,k)
    return v
  def __setitem__(self,k,v):
    with self.lock:
      dict.__setitem__(self,k,v)
  def keys(self):
    with self.lock:
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
