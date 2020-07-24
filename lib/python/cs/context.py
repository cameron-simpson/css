#!/usr/bin/env python

''' Context managers. Initially just `stackattrs`.
'''

from contextlib import contextmanager
try:
  from contextlib import nullcontext
except ImportError:

  @contextmanager
  def nullcontext():
    ''' A simple `nullcontext` for older Pythons
    '''
    yield None

__version__ = '20200517-post'

DISTINFO = {
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [],
}

def pushattrs(o, **attr_values):
  ''' The "push" part of `stackattrs`.
      Push `attr_values` onto `o` as attributes,
      return the previous attribute values in a dict.

      This can be useful in hook/signal/callback,
      where you cannot inline a context manager.
  '''
  old_values = {}
  for attr, value in attr_values.items():
    try:
      old_value = getattr(o, attr)
    except AttributeError:
      pass
    else:
      old_values[attr] = old_value
    setattr(o, attr, value)
  return old_values

def popattrs(o, attr_names, old_values):
  ''' The "pop" part of `stackattrs`.
      Restore previous attributes of `o`
      named by `attr_names` with previous state in `old_values`.

      This can be useful in hook/signal/callback,
      where you cannot inline a context manager.
  '''
  for attr in attr_names:
    try:
      old_value = old_values[attr]
    except KeyError:
      try:
        delattr(o, attr)
      except AttributeError:
        pass
    else:
      setattr(o, attr, old_value)

@contextmanager
def stackattrs(o, **attr_values):
  ''' Context manager to push new values for the attributes of `o`
      and to restore them afterward.
      Returns a `dict` containing a mapping of the previous attribute values.
      Attributes not present are not present in the mapping.

      Restoration includes deleting attributes which were not present
      initially.

      See `stackkeys` for a flavour of this for mappings.

      Example of fiddling a programme's "verbose" mode:

          >>> class RunModes:
          ...     def __init__(self, verbose=False):
          ...         self.verbose = verbose
          ...
          >>> runmode = RunModes()
          >>> if runmode.verbose:
          ...     print("suppressed message")
          ...
          >>> with stackattrs(runmode, verbose=True):
          ...     if runmode.verbose:
          ...         print("revealed message")
          ...
          revealed message
          >>> if runmode.verbose:
          ...     print("another suppressed message")
          ...

      Example exhibiting restoration of absent attributes:

          >>> class O:
          ...     def __init__(self):
          ...         self.a = 1
          ...
          >>> o = O()
          >>> print(o.a)
          1
          >>> print(o.b)
          Traceback (most recent call last):
            File "<stdin>", line 1, in <module>
          AttributeError: 'O' object has no attribute 'b'
          >>> with stackattrs(o, a=3, b=4):
          ...     print(o.a)
          ...     print(o.b)
          ...     o.b = 5
          ...     print(o.b)
          ...     delattr(o, 'a')
          ...
          3
          4
          5
          >>> print(o.a)
          1
          >>> print(o.b)
          Traceback (most recent call last):
            File "<stdin>", line 1, in <module>
          AttributeError: 'O' object has no attribute 'b'
  '''
  old_values = pushattrs(o, **attr_values)
  try:
    yield old_values
  finally:
    popattrs(o, attr_values.keys(), old_values)

def pushkeys(d, **key_values):
  ''' The "push" part of `stackkeys`.
      Push `key_values` onto `d` as key values.
      return the previous key values in a dict.

      This can be useful in hook/signal/callback,
      where you cannot inline a context manager.
  '''
  old_values = {}
  for key, value in key_values.items():
    try:
      old_value = d[key]
    except KeyError:
      pass
    else:
      old_values[key] = old_value
    d[key] = value
  return old_values

def popkeys(d, key_names, old_values):
  ''' The "pop" part of `stackkeys`.
      Restore previous key values of `d`
      named by `key_names` with previous state in `old_values`.

      This can be useful in hook/signal/callback,
      where you cannot inline a context manager.
  '''
  for key in key_names:
    try:
      old_value = old_values[key]
    except KeyError:
      try:
        del d[key]
      except KeyError:
        pass
    else:
      d[key] = old_value

@contextmanager
def stackkeys(d, **key_values):
  ''' Context manager to push new values for the key values of `d`
      and to restore them afterward.
      Returns a `dict` containing a mapping of the previous key values.
      Keys not present are not present in the mapping.

      Restoration includes deleting key values which were not present
      initially.

      See `stackattrs` for a flavour of this for objects.

      Example of making log entries which may reference
      some higher level context log entry:

          >>> import time
          >>> global_context = {
          ...     'parent': None,
          ... }
          >>> def log_entry(desc, **kw):
          ...     print("log_entry: global_context =", repr(global_context))
          ...     entry = dict(global_context)
          ...     entry.update(desc=desc, when=time.time())
          ...     entry.update(kw)
          ...     return entry
          ...
          >>> log_entry("stand alone entry")    #doctest: +ELLIPSIS
          log_entry: global_context = {'parent': None}
          {'parent': None, 'desc': 'stand alone entry', 'when': ...}
          >>> context_entry = log_entry("high level entry")
          log_entry: global_context = {'parent': None}
          >>> context_entry                     #doctest: +ELLIPSIS
          {'parent': None, 'desc': 'high level entry', 'when': ...}
          >>> with stackkeys(global_context, parent=context_entry): #doctest: +ELLIPSIS
          ...     print(repr(log_entry("low level event")))
          ...
          log_entry: global_context = {'parent': {'parent': None, 'desc': 'high level entry', 'when': ...}}
          {'parent': {'parent': None, 'desc': 'high level entry', 'when': ...}, 'desc': 'low level event', 'when': ...}
          >>> log_entry("another standalone entry")    #doctest: +ELLIPSIS
          log_entry: global_context = {'parent': None}
          {'parent': None, 'desc': 'another standalone entry', 'when': ...}
  '''
  old_values = pushkeys(d, **key_values)
  try:
    yield old_values
  finally:
    popkeys(d, key_values.keys(), old_values)
