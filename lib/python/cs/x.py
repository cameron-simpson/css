#!/usr/bin/python
#
# Just my X debugging function.
#   - Cameron Simpson <cs@cskk.id.au>
#

'''
X(), for low level debugging.

X() is my function for low level ad hoc debug messages.
It takes a message and optional format arguments for use with `%`.
It is presented here in its own module for reuse:

    from cs.x import X
    ...
    X("foo: x=%s, a=%r", x, a)

It normally writes directly to `sys.stderr` but accepts an optional
keyword argument `file` to specify a different filelike object.

The following globals are further tune its behaviour,
absent the `file=` parameter:
* `X_logger`: if not `None` then log a warning to that logger
* `X_via_tty`: if true then open `/dev/tty` and write the message to it
* `X_discard`: if true then discard the message
Otherwise write the message to `sys.stderr`.

`X_via_tty` defaults to true if the environment variable `$CS_X_VIA_TTY`
has a nonempty value, false otherwise.
This is handy for getting debugging out of test suites,
which often divert `sys.stderr`.

`X_discard`'s default value is `not sys.stderr.isatty()`.
'''

from __future__ import print_function
import os
import sys
from cs.ansi_colour import colourise

__version__ = '20210123-post'

DISTINFO = {
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': ['cs.ansi_colour'],
}

# discard output? the default if sys.stderr is not a tty
try:
  isatty = sys.stderr.isatty
except AttributeError:
  X_discard = True
else:
  X_discard = not isatty()
# set to a logger to log as a warning
X_logger = None
# set to true to write direct to /dev/tty
X_via_tty = os.environ.get('CS_X_VIA_TTY', '')
X_default_colour = os.environ.get('CS_X_COLOUR')

def X(msg, *args, **kw):
  ''' Unconditionally write the message `msg`.

      If there are positional arguments after `msg`,
      format `msg` using %-expansion with those arguments.

      Keyword arguments:
      * `file`: optional keyword argument specifying the output file.
      * `colour`: optional text colour.
        If specified, surround the message with ANSI escape sequences
        to render the text in that colour.

      If `file` is not `None`, write to it unconditionally.
      Otherwise, the following globals are consulted in order:
      * `X_logger`: if not `None` then log a warning to that logger
      * `X_via_tty`: if true then open `/dev/tty` and write the message to it
      * `X_discard`: if true then discard the message
      Otherwise write the message to `sys.stderr`.

      `X_logger` is `None` by default.
      `X_via_tty` is true if the environment variable `$CS_X_VIA_TTY` is not empty,
      false otherwise.
      `X_discard` is true unless `sys.stderr.isatty()` is true.
  '''
  fp = kw.pop('file', None)
  colour = kw.pop('colour', X_default_colour)
  if kw:
    raise ValueError("unexpected keyword arguments: %r" % (kw,))
  msg = str(msg)
  if args:
    msg = msg % args
  if colour:
    msg = colourise(msg, colour=colour)
  if fp is None:
    if X_logger:
      # NB: ignores any kwargs
      X_logger.warning(msg)
      return
    if X_via_tty:
      # NB: ignores any kwargs
      try:
        with open('/dev/tty', 'w') as fp:
          fp.write(msg)
          fp.write('\n')
      except (IOError, OSError) as e:
        X("X: cannot write to /dev/tty: %s", e, file=sys.stderr)
        X(msg, file=sys.stderr)
      return
    if X_discard:
      return
    fp = sys.stderr
  print(msg, file=fp)

if os.environ.get('CS_X_BUILTIN', ''):
  try:
    import builtins
  except ImportError:
    pass
  else:
    builtins.X = X

def Xtty(msg, *args, **kw):
  ''' Call `X()` with `X_via_tty` set to `True`.

      *Note*:
      this is now obsoleted by the `$CS_X_VIA_TTY` environment variable.

      This supports using:

          from cs.x import Xtty as X

      when hacking on tests without the tedious shuffle:

          from cs.x import X
          import cs.x; cs.x.X_via_tty = True

      which I did _a lot_ to get timely debugging when fixing test failures.
  '''
  global X_via_tty  # pylint: disable=global-statement
  old = X_via_tty
  X_via_tty = True
  X(msg, *args, **kw)
  X_via_tty = old

def Y(msg, *a, **kw):
  ''' Wrapper for `X()` rendering in yellow.
  '''
  X(msg, *a, colour='yellow', **kw)
