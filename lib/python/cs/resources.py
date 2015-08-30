#!/usr/bin/python
#
# Resourcing related classes and functions.
#       - Cameron Simpson <cs@zip.com.au> 11sep2014
#

DISTINFO = {
    'description': "resourcing related classes and functions",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
        ],
    'requires': ['cs.excutils', 'cs.logutils', 'cs.obj', 'cs.py.func'],
}

import threading
from threading import Condition
import time
import traceback
from cs.excutils import logexc
from cs.logutils import debug, warning, error, PfxCallInfo
from cs.obj import O
from cs.py.func import callmethod_if as ifmethod

def not_closed(func):
  ''' Decorator to wrap MultiOpenMixin proxy object methods
      which hould raise when self.closed.
  '''
  def not_closed_wrapper(self, *a, **kw):
    if self.closed:
      raise RuntimeError("%s: %s: already closed" % (not_closed_wrapper.__name__, self))
    return func(self, *a, **kw)
  not_closed_wrapper.__name__ = "not_closed_wrapper(%s)" % (func.__name__,)
  return not_closed_wrapper

class MultiOpenMixin(O):
  ''' A mixin to count open and closes, and to call .startup on the first .open and to call .shutdown on the last .close.
      Use as a context manager calls open()/close() from __enter__() and __exit__().
      Multithread safe.
      This mixin uses the internal attribute _opens and relies on a
      preexisting attribute _lock for locking.
      Classes using this mixin need to define .startup and .shutdown,
      and a mutex of some kind as ._lock (eg Lock or RLock).
  '''

  def __init__(self, finalise_later=False):
    ''' Initialise the MultiOpenMixin state.
        `finalise_later`: do not notify the finalisation Condition on
          shutdown, require a separate call to .finalise().
          This is mode is useful for objects such as queues where
          the final close prevents further .put calls, but users
          calling .join may need to wait for all the queued items
          to be processed.
    '''
    self.opened = False
    self._opens = 0
    ##self.closed = False # final _close() not yet called
    self._finalise_later= finalise_later
    self._finalise = Condition(self._lock)

  def __enter__(self):
    self.open()
    return self

  def __exit__(self, exc_type, exc_value, traceback):
    self.close()
    return False

  def open(self):
    ''' Increment the open count.
        On the first .open call self.startup().
    '''
    self.opened = True
    with self._lock:
      self._opens += 1
      if self._opens == 1:
        self.startup()
    return self

  @logexc
  ##@not_closed
  def close(self, enforce_final_close=False):
    ''' Decrement the open count.
        If the count goes to zero, call self.shutdown().
    '''
    with self._lock:
      if self._opens < 1:
        error("%s: EXTRA CLOSE", self)
      self._opens -= 1
      count = self._opens
      if self._opens == 0:
        if enforce_final_close:
          self.D("OK FINAL CLOSE")
        self.shutdown()
        if not self._finalise_later:
          self.finalise()
      else:
        if enforce_final_close:
          raise RuntimeError("%s: expected this to be the final close, but it was not" % (self,))

  def finalise(self):
    ''' Finalise the object, releasing all callers of .join().
        Normally this is called automatically after .shutdown unless
        `finalise_later` was set to true during initialisation.
    '''
    with self._lock:
      if self._finalise:
        self._finalise.notify_all()
        self._finalise = None
        return
    warning("%s: finalised more than once", self)

  @property
  def closed(self):
    if self._opens > 0:
      return False
    if self._opens < 0:
      with PfxCallInfo():
        warning("%r._opens < 0: %r", self, self._opens)
    if not self.opened:
      # never opened, so not totally closed
      return False
    return True

  def join(self):
    ''' Join this object.
        Wait for the internal _finalise Condition (if still not None).
        Normally this is notified at the end of the shutdown procedure
        unless the object's `finalise_later` parameter was true.
    '''
    self._lock.acquire()
    if self._finalise:
      self._finalise.wait()
    else:
      self._lock.release()

class MultiOpen(MultiOpenMixin):
  ''' Context manager class that manages a single open/close object using a MultiOpenMixin.
  '''

  def __init__(self, openable, finalise_later=False):
    MultiOpenMixin.__init__(self, finalise_later=finalise_later)
    self.openable = openable

  def startup(self):
    self.openable.open()

  def shutdown(self):
    self.openable.close()
