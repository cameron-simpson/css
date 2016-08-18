#!/usr/bin/python
#
# Facilities for terminals.
#       - Cameron Simpson <cs@zip.com.au>
#

from __future__ import print_function

DISTINFO = {
    'description': "functions related to terminals",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Environment :: Console",
        "Operating System :: POSIX",
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
        "Topic :: Terminals",
        ],
}

import re
from subprocess import Popen, PIPE
from collections import namedtuple

WinSize = namedtuple('WinSize', 'rows columns')

def ttysize(fd):
  ''' Return a (rows, columns) tuple for the specified file descriptor.
      If the window size cannot be determined, None will be returned
      for either or both of rows and columns.
  '''
  if not isinstance(fd, int):
    fd = fd.fileno()
  P = Popen(['stty', '-a'], stdin=fd, stdout=PIPE, universal_newlines=True)
  stty = P.stdout.read()
  xit = P.wait()
  if xit != 0:
    return None
  m = re.compile(r' rows (\d+); columns (\d+)').search(stty)
  if m:
    rows, columns = int(m.group(1)), int(m.group(2))
  else:
    rows, columns = None, None
  return WinSize( rows, columns )
