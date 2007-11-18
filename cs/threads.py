#!/usr/bin/python
#
# Thread convenience facilities.
#       - Cameron Simpson <cs@zip.com.au> 18nov2007
#

from threading import BoundedSemaphore

class Channel:
  ''' A zero-storage data passing channel.
  '''
  def __init__(self):
    self.__readable=BoundedSemaphore(1)
    self.__readable.acquire()
    self.__writable=BoundedSemaphore(1)
    self.__writable.acquire()

  def read(self):
    self.__writable.release()
    self.__readable.acquire()
    value=self._value
    self.delattr(_value)
    return value

  def write(self,value):
    self.__writable.acquire()
    self._value=value
    self.__readable.release()
