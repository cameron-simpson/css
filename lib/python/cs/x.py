#!/usr/bin/python
#
# Just my X and D debugging functions.
#   - Cameron Simpson <cs@zip.com.au>
#

from __future__ import print_function
import sys

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
      with open('/dev/tty', 'w') as fp:
        fp.write(msg)
        fp.write('\n')
      return
    fp = sys.stderr
  print(msg, file=fp)
