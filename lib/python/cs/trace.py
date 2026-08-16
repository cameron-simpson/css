#!/usr/bin/env python3

''' Utilities for tracing operations.
'''

import builtins
from os.path import relpath

from cs.fs import shortpath
from cs.lex import printt
from cs.py.stack import caller
from cs.threads import HasThreadState, ThreadState

class Trace(HasThreadState):
  ''' A class/decorator to trace control flow and decisions.
      This makes it possible to record function calls and their
      inner decision chains, and to show these in a nice printout
      after the fact.

      A new trace object adds itself to the records of the ambient trace object.

      A trace object supports the context manager protocol, making
      it the ambient object, so that it accrues any new trace objects
      make inside the context.

          with Trace("name') as T:
              ... add records via T ...

      Calling a trace object adds a new record to the trace

          if T("test x==2", x==2):
              T("acting on x==2")
          else:
              T("x != 2")

      As a trace object:

          >>> from builtins import print
          >>> with Trace("decide!") as T:
          ...   print("start")
          ...   if T("test 1 for never", 1==2):
          ...     print("never")
          ...   elif T("test 2 for always", 1==1):
          ...     print("always")
          ...     with Trace("inside test 2", T) as T2:
          ...       assert T2 in T.tests
          ...       if T2("inside1",1==1):
          ...         print("true")
          ...       else:
          ...         print("false")
          ...
          start
          always
          true
          >>> T.printt()
          decide!
          ├─test 1 for never -> bool   False
          ├─test 2 for always -> bool  True
          ╰─inside test 2
            ╰─inside1 -> bool          True

      As a decorator it calls the function with an additional named
      argument `T` which is the `Trace` instance for that call of
      the function:

          >>> @Trace
          ... def func(x, T):
          ...   x2 = T(f'{x=} + 2', x+2)
          ...   return x2
          ...
          >>> with Trace("func trace") as T:
          ...   x2 = T("call func with 3", func(3))
          ...   print("x2", x2)
          ...
          x2 5
          >>> T.printt() # doctest: +ELLIPSIS
          func trace
          ├─func(....)
          │ │ from <module>() <doctest cs.trace.Trace[4]>:2
          │ │ x2 = T("call func with 3", func(3))
          │ ├─x=3 + 2 -> int                                 5
          │ ╰─return -> int                                  5
          ╰─call func with 3 -> int                          5

  '''

  # class attribute holding the per-thread state stack
  perthread_state = ThreadState()

  def __new__(cls, func, *_, print=False):  # noqa: A002
    ''' Intercept object creation for use as a decorator.
        If `func` is a callable, decorate it.
        otherwise fall through to normal class instantiation.

        The decorated function is passed an additional named `T`
        keyword parameter being the `Trace` instance created for
        the function call and return, ready for additional records.
    '''
    if callable(func):
      # class being used as a decorator

      def traced_func(*func_a, **func_kw):
        c = caller(-4)
        path = relpath(c.filename)
        if path.startswith('../'):
          path = shortpath(c.filename)
        with cls(f'{func.__name__}(....)'
                 f'\n from {c.name}() {path}:{c.lineno}'
                 f'\n {c.line}') as T:
          try:
            result = func(*func_a, T=T, **func_kw)
          except Exception as e:
            T('RAISE', e, print=print)
            raise
          else:
            T('return', result, print=print)
            return result

      return traced_func
    assert print is False
    return super().__new__(cls)

  def __init__(self, name: str, upT=None):
    self.name = name
    self.tests = []
    if upT is None:
      upT = type(self).default()
    if upT is not None:
      upT.tests.append(self)

  def tabulate(self):
    ''' Tabulate this trace object for use with `cs.lex.printt()`.
    '''
    table = [self.name]
    subtable = []
    for subtest in self.tests:
      if isinstance(subtest, tuple):
        label, result = subtest
        subtable.append([f' {label}', result])
      else:
        subtable.extend(subtest.tabulate())
    if subtable:
      table.append(tuple(subtable))
    return table

  def printt(self, **printt_kw):
    ''' Use `cs.lex.printt()` to print this trace object.
        Keyword arguments are passed through.
    '''
    printt(*self.tabulate(), **printt_kw)

  def __call__(self, label: str, result='', print=False):  # noqa: A002
    ''' Calling the trace object records `(abel,result)` and
        optionally `print`s.
    '''
    if print is False:
      print = lambda *_, **__: None
    elif print is True:
      print = builtins.print
    elif not callable(print):
      raise TypeError(
          f'print should be False, True or a print()-compatible callable, got {print=}'
      )
    assert isinstance(label, str)
    print(
        f'{type(self).__name__}: {label}: {type(result).__name__}:{result!r}'
    )
    self.tests.append((f'{label} -> {type(result).__name__}', result))
    return result
