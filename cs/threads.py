#!/usr/bin/python
#
# Thread convenience facilities.
#       - Cameron Simpson <cs@zip.com.au> 18nov2007
#

from __future__ import with_statement
from threading import BoundedSemaphore

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
    self.delattr(_value)
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
