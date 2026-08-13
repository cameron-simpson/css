#!/usr/bin/env python3

''' Utilities for tracing operations.
'''

class Trace(HasThreadState):
  ''' A class/decorator to trace control flow and decisions.
      This makes it possible to record function calls and their
      inner decision chains, and to show these in a nice printout
      after the fact.

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

      As a decorator:

          >>> @Trace
          ... def func(x):
          ...   with Trace(f'compute x+2') as T:
          ...     x2 = T(f'{x=} + 2', x+2)
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
          │ │ from <module>() <doctest cs.debug.Trace[4]>:2
          │ │ x2 = T("call func with 3", func(3))
          │ ├─compute x+2
          │ │ ╰─x=3 + 2 -> int                               5
          │ ╰─return -> int                                  5
          ╰─call func with 3 -> int                          5

  '''

  # class attribute holding the per-thread state stack
  perthread_state = ThreadState()

  def __new__(cls, func, *_, print=None):
    if callable(func):
      # class being used as a decorator

      def with_trace(*func_a, **func_kw):
        upT = cls.default()
        c = caller(-4)
        with cls(f'{func.__name__}(....)'
                 f'\n from {c.name}() {shortpath(c.filename)}:{c.lineno}'
                 f'\n {c.line}', upT) as subT:
          try:
            result = func(*func_a, **func_kw)
          except Exception as e:
            subT('RAISE', e)
            raise
          else:
            subT('return', result)
            return result

      return with_trace
    return super().__new__(cls)

  def __init__(self, name: str, upT=None):
    self.name = name
    self.tests = []
    if upT is None:
      upT = type(self).default()
    if upT is not None:
      upT.tests.append(self)

  def tabulate(self):
    table = [self.name]
    subtable = []
    for subtest in self.tests:
      if isinstance(subtest, tuple):
        label, result = subtest
        subtable.append([label, result])
      else:
        subtable.extend(subtest.tabulate())
    if subtable:
      table.append(tuple(subtable))
    return table

  def printt(self, **printt_kw):
    printt(*self.tabulate(), **printt_kw)

  def __call__(self, label: str, *tt_a):
    assert isinstance(label, str)
    tt_a = list(tt_a)
    result = tt_a.pop(0)
    assert not tt_a
    self.tests.append((f'{label} -> {type(result).__name__}', result))
    return result
