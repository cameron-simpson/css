#!/usr/bin/python
#
# Just my X debugging function.
#   - Cameron Simpson <cs@cskk.id.au>
#

'''
X(), for low level debugging

X() is my function for low level ad hoc debug messages.
It takes a message and optional format arguments for use with `%`.
It is presented here in its own module for reuse:

    from cs.x import X
    ...
    X("foo: x=%s, a=%r", x, a)

It normally writes directly to `sys.stderr` but accepts an optional
keyword argument `file` to specify a different filelike object.
If `file` is not specified, its behaviour is further tweaked with
the globals `X_discard`, `X_logger` and `X_via_tty`:
if X_logger then log a warning to that logger;
otherwise if X_via_tty then open /dev/tty and write the message to it;
otherwise if X_discard then discard the message;
otherwise write the message to sys.stderr.
`X_discard`'s default value is `not sys.stderr.isatty()`.
'''

from __future__ import print_function
import sys
from cs.ansi_colour import colourise

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
X_via_tty = False

def X(msg, *args, **kw):
  ''' Unconditionally write the message `msg`.

      If there are positional arguments after `msg`,
      format `msg` using %-expansion with those arguments.

      Keyword arguments:
      * `file`: optional keyword argument specifying the output file.
      * `colour`: optional text colour.
        If specified, surround the message with ANSI escape sequences
        to render the text in that colour.

      If `file` is not None, write to it unconditionally;
      otherwise if `X_logger` then log a warning to that logger;
      otherwise if `X_via_tty` then open `'/dev/tty'` and write the message to it;
      otherwise if `X_discard` then discard the message;
      otherwise write the message to sys.stderr.
  '''
  fp = kw.pop('file', None)
  colour = kw.pop('colour', None)
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

def Xtty(msg, *args, **kw):
  ''' Call `X()` with `X_via_tty` set to `True`.

      This supports using:

          from cs.x import Xtty as X

      when hacking on tests without the tedious shuffle:

          from cs.x import X
          import cs.x; cs.x.X_via_tty = True

      which I did _a lot_ to get timely debugging when fixing test failures.
  '''
  global X_via_tty
  old = X_via_tty
  X_via_tty = True
  X(msg, *args, **kw)
  X_via_tty = old
