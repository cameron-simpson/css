#!/usr/bin/python
#
# Facilities for terminals.
#       - Cameron Simpson <cs@zip.com.au>
#

import os
import re
from collections import namedtuple

WinSize = namedtuple('WinSize', 'rows columns')

def winsize(f):
  '''   Return a (rows, columns) tuple or None for the specified file object.
  '''
  fd = os.dup(f.fileno()) # obtain fresh fd to pass to the shell
  sttycmd = "stty -a <&" + str(fd) + " 2>/dev/null"
  stty = os.popen(sttycmd).read()
  os.close(fd)
  m = re.compile(r' rows (\d+); columns (\d+)').search(stty)
  if not m:
    return None
  return WinSize( int(m.group(1)), int(m.group(2)) )
