#!/usr/bin/python
#
# Facilities for terminals.
#       - Cameron Simpson <cs@zip.com.au>
#

from __future__ import print_function
import os
from subprocess import Popen, PIPE

def ttysize(fd):
  ''' Return a (rows, columns) tuple for the specified file descriptor.
      If the window size cannot be determined, None will be returns
      for either or both of rows and columns.
  '''
  # obtain fresh fd to pass to the shell
  fd = os.dup(fd)
  P = Popen(['stty', '-a'], stdin=fd, stdout=PIPE)
  stty = P.stdout.read()
  xit = P.wait()
  os.close(fd)
  P = None
  rows = None
  columns = None
  if xit == 0:
    stty = stty.decode()
    for field in [ _.strip() for _ in stty.split('\n')[0].split(';') ]:
      if field.endswith(' columns'):
        columns = int(field[:-8])
      elif field.startswith("columns "):
        columns = int(field[8:])
      elif field.endswith(' rows'):
        rows = int(field[:-5])
      elif field.startswith("rows "):
        rows = int(field[5:])
  return rows, columns

if __name__ == '__main__':
  print(winsize(0))
