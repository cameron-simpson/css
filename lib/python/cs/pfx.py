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

    from cs.pfx import Pfx
    ...
    def parser(filename):
      with Pfx(filename):
        with open(filename) as f:
          for lineno, line in enumerate(f, 1):
            with Pfx(lineno):
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

The main items supplied by this module are:
- `Pfx`: a context manager for wrapping code in an addition prefix context
- `@pfx`: a decorator for a function to wrap calls to the function
- `@pfx_method`: like `@pfx` but for instance methods
- `pfx_call(func,*a,**kw)`: call `func(*a,**kw)` inside a prefix context
- `pfxprint()`: call `print()` with a leading prefix
'''

import builtins
from contextlib import contextmanager
from functools import partial
from inspect import isclass, isgeneratorfunction
import logging
import sys
import traceback

from cs.context import Contextual
from cs.deco import OBSOLETE, decorator, fmtdoc, logging_wrapper
from cs.py.func import funcname, func_a_kw_fmt
from cs.x import X

__version__ = '20260912-post'

DISTINFO = {
    'description':
    "Easy context prefixes for messages.",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.context',
        'cs.deco',
        'cs.py.func>=func_a_kw_fmt',
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
    yield from iterable

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

class Pfx(Contextual):
  ''' A context manager to maintain a per-thread stack of message prefixes.
  '''

  def __init__(
      self,
      mark: str,
      *args,
      absolute=False,
      loggers=None,
      parent=None,
      print=False,
  ):
    ''' Initialise a new Pfx instance.

        Parameters:
        * `mark`: message prefix string
        * `args`: if not empty, apply to the prefix string with `%`
        * `absolute`: optional keyword argument, default `False`. If
          true, this message forms the base of the message prefixes;
          earlier prefixes will be suppressed.
        * `loggers`: which loggers should receive log messages.
        * `parent`: optional parent for this `Pfx`, defaultthe ambient `Pfx`
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
    if print:
      # True/False
      if isinstance(print, bool):
        print = builtins.print
      # a fil-like object
      elif not callable(print) and hasattr(print, 'write'):
        print = partial(builtins.print, file=print)
    if absolute:
      if parent is not None:
        raise ValueError('parent must be None if absolute is true')
      parent = None
    elif parent is None:
      parent = self.__class__.current()
    self.parent = parent
    self.print = print
    self.mark = mark
    self.mark_args = args
    self.print = print
    self._loggers = None
    if loggers is not None:
      if not hasattr(loggers, '__getitem__'):
        loggers = (loggers,)
      self.logto(loggers)

  def __str__(self):
    ''' The `str` of a `Pfx` is its mark.
    '''
    mark = self.mark
    if self.mark_args:
      mark = mark % self.mark_args
    return mark

  def __exit__(self, exc_type, exc_value, tb):
    if exc_value is not None:
      self.prefixify_exception(exc_value)
    return super().__exit__(exc_type, exc_value, tb)

  @property
  @OBSOLETE('Pfx.__str__')
  def umark(self):
    ''' Return the message mark for use with this Pfx.
    '''
    return str(self)

  @classmethod
  def prefixify(cls, text, *, sep: str = ': '):
    ''' Return `text` with the current prefix prepended.
        *NOTE*: returns `text` unchanged if it is not a string.
    '''
    if not isinstance(text, str):
      return text
    return cls.current().prefix(text, sep=sep)

  def prefix(self, text=None, *, sep=': '):
    ''' Return `str(text)` with the prefix prepended.
    '''
    prefixes = []
    if text is not None:
      prefixes.append(str(text))
    P = self
    while P is not None:
      prefixes.append(str(P))
      P = P.parent
    return sep.join(reversed(prefixes))

  @classmethod
  def prefixify_exception(cls, e, *, sep=': ') -> bool:
    ''' Modify the supplied exception `e` with the current prefix.
        The original value of some .attr is preserved as .{attr}_without_prefix.
        Return `True` if modified, `False` if unable to modify.
    '''
    if hasattr(e, '_'):
      return True
    # preserve the original str(e) for concise messages
    e._ = str(e)
    # now try to modify the attributes to prefix the first one
    P = cls.current()
    if P is None:
      return False
    current_prefix = P.prefix(sep=sep)
    did_prefix = False
    for attr in 'args', 'message', 'msg', 'reason', 'strerror':
      try:
        value = getattr(e, attr)
      except AttributeError:
        continue
      if value is None:
        continue
      ovalue = value
      # special case various known exception type attributes
      if attr == 'args' and isinstance(e, OSError):
        # prefixify the first string
        value = list(value)
        for i, v in enumerate(value):
          if isinstance(v, str):
            value[i] = cls.prefixify(v)
            did_prefix = True
            break
        value = tuple(value)
      elif attr == 'args' and isinstance(e, LookupError):
        if (isinstance(value, tuple) and value):
          value0 = value[0]
          if (isinstance(value0, str) and value0.startswith("'")
              and value0.endswith("'")):
            # args[0] is the key, do not fiddle with it
            continue
          value = (cls.prefixify(value0), *value[1:])
        else:
          continue
      elif attr == 'message' and not isinstance(value, str):
        # saw django.core.exceptions.ValidationError.message
        # is not a string but some kind of proxy object
        continue
      elif isinstance(value, str):
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
            value = [P.prefix(repr(value))]
          else:
            value = [P.prefix(value[0]), *value[1:]]
      t0 = type(ovalue)
      t1 = type(value)
      if t0 is not t1:
        if set((t0, t1)) == set((list, tuple)):
          # convert list back to tuple or tuple back to list
          value = t0(value)
        else:
          X(
              "prefixify_exception: %s.%s.%s:%s.%s:%r is a different type from the new value:%s:%r",
              e.__class__.__module__, e.__class__.__name__, attr,
              ovalue.__class__.__module__, ovalue.__class__.__name__, ovalue,
              value.__class__.__name__, value
          )
      try:
        setattr(e, attr + '_without_prefix', ovalue)
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
        within a surrounding `Pfx` context with the current mark string.

        This is intended for deferred call facilities like
        `Later` and `futures`.
    '''
    pfx2 = Pfx(self.mark, absolute=True, loggers=self.loggers)

    def pfxfunc():
      with pfx2:
        return func(*a, **kw)

    return pfxfunc

  @property
  def loggers(self):
    ''' Return the loggers to use for this `Pfx` instance.
    '''
    P = self
    while P is not None:
      loggers = P._loggers
      if loggers is not None:
        return loggers
      P = P.parent
    return (logging.getLogger(),)

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
    for logger in self.loggers:
      logger.log(level, msg, *args, **kwargs)

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
  ''' Return the current `Pfx` prefix.
  '''
  return Pfx.current().prefix

def pfxprint(*a, print=None, **print_kw):
  ''' Call `print()` with the current prefix.

      The optional keyword parameter `print`
      provides an alternative function to the builtin `print()`.
  '''
  if print is None:
    print = builtins.print
  print(prefix() + ':', *a, **print_kw)

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

def PfxThread(target, **kw):
  ''' Factory function returning a `Thread`
      which presents the current prefix as context.
  '''
  from cs.threads import HasThreadState  # pylint: disable=import-outside-toplevel

  current_prefix = prefix()

  def PfxThread_run(*a, **kw):
    with Pfx(current_prefix):
      return target(*a, **kw)

  return HasThreadState.Thread(target=PfxThread_run, **kw)

@decorator
def pfx(func, message=None, *message_args):
  ''' General purpose @pfx for generators, methods etc.

      Parameters:
      * `func`: the function or generator function to decorate
      * `message`: optional prefix to use instead of the function name
      * `message_args`: optional arguments to embed in the prefix using `%`

      Example usage:

          @pfx
          def f(....):
              ....
  '''
  fname = funcname(func)
  if message is None:
    if message_args:
      raise ValueError("no message, but message_args=%r" % (message_args,))
    message = fname

  def make_message_args(*a, **kw):
    ''' Compute the message args to use inside the called function.
    '''
    return [
        a[ma] if isinstance(ma, int) else
        kw[ma] if isinstance(ma, str) else "%s:%r" %
        (ma.__class__.__name__, repr(ma)) for ma in message_args
    ]

  if isgeneratorfunction(func):
    # a generator function must stash any Pfx state from the generator as it runs

    def pfx_generator_wrapper(*a, **kw):
      # obtain the generator from the generator function
      gen = pfx_call(func, *a, **kw)
      if message is None:
        pfxf, pfxav = func_a_kw_fmt(func, *a, **kw)
      else:
        pfxf = message
        pfxav = make_message_args(*a, **kw)
      pfx_stack = Pfx._state.stack
      gen_pfx_stack = []
      while True:
        # note the height of the Pfx stackwithout the generator's tsack
        height = len(pfx_stack)
        # add on the generator's stack from before
        pfx_stack.extend(gen_pfx_stack)
        try:
          with Pfx(pfxf, *pfxav):
            result = next(gen)
        except StopIteration:
          break
        finally:
          # stash the in-generator Pfx stack
          gen_pfx_stack = pfx_stack[height:]
          # restore to how it was
          pfx_stack[height:] = []
        yield result

    return pfx_generator_wrapper

  # TODO: async functions? much like generators?

  # an ordinary function
  def pfx_function_wrapper(*a, **kw):
    if message is None:
      pfxf, pfxav = func_a_kw_fmt(func, *a, **kw)
    else:
      pfxf = message
      pfxav = make_message_args(*a, **kw)
    with Pfx(pfxf, *pfxav):
      return func(*a, **kw)

  return pfx_function_wrapper

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
  fname = getattr(method, '__name__', repr(method))

  def pfx_method_wrapper(self, *a, **kw):
    ''' Prefix messages with "type_name.method_name" or "str(self).method_name".
    '''
    classref = (
        self
        if use_str else self.__name__ if isclass(self) else type(self).__name__
    )
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
    return X("%s%s" + msg, prefix(), DEFAULT_SEPARATOR, *args, **kwargs)
  return X(prefix() + DEFAULT_SEPARATOR + msg, **kwargs)

def XX(prepfx, msg, *args, **kwargs):
  ''' Trite wrapper for `XP()` to transiently insert a leading prefix string.

      Example:

          XX("NOTE!", "some message")
  '''
  with PrePfx(prepfx):
    return XP(msg, *args, **kwargs)
