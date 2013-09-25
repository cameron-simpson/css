#!/usr/bin/python
#
# Facilities for terminals.
#       - Cameron Simpson <cs@zip.com.au>
#

from __future__ import print_function
import os
from subprocess import Popen, PIPE
from collections import namedtuple

WinSize = namedtuple('WinSize', 'rows columns')

def ttysize(fd):
  ''' Return a (rows, columns) tuple for the specified file descriptor.
      If the window size cannot be determined, None will be returns
      for either or both of rows and columns.
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
