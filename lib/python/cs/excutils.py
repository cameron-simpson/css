#!/usr/bin/python -tt
#
# Convenience facilities for exceptions.
#       - Cameron Simpson <cs@cskk.id.au>
#

r'''
Convenience facilities for managing exceptions.
'''

import sys
import traceback
from cs.deco import decorator
from cs.logutils import error
from cs.py.func import funcname

__version__ = '20210123-post'

DISTINFO = {
    'description':
    "Convenience facilities for managing exceptions.",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': ['cs.deco', 'cs.logutils', 'cs.py.func'],
}

if sys.hexversion >= 0x03000000:
  exec("def raise_from(src_exc, dst_exc): raise dst_exc from src_exc")
else:
  exec("def raise_from(src_exc, dst_exc): raise dst_exc")

def return_exc_info(func, *args, **kwargs):
  ''' Run the supplied function and arguments.
      Return `(func_return, None)`
      in the case of successful operation
      and `(None, exc_info)` in the case of an exception.

      `exc_info` is a 3-tuple of `(exc_type, exc_value, exc_traceback)`
      as returned by `sys.exc_info()`.
      If you need to protect a whole suite and would rather not move it
      into its own function, consider the NoExceptions context manager.
  '''
  try:
    result = func(*args, **kwargs)
  except Exception:
    return None, tuple(sys.exc_info())
  return result, None

def returns_exc_info(func):
  ''' Decorator function to wrap functions whose exceptions should be caught,
      such as inside event loops or worker threads.

      It causes a function to return `(func_return, None)`
      in the case of successful operation
      and `(None, exc_info)` in the case of an exception.

      `exc_info` is a 3-tuple of `(exc_type, exc_value, exc_traceback)`
      as returned by `sys.exc_info()`.
  '''

  def returns_exc_info_wrapper(*args, **kwargs):
    return return_exc_info(func, *args, **kwargs)

  return returns_exc_info_wrapper

def noexc(func):
  ''' Decorator to wrap a function which should never raise an exception.
      Instead, any raised exception is attempted to be logged.

      A significant side effect is of course that if the function raises an
      exception it now returns `None`.
      My primary use case is actually to wrap logging functions,
      which I have had abort otherwise sensible code.
  '''

  def noexc_wrapper(*args, **kwargs):
    from cs.logutils import exception
    from cs.x import X
    try:
      return func(*args, **kwargs)
    except Exception:
      try:
        exception(
            "exception calling %s(%s, **(%s))", func.__name__, args, kwargs
        )
      except Exception as e:
        try:
          X(
              "exception calling %s(%s, **(%s)): %s", func.__name__, args,
              kwargs, e
          )
        except Exception:
          pass

  noexc_wrapper.__name__ = 'noexc(%s)' % (func.__name__,)
  return noexc_wrapper

def noexc_gen(func):
  ''' Decorator to wrap a generator which should never raise an exception.
      Instead, any raised exception is attempted to be logged and iteration ends.

      My primary use case is wrapping generators chained in a pipeline,
      as in cs.later.Later.pipeline.
  '''
  from cs.logutils import exception
  from cs.x import X

  def noexc_gen_wrapper(*args, **kwargs):
    try:
      it = iter(func(*args, **kwargs))
    except Exception as e0:
      try:
        exception(
            "exception calling %s(*%s, **(%s)): %s", func.__name__, args,
            kwargs, e0
        )
      except Exception as e2:
        try:
          X(
              "exception calling %s(*%s, **(%s)): %s", func.__name__, args,
              kwargs, e2
          )
        except Exception:
          pass
      return
    while True:
      try:
        item = next(it)
      except StopIteration:
        raise
      except Exception as e:
        try:
          exception(
              "exception calling next(%s(*%s, **(%s))): %s", func.__name__,
              args, kwargs, e
          )
        except Exception:
          try:
            X(
                "exception calling next(%s(*%s, **(%s))): %s", func.__name__,
                args, kwargs, e
            )
          except Exception:
            pass
        return
      else:
        yield item

  noexc_gen_wrapper.__name__ = 'noexc_gen(%s)' % (func.__name__,)
  return noexc_gen_wrapper

@decorator
def transmute(func, exc_from, exc_to=None):
  ''' Decorator to transmute an inner exception to another exception type.

      The motivating use case is properties in a class with a
      `__getattr__` method;
      if some inner operation of the property function raises `AttributeError`
      then the property is bypassed in favour of `__getattr__`.
      Confusion ensues.

      In principle this can be an issue with any exception raised
      from "deeper" in the call chain, which can be mistaken for a
      "shallow" exception raised by the function itself.
  '''
  if exc_to is None:
    exc_to = RuntimeError

  def transmute_transmutor_wrapper(*a, **kw):
    try:
      return func(*a, **kw)
    except exc_from as src_exc:
      # pylint: disable=unidiomatic-typecheck
      dst_exc = (
          exc_to(src_exc) if type(exc_to) is type else exc_to(
              "inner %s:%s transmuted to %s" %
              (type(src_exc), src_exc, exc_to)
          )
      )
      # TODO: raise from for py3
      raise_from(src_exc, dst_exc)  # pylint: disable=undefined-variable
      raise RuntimeError("NOTREACHED")

  return transmute_transmutor_wrapper

def unattributable(func):
  ''' Decorator to transmute `AttributeError` into a `RuntimeError`.
  '''
  return transmute(AttributeError, RuntimeError)(func)

def safe_property(func):
  ''' Substitute for @property which lets AttributeErrors escape as RuntimeErrors.
  '''
  return property(unattributable(func))

def unimplemented(func):
  ''' Decorator for stub methods that must be implemented by a stub class.
  '''

  def unimplemented_wrapper(self, *a, **kw):
    raise NotImplementedError(
        "%s.%s(*%s, **%s)" % (type(self), func.__name__, a, kw)
    )

  return unimplemented_wrapper

class NoExceptions(object):
  ''' A context manager to catch _all_ exceptions and log them.

      Arguably this should be a bare try...except but that's syntacticly
      noisy and separates the catch from the top.
      For simple function calls `return_exc_info()` is probably better.
  '''

  def __init__(self, handler):
    ''' Initialise the `NoExceptions` context manager.

        The `handler` is a callable which
        expects `(exc_type,exc_value,traceback)`
        and returns `True` or `False`
        for the `__exit__` method of the manager.
        If `handler` is `None`, the `__exit__` method
        always returns `True`, suppressing any exception.
    '''
    self.handler = handler

  def __enter__(self):
    pass

  def __exit__(self, exc_type, exc_value, tb):
    if exc_type is not None:
      if self.handler is not None:
        return self.handler(exc_type, exc_value, tb)
      # report handled exception
      from cs.logutils import warning
      warning("IGNORE  " + str(exc_type) + ": " + str(exc_value))
      for line in traceback.format_tb(tb):
        warning("IGNORE> " + line[:-1])
    return True

def LogExceptions(conceal=False):
  ''' Wrapper for `NoExceptions` which reports exceptions and optionally
      suppresses them.
  '''
  from cs.logutils import exception

  def handler(exc_type, exc_value, exc_tb):
    exception("EXCEPTION: <%s> %s", exc_type, exc_value)
    return conceal

  return NoExceptions(handler)

def logexc(func):
  ''' Decorator to log exceptions and reraise.
  '''

  def logexc_wrapper(*a, **kw):
    with LogExceptions():
      return func(*a, **kw)

  try:
    name = func.__name__
  except AttributeError:
    name = str(func)
  logexc_wrapper.__name__ = 'logexc(%s)' % (name,)
  return logexc_wrapper

def logexc_gen(genfunc):
  ''' Decorator to log exceptions and reraise for generators.
  '''

  def logexc_gen_wrapper(*a, **kw):
    with LogExceptions():
      it = genfunc(*a, **kw)
      while True:
        try:
          item = next(it)
        except StopIteration:
          return
        yield item

  logexc_gen_wrapper.__name__ = 'logexc_gen(%s)' % (genfunc.__name__,)
  return logexc_gen_wrapper

@decorator
def exc_fold(func, exc_types=None, exc_return=False):
  ''' Decorator to catch specific exception types and return a defined default value.
  '''

  def wrapped(*a, **kw):
    try:
      return func(*a, **kw)
    except exc_types as e:
      error("%s", e)
      return exc_return

  wrapped.__name__ = (
      "@exc_fold[%r=>%r]%s" % (exc_types, exc_return, funcname(func))
  )
  doc = getattr(func, '__doc__', '')
  if doc:
    wrapped.__doc__ = wrapped.__name__ + '\n' + doc
  return wrapped

if __name__ == '__main__':
  import cs.excutils_tests
  cs.excutils_tests.selftest(sys.argv)
