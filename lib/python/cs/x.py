#!/usr/bin/python
#
# Just my X debugging function.
#   - Cameron Simpson <cs@zip.com.au>
#

from __future__ import print_function
import sys

DISTINFO = {
    'description': "X(), for low level debugging",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
        ],
    'install_requires': [],
}

# set to true to log as a warning
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
