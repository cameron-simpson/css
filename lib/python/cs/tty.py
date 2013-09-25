#!/usr/bin/python
#
# Facilities for terminals.
#       - Cameron Simpson <cs@zip.com.au>
#

import os
import re
from subprocess import Popen, PIPE
from collections import namedtuple

WinSize = namedtuple('WinSize', 'rows columns')

def winsize(f):
  '''   Return a (rows, columns) tuple or None for the specified file object.
  '''
  P = Popen(['stty', '-a'], stdin=f, stdout=PIPE, universal_newlines=True)
  stty = P.stdout.read()
  xit = P.wait()
  if xit != 0:
    return None
  m = re.compile(r' rows (\d+); columns (\d+)').search(stty)
  if not m:
    return None
  return WinSize( int(m.group(1)), int(m.group(2)) )
