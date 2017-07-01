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
    'install_requires': ['cs.excutils', 'cs.logutils', 'cs.obj', 'cs.py.stack'],
}

from contextlib import contextmanager
from threading import Condition, RLock, Lock, current_thread
import time
import traceback
from cs.excutils import logexc
import cs.logutils
from cs.logutils import debug, warning, error, PfxCallInfo, X
from cs.pfx import XP
from cs.pfx import Pfx
from cs.obj import O
from cs.py.stack import caller, stack_dump

class ClosedError(Exception):
  pass

def not_closed(func):
  ''' Decorator to wrap methods of objects with a .closed property which should raise when self.closed.
  '''
  def not_closed_wrapper(self, *a, **kw):
    if self.closed:
      raise ClosedError("%s: %s: already closed" % (not_closed_wrapper.__name__, self))
    return func(self, *a, **kw)
  not_closed_wrapper.__name__ = "not_closed_wrapper(%s)" % (func.__name__,)
  return not_closed_wrapper

class MultiOpenMixin(O):
  ''' A mixin to count open and closes, and to call .startup on the first .open and to call .shutdown on the last .close.
      Use as a context manager calls open()/close() from __enter__() and __exit__().
      Multithread safe.
      This mixin defines ._lock = RLock(); subclasses need not bother.
      Classes using this mixin need to define .startup and .shutdown.
  '''

  def __init__(self, finalise_later=False, lock=None):
    ''' Initialise the MultiOpenMixin state.
        `finalise_later`: do not notify the finalisation Condition on
          shutdown, require a separate call to .finalise().
          This is mode is useful for objects such as queues where
          the final close prevents further .put calls, but users
          calling .join may need to wait for all the queued items
          to be processed.
        `lock`: if set and not None, an RLock to use; otherwise one will be allocated
    '''
    if lock is None:
      lock = RLock()
    self.opened = False
    self._opens = 0
    self._opened_from = {}
    ##self.closed = False # final _close() not yet called
    self._lock = lock
    self._finalise_later = finalise_later
    self._finalise = None

  def __enter__(self):
    self.open(caller_frame=caller())
    return self

  def __exit__(self, exc_type, exc_value, traceback):
    self.close()
    return False

  def open(self, caller_frame=None):
    ''' Increment the open count.
        On the first .open call self.startup().
    '''
    if True:    ## cs.logutils.D_mode:
      if caller_frame is None:
        caller_frame = caller()
      Fkey = caller_frame.filename, caller_frame.lineno
      self._opened_from[Fkey] = self._opened_from.get(Fkey, 0) + 1
    self.opened = True
    with self._lock:
      self._opens += 1
      opens = self._opens
    if opens == 1:
      self._finalise = Condition(self._lock)
      self.startup()
    return self

  def close(self, enforce_final_close=False):
    ''' Decrement the open count.
        If the count goes to zero, call self.shutdown().
    '''
    with self._lock:
      if self._opens < 1:
        error("%s: UNDERFLOW CLOSE", self)
        for Fkey in sorted(self._opened_from.keys()):
          error("  opened from %s %d times", Fkey, self._opened_from[Fkey])
        ##from cs.debug import thread_dump
        ##thread_dump([current_thread()])
        ##raise RuntimeError("UNDERFLOW CLOSE of %s" % (self,))
        return
      self._opens -= 1
      opens = self._opens
    if opens == 0:
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
      finalise = self._finalise
      if finalise is None:
        raise RuntimeError("%s: finalised more than once" % (self,))
      self._finalise = None
      finalise.notify_all()

  @property
  def closed(self):
    if self._opens > 0:
      return False
    if self._opens < 0:
      XP("_opens < 0: %r", self._opens)
      raise RuntimeError("_OPENS UNDERFLOW")
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

  @staticmethod
  def is_opened(func):
    ''' Decorator to wrap MultiOpenMixin proxy object methods which should raise when if the object is not yet open.
    '''
    def is_opened_wrapper(self, *a, **kw):
      if self.closed:
        raise RuntimeError("%s: %s: already closed" % (is_opened_wrapper.__name__, self))
      if not self.opened:
        raise RuntimeError("%s: %s: not yet opened" % (is_opened_wrapper.__name__, self))
      return func(self, *a, **kw)
    is_opened_wrapper.__name__ = "is_opened_wrapper(%s)" % (func.__name__,)
    return is_opened_wrapper

class MultiOpen(MultiOpenMixin):
  ''' Context manager class that manages a single open/close object using a MultiOpenMixin.
  '''

  def __init__(self, openable, finalise_later=False, lock=None):
    MultiOpenMixin.__init__(self, finalise_later=finalise_later, lock=lock)
    self.openable = openable

  def startup(self):
    self.openable.open()

  def shutdown(self):
    self.openable.close()

class Pool(O):
  ''' A generic pool of objects on the premise that reuse is cheaper than recreation.
      All the pool objects must be suitable for use, so the
      `new_object` callable will typically be a closure. For example,
      here is the __init__ for a per-thread AWS Bucket using a
      distinct Session:

      def __init__(self, bucket_name):
        Pool.__init__(self, lambda: boto3.session.Session().resource('s3').Bucket(bucket_name)
  '''

  def __init__(self, new_object, max_size=None):
    ''' Initialise the Pool with creator `new_object` and maximum size `max_size`.
        `new_object` is a callable which returns a new object for the Pool.
        `max_size`: The maximum size of the pool of available objects saved for reuse.
            If omitted or None, defaults to 4.
            If 0, no upper limit is applied.
    '''
    if max_size is None:
      max_size = 4
    self.new_object = new_object
    self.max_size = max_size
    self.pool = []
    self._lock = Lock()

  def __str__(self):
    return "Pool(max_size=%s, new_object=%s)" % (self.max_size, self.new_object)

  @contextmanager
  def instance(self):
    ''' Context manager returning an object for use, which is returned to the pool afterwards.
    '''
    with self._lock:
      try:
        o = self.pool.pop()
      except IndexError:
        o = self.new_object()
    yield o
    with self._lock:
      if self.max_size == 0 or len(self.pool) < self.max_size:
        self.pool.append(o)
