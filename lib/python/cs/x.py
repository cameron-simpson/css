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

def X(msg, *args, **kwargs):
  ''' Unconditionally write the message `msg` to sys.stderr.
      If `args` is not empty, format `msg` using %-expansion with `args`.
  '''
  msg = str(msg)
  if args:
    msg = msg % args
  if X_logger:
    # NB: ignores any kwargs
    X_logger.warning(msg)
  elif X_via_tty:
    # NB: ignores any kwargs
    with open('/dev/tty', 'w') as fp:
      fp.write(msg)
      fp.write('\n')
      fp.flush()
  else:
    fp = kwargs.pop('file', None)
    if fp is None:
      fp = sys.stderr
    print(msg, file=fp)
