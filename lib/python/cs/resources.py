#!/usr/bin/python
#
# Resourcing related classes and functions.
#       - Cameron Simpson <cs@zip.com.au> 11sep2014
#

import threading
from threading import Condition
from cs.excutils import logexc
from cs.obj import O, Proxy

def not_closed(func):
  ''' Decorator to wrap NestingOpenCloseMixin proxy object methods
      which hould raise when self.closed.
  '''
  def not_closed_wrapper(self, *a, **kw):
    if self.closed:
      error("%r: ALREADY CLOSED: closed set to True from the following:", self)
      stack_dump(stack=self.closed_stacklist, log_level=logging.ERROR)
      raise RuntimeError("%s: %s: already closed" % (not_closed_wrapper.__name__, self))
    return func(self, *a, **kw)
  not_closed_wrapper.__name__ = "not_closed_wrapper(%s)" % (func.__name__,)
  return not_closed_wrapper

class _NOC_Proxy(Proxy):
  ''' A Proxy subclass to return from NestingOpenCloseMixin.open() and __enter__.
      Note tht this has its own localised .closed attribute which starts False.
      This lets users indidually track .closed for their use.
  '''

  def __init__(self, other, name=None):
    Proxy.__init__(self, other)
    if name is None:
      name = "%s-open%d" % ( getattr(other,
                                     'name',
                                     "%s#%d" % (self.__class__.__name__,
                                                id(self))),
                             seq()
                           )
    self.name = name
    self.closed = False

  def __str__(self):
    return "open(%r:%s[closed=%r,all_closed=%r])" % (self.name, self._proxied, self.closed, self._proxied.all_closed)

  __repr__ = __str__

  @not_closed
  def close(self, check_final_close=False):
    ''' Close this open-proxy. Sanity check then call inner close.
    '''
    self.closed = True
    self.closed_stacklist = traceback.extract_stack()
    self._proxied._close()
    if check_final_close:
      if self._proxied.all_closed:
        self.D("OK FINAL CLOSE")
      else:
        raise RuntimeError("%s: expected this to be the final close, but it was not" % (self,))

class _NOC_ThreadingLocal(threading.local):

  def __init__(self):
    self.cmgr_proxies = []

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

  def __init__(self, proxy_type=None, finalise_later=False):
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
    if proxy_type is None:
      proxy_type = _NOC_Proxy
    self._noc_proxy_type = proxy_type
    self._noc_tl = _NOC_ThreadingLocal()
    self.opened = False
    self._opens = 0
    ##self.closed = False # final _close() not yet called
    self._keep_open = None
    self._keep_open_until = None
    self._keep_open_poll_interval = 0.5
    self._keep_open_increment = 1.0
    self._finalise_later= finalise_later
    self._finalise = Condition(self._lock)

  def open(self, name=None):
    ''' Increment the open count.
	If self.on_open, call self.on_open(self, count) with the
	post-increment count.
        `name`: optional name for this open object.
        Return a Proxy object that tracks this open.
    '''
    self.opened = True
    with self._lock:
      self._opens += 1
      count = self._opens
    ifmethod(self, 'on_open', a=(count,))
    return self._noc_proxy_type(self, name=name)

  def close(self):
    ''' Placeholder method to warn callers that they should be using the proxy returned from .open().
    '''
    raise RuntimeError("%s subclasses do not support .close(): that method is to be called on the _NOC_Proxy returned from .open()" % (self.__class__.__name__,))

  @property
  def cmgr_proxy(self):
    ''' Property representing the current context manager proxy.
    '''
    return self._noc_tl.cmgr_proxies[-1]

  def __enter__(self):
    ''' NestingOpenClose context managers return an open proxy.
    '''
    # get an open proxy and push it onto the thread-local stack
    proxy = self.open()
    self._noc_tl.cmgr_proxies.append(proxy)
    return proxy

  def __exit__(self, exc_type, exc_value, traceback):
    # retrieve the open proxy
    proxy = self._noc_tl.cmgr_proxies.pop()
    proxy.close()
    return False

  @logexc
  def _close(self):
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
      self.shutdown()
      if not self._finalise_later:
        self.finalise()
    elif self.all_closed:
      error("%s.close: count=%r, ALREADY CLOSED", self, count)

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
  def all_closed(self):
    if self._opens > 0:
      return False
    if self._opens < 0:
      with PfxCallInfo():
        warning("%r._opens < 0: %r", self, self._opens)
    if not self.opened:
      # never opened, so not totally closed
      return False
    ##if not self.closed:
    ##  with PfxCallInfo():
    ##    warning("%r.closed = %r, but want to return all_closed=True", self, self.closed)
    ##  return False
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
      if self._keep_open is None:
        name = "%s._ping_mainloop" % (self,)
        P = self.open(name=name)
        self._keep_open = P
        T = Thread(name=name, target=self._ping_mainloop, args=(P,))
      else:
        P = self._keep_open
    self._keep_open_until = time.time() + self._keep_open_increment
    if T:
      T.start()

  def _ping_mainloop(self, proxy):
    ''' Pinger main loop: wait until expiry then close the open proxy.
    '''
    name = self._keep_open.name
    while self._keep_open_until > time.time():
      debug("%s: pinger: sleep for another %gs", name, self._keep_open_poll_interval)
      time.sleep(self._keep_open_poll_interval)
    self._keep_open = None
    self._keep_open_until = None
    debug("%s: pinger: close()", name)
    proxy.close()
