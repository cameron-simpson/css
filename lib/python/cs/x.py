#!/usr/bin/python
#
# Just my X debugging function.
#   - Cameron Simpson <cs@cskk.id.au>
#

'''
X(), for low level debugging

X() is my function for low level ad hoc debug messages.
It takes a message and optional format arguments for use with `%`.
It is presented here in its own module for reuse.

It normally writes directly to `sys.stderr` but accepts an optional keyword argument `file` to specify a different filelike object.

Its behaviour may be tweaked with the globals `X_logger` or `X_via_tty`.
If `file` is not None, write to it unconditionally.
Otherwise, if X_logger then log a warning to that logger.
Otherwise, if X_via_tty then open /dev/tty and write the message to it.
Otherwise, write the message to sys.stderr.
'''

from __future__ import print_function
import sys

DISTINFO = {
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
        ],
    'install_requires': [],
}

# set to a logger to log as a warning
X_logger = None
# set to true to write direct to /dev/tty
X_via_tty = False

def X(msg, *args, **kw):
  ''' Unconditionally write the message `msg` to sys.stderr.
      If `args` is not empty, format `msg` using %-expansion with `args`.
      `file`: optional keyword argument specifying the output file.
      If `file` is not None, write to it unconditionally.
      Otherwise, if X_logger then log a warning to that logger.
      Otherwise, if X_via_tty then open /dev/tty and write the message to it.
      Otherwise, write the message to sys.stderr.
  '''
  fp = kw.pop('file', None)
  if kw:
    raise ValueError("unexpected keyword arguments: %r" % (kw,))
  msg = str(msg)
  if args:
    msg = msg % args
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
      else:
        return
    fp = sys.stderr
  print(msg, file=fp)
