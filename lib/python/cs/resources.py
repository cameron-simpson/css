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
import traceback
from cs.excutils import logexc
from cs.logutils import warning, error
from cs.obj import O
from cs.py.func import callmethod_if as ifmethod

def not_closed(func):
  ''' Decorator to wrap NestingOpenCloseMixin proxy object methods
      which hould raise when self.closed.
  '''
  def not_closed_wrapper(self, *a, **kw):
    if self.closed:
      raise RuntimeError("%s: %s: already closed" % (not_closed_wrapper.__name__, self))
    return func(self, *a, **kw)
  not_closed_wrapper.__name__ = "not_closed_wrapper(%s)" % (func.__name__,)
  return not_closed_wrapper

class NestingOpenCloseMixin(O):
  ''' A mixin to count open and closes, and to call .shutdown() when the count goes to zero.
      A count of active open()s is kept, and on the last close()
      the object's .shutdown() method is called.
      Use via the with-statement calls open()/close() for __enter__()
      and __exit__().
      Multithread safe.
      This mixin uses the internal attribute _opens and relies on a
      preexisting attribute _lock for locking.
  '''

  def __init__(self, finalise_later=False):
    ''' Initialise the NestingOpenCloseMixin state.
        Then takes makes use of the following methods if present:
          `self.on_open(count)`: called on open with the post-increment open count
          `self.on_close(count)`: called on close with the pre-decrement open count
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
    self._keep_open = None
    self._keep_open_until = None
    self._keep_open_poll_interval = 0.5
    self._keep_open_increment = 1.0
    self._finalise_later= finalise_later
    self._finalise = Condition(self._lock)

  def __enter__(self):
    self.open()
    return self

  def __exit__(self, exc_type, exc_value, traceback):
    self.close()
    return False

  def open(self, name=None):
    ''' Increment the open count.
	If self.on_open, call self.on_open(self, count) with the
	post-increment count.
        `name`: optional name for this open object.
    '''
    self.opened = True
    with self._lock:
      self._opens += 1
      count = self._opens
    ifmethod(self, 'on_open', a=(count,))
    return self

  @logexc
  ##@not_closed
  def close(self, enforce_final_close=False):
    ''' Decrement the open count.
        If self.on_close, call self.on_close(self, count) with the
        pre-decrement count.
        If the count goes to zero, call self.shutdown().
    '''
    with self._lock:
      if self._opens < 1:
        error("%s: EXTRA CLOSE", self)
      self._opens -= 1
      count = self._opens
    ifmethod(self, 'on_close', a=(count+1,))
    if count == 0:
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

  def ping(self):
    ''' Mark this object as "busy"; it will be kept open a little longer in case of more use.
    '''
    T = None
    with self._lock:
      if not self._keep_open:
        self._keep_open = True
        name = "%s._ping_mainloop" % (self,)
        T = Thread(name=name, target=self._ping_mainloop)
    self._keep_open_until = time.time() + self._keep_open_increment
    if T:
      T.start()

  def _ping_mainloop(self):
    ''' Pinger main loop: wait until expiry then close the open proxy.
    '''
    name = self._keep_open.name
    while self._keep_open_until > time.time():
      debug("%s: pinger: sleep for another %gs", name, self._keep_open_poll_interval)
      time.sleep(self._keep_open_poll_interval)
    self._keep_open = False
    self._keep_open_until = None
    debug("%s: pinger: close()", name)
    self.close()
