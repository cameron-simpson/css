#!/usr/bin/python
#
# Thread convenience facilities.
#       - Cameron Simpson <cs@zip.com.au> 18nov2007
#

from __future__ import with_statement
from threading import Thread, BoundedSemaphore

class Channel:
  ''' A zero-storage data passage.
  '''
  def __init__(self):
    self.__readable=BoundedSemaphore(1)
    self.__readable.acquire()
    self.__writable=BoundedSemaphore(1)
    self.__writable.acquire()

  def read(self):
    ''' Read a value from the Channel.
        Blocks until someone writes to the Channel.
    '''
    self.__writable.release()
    self.__readable.acquire()
    value=self._value
    delattr(self,'_value')
    return value

  def write(self,value):
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
      yield self.read()

  def call(self,value,backChannel):
    ''' Asynchronous call to daemon via channel.
        Daemon should reply by .write()ing to the backChannel.
    '''
    self.write((value,backChannel))

  def call_s(self,value):
    ''' Synchronous call to daemon via channel.
    '''
    ch=getChannel()
    self.call(value,ch)
    result=ch.read()
    returnChannel(ch)
    return result

class JobQueue:
  ''' A job queue.
      Q.queue(token) -> channel
      Q.dequeue(token, result) -> sends result to channel, releases channel
  '''
  def __init__(self,maxq=None):
    self.q={}
    self.lock=BoundedSemaphore(1)
    self.maxq=maxq
    if maxq is not None:
      assert maxq > 0
      self.maxsem=BoundedSemaphore(maxq)

  def enqueue(self,n,ch=None):
    ''' Queue a token, return a channel to recent the job completion.
        Allocate a channel for the result if none supplied.
    '''
    if self.maxq is not None:
      self.maxsem.acquire()
    if ch is None:
      ch=getChannel()
      doRelease=True
    else:
      doRelease=False
    with self.lock:
      assert n not in self.q, "\"%s\" already queued"
      self.q[n]=(ch,doRelease)
    return ch
  
  def dequeue(self,n,result):
    ''' Dequeue a token, send the result and token down the channel allocated.
        Release the channel if we allocated it.
    '''
    with self.lock:
      ch, doRelease = self.q[n]
      del self.q[n]
    if self.maxq is not None:
      self.maxsem.release()
    ch.write((n,result))
    if doRelease:
      returnChannel(ch)

''' A pool of Channels.
'''
__channels=[]
__channelsLock=BoundedSemaphore(1)
def getChannel():
  with __channelsLock:
    if len(__channels) == 0:
      ch=Channel()
    else:
      ch=__channels.pop(-1)
  return ch
def returnChannel(ch):
  with __channelsLock:
    __channels.append(ch)

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
  ch.write(result)
def _bgReturn(args,ch):
  ch.write(args)
def bgReturn(result,ch=None):
  ''' Return an asynchronous result.
      Takes the result, returns a channel from which to read it back.
      A channel may be supplied by the caller; if not then the returned
      channel must be release with returnChannel().
  '''
  return bgCall(_bgReturn,(result,))
