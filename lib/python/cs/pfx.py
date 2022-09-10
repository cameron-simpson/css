#!/usr/bin/python
#
# Pfx: a framework for easy to use dynamic message prefixes.
# - Cameron Simpson <cs@cskk.id.au>
#

r'''
Dynamic message prefixes providing execution context.

The primary facility here is `Pfx`,
a context manager which maintains a per thread stack of context prefixes.
There are also decorators for functions.
This stack is used to prefix logging messages and exception text with context.

Usage is like this:

    from cs.logutils import setup_logging, info
    from cs.pfx import Pfx
    ...
    setup_logging()
    ...
    def parser(filename):
      with Pfx(filename):
        with open(filename) as f:
          for lineno, line in enumerate(f, 1):
            with Pfx(lineno) as P:
              if line_is_invalid(line):
                raise ValueError("problem!")
              info("line = %r", line)

This produces log messages like:

    datafile: 1: line = 'foo\n'

and exception messages like:

    datafile: 17: problem!

which lets one put just the relevant complaint in exception and log
messages and get useful calling context on the output.
This does make for wordier logs and exceptions
but used with a little discretion produces far more debuggable results.
'''

from __future__ import print_function
from contextlib import contextmanager
from functools import partial
from inspect import isgeneratorfunction
import logging
import sys
import threading
import traceback
from cs.deco import decorator, contextdecorator, fmtdoc, logging_wrapper
from cs.py.func import funcname, func_a_kw_fmt
from cs.py3 import StringTypes, ustr, unicode
from cs.x import X

__version__ = '20220523-post'

DISTINFO = {
    'description':
    "Easy context prefixes for messages.",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.deco',
        'cs.py.func>=func_a_kw_fmt',
        'cs.py3',
        'cs.x',
    ],
}

DEFAULT_SEPARATOR = ': '

cmd = None

@fmtdoc
def unpfx(s, sep=None):
  ''' Strip the leading prefix from the string `s`
      using the prefix delimiter `sep`
      (default from `DEFAULT_SEPARATOR`: `{DEFAULT_SEPARATOR!r}`).

      This is a simple hack to support reporting error messages
      which have had a prefix applied,
      and fails accordingly if the base message itself contains the separator.
  '''
  if sep is None:
    sep = DEFAULT_SEPARATOR
  return s.rsplit(sep, 1)[-1].strip()

def pfx_iter(tag, iterable):
  ''' Wrapper for iterables to prefix exceptions with `tag`.
  '''
  with Pfx(tag):
    it = iter(iterable)
  while True:
    with Pfx(tag):
      try:
        i = next(it)
      except StopIteration:
        break
    yield i

def pfx_call(func, *a, **kw):
  ''' Call `func(*a,**kw)` within an enclosing `Pfx` context manager
      reciting the function name and arguments.

      Example:

          >>> import os
          >>> pfx_call(os.rename, "oldname", "newname")
  '''
  pfxf, pfxav = func_a_kw_fmt(func, *a, **kw)
  with Pfx(pfxf, *pfxav):
    return func(*a, **kw)

class _PfxThreadState(threading.local):
  ''' A Thread local class to track `Pfx` stack state.
  '''

  def __init__(self):
    threading.local.__init__(self)
    self._ur_prefix = None
    self.stack = []
    self.trace = None
    self._doing_prefix = False

  @property
  def cur(self):
    ''' The current/topmost `Pfx` instance.
    '''
    global cmd  # pylint: disable=global-statement
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
    global cmd  # pylint: disable=global-statement
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
    return unicode(DEFAULT_SEPARATOR).join(marks)

  def append(self, P):
    ''' Push a new Pfx instance onto the stack.
    '''
    self.stack.append(P)

  def pop(self):
    ''' Pop a Pfx instance from the stack.
    '''
    return self.stack.pop()

class Pfx(object):
  ''' A context manager to maintain a per-thread stack of message prefixes.
  '''

  # instantiate the thread-local class state object
  _state = _PfxThreadState()

  def __init__(self, mark, *args, **kwargs):
    ''' Initialise a new Pfx instance.

        Parameters:
        * `mark`: message prefix string
        * `args`: if not empty, apply to the prefix string with `%`
        * `absolute`: optional keyword argument, default `False`. If
          true, this message forms the base of the message prefixes;
          earlier prefixes will be suppressed.
        * `loggers`: which loggers should receive log messages.
        * `print`: if true, print the `mark` on entry to the `with` suite.
          This may be a `bool`, implying `print()` if `True`,
          a callable which works like `print()`,
          or a file-like object which implies using `print(...,file=print)`.

        *Note*:
        the `mark` and `args` are only combined if the `Pfx` instance gets used,
        for example for logging or to annotate an exception.
        Otherwise, they are not combined.
        Therefore the values interpolated are as they are when the `Pfx` is used,
        not necessarily as they were when the `Pfx` was created.
        If the `args` are subject to change and you require the original values,
        apply them to `mark` immediately, for example:

            with Pfx('message %s ...' % (arg1, arg2, ...)):

        This is a bit more expensive as it incurs the formatting cost
        whenever you enter the `with` clause.
        The common usage is:

            with Pfx('message %s ...', arg1, arg2, ...):
    '''
    absolute = kwargs.pop('absolute', False)
    loggers = kwargs.pop('loggers', None)
    print_func = kwargs.pop('print', False)
    if print_func:
      if isinstance(print_func, bool):
        # bool:True => print()
        print_func = print
      elif not callable(print_func) and hasattr(print_func, 'write'):
        # presume a file
        print_func = partial(print, file=print_func)
    if kwargs:
      raise TypeError("unsupported keyword arguments: %r" % (kwargs,))

    self.mark = mark
    self.mark_args = args
    self.absolute = absolute
    self.print_func = print_func
    self._umark = None
    self._loggers = None
    if loggers is not None:
      if not hasattr(loggers, '__getitem__'):
        loggers = (loggers,)
      self.logto(loggers)

  def __enter__(self):
    # push this Pfx onto the per-Thread stack
    self._push(self)
    print_func = self.print_func
    if print_func:
      mark = self.mark
      mark_args = self.mark_args
      if mark_args:
        mark = mark % mark_args
      print_func(mark)

  @classmethod
  def _push(cls, P):
    ''' Push this `Pfx` instance onto the current `Thread`'s stack.
    '''
    state = cls._state
    state.append(P)
    if state.trace:
      state.trace(state.prefix)

  @classmethod
  def push(cls, msg, *a):
    ''' A new `Pfx(msg,*a)` onto the `Thread` stack.
    '''
    cls._push(cls(msg, *a))

  def __exit__(self, exc_type, exc_value, _):
    _state = self._state
    if exc_value is not None:
      try:
        exc_prefix = exc_value._pfx_prefix
      except AttributeError:
        exc_value._pfx_prefix = self._state.prefix
        # prevent outer Pfx wrappers from hacking stuff as well
        # now hack the exception attributes
        if not self.prefixify_exception(exc_value):
          True or print(
              "warning: %s: %s:%s: message not prefixed" %
              (self._state.prefix, type(exc_value).__name__, exc_value),
              file=sys.stderr
          )
    try:
      _state.pop()
    except IndexError as e:
      print(
          "warning: %s.__exit__: _state.pop(): %s" % (type(self).__name__, e)
      )
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
      u = ustr(self.mark)
      if self.mark_args:
        try:
          u = u % self.mark_args
        except TypeError as e:
          logging.warning(
              "FORMAT CONVERSION: %s: %r %% %r",
              e,
              u,
              self.mark_args,
              exc_info=True
          )
          u = u + ' % ' + repr(self.mark_args)
      self._umark = u
    return u

  @classmethod
  def prefixify(cls, text):
    ''' Return `text` with the current prefix prepended.
        Return `text` unchanged if it is not a string.
    '''
    current_prefix = cls._state.prefix
    if not isinstance(text, StringTypes):
      ##X("%s: not a string (class %s), not prefixing: %r (sys.exc_info=%r)",
      ##  current_prefix, text.__class__, text, sys.exc_info())
      return text
    return (
        current_prefix + DEFAULT_SEPARATOR +
        ustr(text, errors='replace'
             ).replace('\n', '\n  ' + current_prefix + DEFAULT_SEPARATOR)
    )

  @classmethod
  def prefixify_exception(cls, e):
    ''' Modify the supplied exception `e` with the current prefix.
        Return `True` if modified, `False` if unable to modify.
    '''
    current_prefix = cls._state.prefix
    did_prefix = False
    for attr in 'args', 'message', 'msg', 'reason', 'strerror':
      try:
        value = getattr(e, attr)
      except AttributeError:
        continue
      if value is None:
        continue
      # special case various known exception type attributes
      if attr == 'args' and isinstance(e, OSError):
        try:
          value0, value1 = value
        except ValueError as args_e:
          X(
              "prefixify_exception OSError.args: %s(%s) %s: args=%r: %s",
              type(e).__name__,
              ','.join(
                  cls.__name__
                  for cls in type(e).__mro__
                  if cls is not type(e) and cls is not object
              ),
              e,
              value,
              args_e,
          )
          continue
        else:
          value = (value0, cls.prefixify(value1))
      elif attr == 'args' and isinstance(e, LookupError):
        # args[0] is the key, do not fiddle with it
        continue
      elif isinstance(value, StringTypes):
        value = cls.prefixify(value)
      elif isinstance(value, Exception):
        # set did_prefix if we modify this in place
        did_prefix = cls.prefixify_exception(value)
      else:
        try:
          vlen = len(value)
        except TypeError:
          print(
              "warning: %s: %s.%s: " % (current_prefix, e, attr),
              cls.prefixify(
                  "do not know how to prefixify .%s=<%s>:%r" %
                  (attr, type(value).__name__, value)
              ),
              file=sys.stderr
          )
          continue
        else:
          if vlen < 1:
            value = [cls.prefixify(repr(value))]
          else:
            value = [cls.prefixify(value[0])] + list(value[1:])
      try:
        setattr(e, attr, value)
      except AttributeError as e2:
        print(
            "warning: %s: %s.%s: cannot set to %r: %s" %
            (current_prefix, e, attr, value, e2),
            file=sys.stderr
        )
        continue
      did_prefix = True
    return did_prefix

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

  @classmethod
  def scope(cls, msg=None, *a):
    ''' Context manager to save the current `Thread`'s stack state
        and to restore it on exit.

        This is to aid long suites which progressively add `Pfx` context
        as the suite progresses, example:

            for item in items:
                with Pfx.scope("item %s", item):
                    db_row = db.get(item)
                    Pfx.push("db_row = %r", db_row)
                    matches = db.lookup(db_row.category)
                    if not matches:
                        continue
                    Pfx.push("%d matches", len(matches):
                    ... etc etc ...
    '''

    @contextmanager
    def scope_cmgr():
      old_stack = list(cls._state.stack)
      try:
        if msg is not None:
          cls.push(msg, *a)
        yield
      finally:
        cls._state.stack[:] = old_stack

    return scope_cmgr()

  # Logger methods
  @logging_wrapper
  def exception(self, msg, *args, **kwargs):
    ''' Log an exception message to this Pfx's loggers.
    '''
    for L in self.loggers:
      L.exception(msg, *args, **kwargs)

  @logging_wrapper
  def log(self, level, msg, *args, **kwargs):
    ''' Log a message at an arbitrary log level to this Pfx's loggers.
    '''
    ## to debug format errors ## D("msg=%r, args=%r, kwargs=%r", msg, args, kwargs)
    for L in self.loggers:
      try:
        L.log(level, msg, *args, **kwargs)
      except Exception as e:  # pylint: disable=broad-except
        print(
            "%s: exception logging to %s msg=%r, args=%r, kwargs=%r: %s" %
            (self._state.prefix, L, msg, args, kwargs, e),
            file=sys.stderr
        )

  @logging_wrapper
  def debug(self, msg, *args, **kwargs):
    ''' Emit a debug log message.
    '''
    self.log(logging.DEBUG, msg, *args, **kwargs)

  @logging_wrapper
  def info(self, msg, *args, **kwargs):
    ''' Emit an info log message.
    '''
    self.log(logging.INFO, msg, *args, **kwargs)

  @logging_wrapper
  def warning(self, msg, *args, **kwargs):
    ''' Emit a warning log message.
    '''
    self.log(logging.WARNING, msg, *args, **kwargs)

  @logging_wrapper
  def error(self, msg, *args, **kwargs):
    ''' Emit an error log message.
    '''
    self.log(logging.ERROR, msg, *args, **kwargs)

  @logging_wrapper
  def critical(self, msg, *args, **kwargs):
    ''' Emit a critical log message.
    '''
    self.log(logging.CRITICAL, msg, *args, **kwargs)

def prefix():
  ''' Return the current Pfx prefix.
  '''
  return Pfx._state.prefix

def pfxprint(*a, print_func=None, **kw):
  ''' Call `print()` with the current prefix.

      The optional keyword parameter `print_func`
      provides an alternative function to the builtin `print()`.
  '''
  if print_func is None:
    print_func = print
  print_func(prefix() + ':', *a, **kw)

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
  ''' Subclass of Pfx to insert current function and caller into messages.
  '''

  def __init__(self):
    grandcaller, caller, _ = traceback.extract_stack(None, 3)
    Pfx.__init__(
        self, "at %s:%d %s(), called from %s:%d %s()", caller[0], caller[1],
        caller[2], grandcaller[0], grandcaller[1], grandcaller[2]
    )

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

@decorator
def pfx(func, message=None, message_args=()):
  ''' General purpose @pfx for generators, methods etc.

      Parameters:
      * `func`: the function or generator function to decorate
      * `message`: optional prefix to use instead of the function name
      * `message_args`: optional arguments to embed in the preifx using `%`

      Example usage:

          @pfx
          def f(....):
              ....
  '''
  fname = funcname(func)
  if message is None:
    if message_args:
      raise ValueError("no message, but message_args=%r" % (message_args,))

  if isgeneratorfunction(func):

    # persistent in-generator stack to be reused across calls to
    # the context manager
    saved_stack = []
    if message is None:
      message = funcname

    @contextdecorator
    def cmgrdeco(func, a, kw):
      ''' Context manager to note the entry `Pfx` stack height, append saved
          `Pfx` stack from earlier run, then after the iteration step save the
          top of the `Pfx` stack for next time.
      '''
      pfx_stack = Pfx._state.stack
      height = len(pfx_stack)
      pfx_stack.extend(saved_stack)
      with Pfx(message, *message_args):
        yield
      saved_stack[:] = pfx_stack[height:]
      pfx_stack[height:] = []

    wrapper = cmgrdeco(func)

  else:

    if message is None:

      def wrapper(*a, **kw):
        ''' Run function inside `Pfx` context manager.
        '''
        return pfx_call(func, *a, **kw)

    else:

      def wrapper(*a, **kw):
        ''' Run function inside `Pfx` context manager.
        '''
        with Pfx(message, *message_args):
          return func(*a, **kw)

  wrapper.__name__ = "@pfx(%s)" % (fname,)
  wrapper.__doc__ = func.__doc__
  return wrapper

@decorator
def pfx_method(method, use_str=False, with_args=False):
  ''' Decorator to provide a `Pfx` context for an instance method prefixing
      *classname.methodname*.

      If `use_str` is true (default `False`)
      use `str(self)` instead of `classname`.

      If `with_args` is true (default `False`)
      include the specified arguments in the `Pfx` context.
      If `with_args` is `True`, this includes all the arguments.
      Otherwise `with_args` should be a sequence of argument references:
      an `int` specifies one of the positional arguments
      and a string specifies one of the keyword arguments.

      Examples:

          class O:
              # just use "O.foo"
              @pfx_method
              def foo(self, .....):
                  ....
              # use the value of self instead of the class name
              @pfx_method(use_str=True)
              def foo2(self, .....):
                  ....
              # include all the arguments
              @pfx_method(with_args=True)
              def foo3(self, a, b, c, *, x=1, y):
                  ....
              # include the "b", "c" and "x" arguments
              @pfx_method(with_args=[1,2,'x'])
              def foo3(self, a, b, c, *, x=1, y):
                  ....
  '''

  fname = method.__name__

  def pfx_method_wrapper(self, *a, **kw):
    ''' Prefix messages with "type_name.method_name" or "str(self).method_name".
    '''
    classref = self if use_str else type(self).__name__
    pfxfmt, pfxargs = func_a_kw_fmt(method, *a, **kw)
    with Pfx("%s." + pfxfmt, classref, *pfxargs):
      return method(self, *a, **kw)

  pfx_method_wrapper.__doc__ = method.__doc__
  pfx_method_wrapper.__name__ = fname
  return pfx_method_wrapper

def XP(msg, *args, **kwargs):
  ''' Variation on `cs.x.X`
      which prefixes the message with the current Pfx prefix.
  '''
  if args:
    return X("%s: " + msg, prefix(), *args, **kwargs)
  return X(prefix() + DEFAULT_SEPARATOR + msg, **kwargs)

def XX(prepfx, msg, *args, **kwargs):
  ''' Trite wrapper for `XP()` to transiently insert a leading prefix string.

      Example:

          XX("NOTE!", "some message")
  '''
  with PrePfx(prepfx):
    return XP(msg, *args, **kwargs)
