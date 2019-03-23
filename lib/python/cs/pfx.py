#!/usr/bin/python
#
# Pfx: a framework for easy to use dynamic message prefixes.
#   - Cameron Simpson <cs@cskk.id.au>
#

r'''
Dynamic message prefixes providing execution context.

The primary facility here is Pfx,
a context manager which maintains a per thread stack of context prefixes.

Usage is like this:

    from cs.pfx import Pfx
    ...
    def parser(filename):
      with Pfx("parse(%r)", filename):
        with open(filename) as f:
          for lineno, line in enumerate(f, 1):
            with Pfx("%d", lineno) as P:
              if line_is_invalid(line):
                raise ValueError("problem!")
              P.info("line = %r", line)

This produces log messages like:

    datafile: 1: line = 'foo\n'

and exception messages like:

    datafile: 17: problem!

which lets one put just the relevant complaint in exception and log
messages and get useful calling context on the output.
This does make for wordier logs and exceptions
but used with a little discretion produces far more debugable results.
'''

from __future__ import print_function
from contextlib import contextmanager
from inspect import isgeneratorfunction
import logging
import sys
import threading
from cs.py3 import StringTypes, ustr, unicode
from cs.x import X

DISTINFO = {
    'description': "Easy context prefixes for messages.",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.py3',
        'cs.x',
    ],
}

cmd = None

def pfx_iter(tag, iterable):
  ''' Wrapper for iterables to prefix exceptions with `tag`.
  '''
  with Pfx(tag):
    I = iter(iterable)
  while True:
    with Pfx(tag):
      try:
        i = next(I)
      except StopIteration:
        break
    yield i

def pfx(func):
  ''' Decorator for functions that should run inside:

          with Pfx(func_name):

      Use:

          @pfx
          def f(...):
  '''
  def wrapped(*args, **kwargs):
    with Pfx(func.__name__):
      return func(*args, **kwargs)
  return wrapped

def pfxtag(tag, loggers=None):
  ''' Decorator for functions that should run inside:

          with Pfx(tag, loggers=loggers):

      Use:

          @pfxtag(tag)
          def f(...):
  '''
  def wrap(func):
    if tag is None:
      wraptag = func.__name__
    else:
      wraptag = tag
    def wrapped(*args, **kwargs):
      with Pfx(wraptag, loggers=loggers):
        return func(*args, **kwargs)
    return wrapped
  return wrap

class _PfxThreadState(threading.local):
  ''' _PfxThreadState is a thread local class to track Pfx stack state.
  '''

  def __init__(self):
    threading.local.__init__(self)
    self.raise_needs_prefix = False
    self._ur_prefix = None
    self.stack = []
    self.trace = None
    self._doing_prefix = False

  @property
  def cur(self):
    ''' .cur is the current/topmost Pfx instance.
    '''
    global cmd
    stack = self.stack
    if not stack:
      if not cmd:
        try:
          cmd = sys.argv[0]
        except IndexError:
          cmd = "NO_SYS_ARGV_0"
      return Pfx(cmd)
    return stack[-1]

  @property
  def prefix(self):
    ''' Return the prevailing message prefix.
    '''
    global cmd
    # Because P.umark can call str() on the mark, which in turn may
    # call arbitrary code which in turn may issue log messages, which
    # in turn may call this, we prevent such recursion.
    doing_prefix = self._doing_prefix
    self._doing_prefix = True
    marks = []
    for P in reversed(list(self.stack)):
      if doing_prefix:
        marks.append(str(type(P._umark)))
      else:
        marks.append(P.umark)
      if P.absolute:
        break
    self._doing_prefix = doing_prefix
    if self._ur_prefix is not None:
      marks.append(self._ur_prefix)
    if cmd is not None:
      marks.append(cmd)
    marks = reversed(marks)
    return unicode(': ').join(marks)

  def append(self, P):
    ''' Push a new Pfx instance onto the stack.
    '''
    self.stack.append(P)

  def pop(self):
    ''' Pop a Pfx instance from the stack.
    '''
    return self.stack.pop()

def gen(func):
  ''' Decorator for generators to manage the Pfx stack.

      Before running the generator the current stack height is
      noted.  After yield, the stack above that height is trimmed
      and saved, and the value yielded.  On recommencement the saved
      stack is reapplied to the current stack (which may have
      changed) and the generator continued.
  '''
  def wrapper(*a, **kw):
    if not isgeneratorfunction(func):
      raise ValueError("@gen: generatior function required, received %s" % (func,))
    # commence the generator
    g = func(*a, **kw)
    # note the current Thread's Pfx stack
    stack = Pfx._state.stack
    height = len(stack)
    while True:
      try:
        value = next(g)
      except StopIteration:
        # clean up and reraise
        stack[height:] = []
        return
      except:
        # clean up and reraise
        stack[height:] = []
        raise
      # shelve the in-generator Pfx stack and yield
      saved = stack[height:]
      stack[height:] = []
      yield value
      # note the current Thread's Pfx stack
      stack = Pfx._state.stack
      height = len(stack)
      # reapply the in-generator Pfx stack
      stack.extend(saved)
      del saved
  wrapper.__name__ = "@Pfx.gen(%s)" % (func.__name__,)
  fdoc = func.__doc__
  wrapper.__doc__ = wrapper.__name__ + ":\n" + fdoc if fdoc else ''
  return wrapper

class Pfx(object):
  ''' A context manager to maintain a per-thread stack of message prefixes.
  '''

  # instantiate the thread-local state object
  _state = _PfxThreadState()

  def __init__(self, mark, *args, **kwargs):
    ''' Initialise a new Pfx instance.

        Parameters:
        * `mark`: message prefix string
        * `args`: if not empty, apply to the prefix string with `%`
        * `absolute`: optional keyword argument, default False. If
          true, this message forms the base of the message prefixes;
          existing prefixes will be suppressed.
        * `loggers`: which loggers should receive log messages.
    '''
    absolute = kwargs.pop('absolute', False)
    loggers = kwargs.pop('loggers', None)
    if kwargs:
      raise TypeError("unsupported keyword arguments: %r" % (kwargs,))

    self.mark = mark
    self.mark_args = args
    self.absolute = absolute
    self._umark = None
    self._loggers = None
    if loggers is not None:
      if not hasattr(loggers, '__getitem__'):
        loggers = (loggers, )
      self.logto(loggers)

  def __enter__(self):
    _state = self._state
    _state.append(self)
    _state.raise_needs_prefix = True
    if _state.trace:
      _state.trace(_state.prefix)

  def __exit__(self, exc_type, exc_value, traceback):
    _state = self._state
    if exc_value is not None:
      if _state.raise_needs_prefix:
        # prevent outer Pfx wrappers from hacking stuff as well
        _state.raise_needs_prefix = False
        # now hack the exception attributes
        current_prefix = self._state.prefix
        def prefixify(text):
          if not isinstance(text, StringTypes):
            ##X("%s: not a string (class %s), not prefixing: %r (sys.exc_info=%r)",
            ##  current_prefix, text.__class__, text, sys.exc_info())
            return text
          return current_prefix \
              + ': ' \
              + ustr(text, errors='replace').replace('\n', '\n' + current_prefix)
        did_prefix = False
        for attr in 'args', 'message', 'msg', 'reason':
          try:
            value = getattr(exc_value, attr)
          except AttributeError:
            pass
          else:
            if isinstance(value, StringTypes):
              value = prefixify(value)
            else:
              try:
                vlen = len(value)
              except TypeError:
                print(
                    "warning: %s: %s.%s: " % (current_prefix, exc_value, attr),
                    prefixify("do not know how to prefixify: %r" % (value,)),
                    file=sys.stderr)
                continue
              else:
                if vlen < 1:
                  value = [ prefixify(repr(value)) ]
                else:
                  value = [ prefixify(value[0]) ] + list(value[1:])
            setattr(exc_value, attr, value)
            did_prefix = True
        if not did_prefix:
          print(
              "warning: %s: %s:%s: message not prefixed"
              % (current_prefix, type(exc_value).__name__, exc_value),
              file=sys.stderr)
    _state.pop()
    if _state.trace:
      _state.trace(_state.prefix)
    return False

  @property
  def umark(self):
    ''' Return the unicode message mark for use with this Pfx.

        This is used by Pfx._state.prefix to compute the full prefix.
    '''
    u = self._umark
    if u is None:
      mark = ustr(self.mark)
      if not isinstance(mark, unicode):
        if isinstance(mark, str):
          mark = unicode(mark, errors='replace')
        else:
          mark = unicode(mark)
      u = mark
      if self.mark_args:
        try:
          u = u % self.mark_args
        except TypeError as e:
          X("FORMAT CONVERSION: %s: %r %% %r", e, u, self.mark_args)
          u = u + ' % ' + repr(self.mark_args)
      self._umark = u
    return u

  def logto(self, new_loggers):
    ''' Define the Loggers anew.
    '''
    self._loggers = new_loggers

  def partial(self, func, *a, **kw):
    ''' Return a function that will run the supplied function `func`
        within a surrounding Pfx context with the current mark string.

        This is intended for deferred call facilities like
        WorkerThreadPool, Later, and futures.
    '''
    pfx2 = Pfx(self.mark, absolute=True, loggers=self.loggers)
    def pfxfunc():
      with pfx2:
        return func(*a, **kw)
    return pfxfunc

  @property
  def loggers(self):
    ''' Return the loggers to use for this Pfx instance.
    '''
    _loggers = self._loggers
    if _loggers is None:
      for P in reversed(self._state.stack):
        if P._loggers is not None:
          _loggers = P._loggers
          break
      if _loggers is None:
        _loggers = (logging.getLogger(),)
    return _loggers

  enter = __enter__
  exit = __exit__

  # Logger methods
  def exception(self, msg, *args):
    ''' Log an exception message to this Pfx's loggers.
    '''
    for L in self.loggers:
      L.exception(msg, *args)
  def log(self, level, msg, *args, **kwargs):
    ''' Log a message at an arbitrary log level to this Pfx's loggers.
    '''
    ## to debug format errors ## D("msg=%r, args=%r, kwargs=%r", msg, args, kwargs)
    for L in self.loggers:
      try:
        L.log(level, msg, *args, **kwargs)
      except Exception as e:
        print(
            "%s: exception logging to %s msg=%r, args=%r, kwargs=%r: %s"
            % (self._state.prefix, L, msg, args, kwargs, e), file=sys.stderr)
  def debug(self, msg, *args, **kwargs):
    ''' Emit a debug log message.
    '''
    self.log(logging.DEBUG, msg, *args, **kwargs)
  def info(self, msg, *args, **kwargs):
    ''' Emit an info log message.
    '''
    self.log(logging.INFO, msg, *args, **kwargs)
  def warning(self, msg, *args, **kwargs):
    ''' Emit a warning log message.
    '''
    self.log(logging.WARNING, msg, *args, **kwargs)
  def error(self, msg, *args, **kwargs):
    ''' Emit an error log message.
    '''
    self.log(logging.ERROR, msg, *args, **kwargs)
  def critical(self, msg, *args, **kwargs):
    ''' Emit a critical log message.
    '''
    self.log(logging.CRITICAL, msg, *args, **kwargs)

def prefix():
  ''' Return the current Pfx prefix.
  '''
  return Pfx._state.prefix

@contextmanager
def PrePfx(tag, *args):
  ''' Push a temporary value for Pfx._state._ur_prefix to enloundenify messages.
  '''
  if args:
    tag = tag % args
  state = Pfx._state
  old_ur_prefix = state._ur_prefix
  state._ur_prefix = tag
  try:
    yield None
  finally:
    state._ur_prefix = old_ur_prefix

class PfxCallInfo(Pfx):
  ''' Subclass of Pfx to insert current function an caller into messages.
  '''

  def __init__(self):
    import traceback
    grandcaller, caller, _ = traceback.extract_stack(None, 3)
    Pfx.__init__(self,
                 "at %s:%d %s(), called from %s:%d %s()",
                 caller[0], caller[1], caller[2],
                 grandcaller[0], grandcaller[1], grandcaller[2])

def PfxThread(target=None, **kw):
  ''' Factory function returning a Thread
      which presents the current prefix as context.
  '''
  current_prefix = prefix()
  def run(*a, **kw):
    with Pfx(current_prefix):
      if target is not None:
        target(*a, **kw)
  return threading.Thread(target=run, **kw)

def XP(msg, *args, **kwargs):
  ''' Variation on `cs.x.X`
      which prefixes the message with the currrent Pfx prefix.
  '''
  file = kwargs.pop('file', None)
  if file is None:
    file = sys.stderr
  elif file is not None:
    if isinstance(file, StringTypes):
      with open(file, "a") as fp:
        return XP(msg, *args, file=fp)
  file.write(prefix())
  file.write(': ')
  file.flush()
  return X(msg, *args, file=file)

def XX(prepfx, msg, *args, **kwargs):
  ''' Trite wrapper for `XP()` to transiently insert a leading prefix string.

      Example:

          XX("NOTE!", "some message")
  '''
  with PrePfx(prepfx):
    return XP(msg, *args, **kwargs)
