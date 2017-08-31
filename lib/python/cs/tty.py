#!/usr/bin/python
#
# Facilities for terminals.
#       - Cameron Simpson <cs@cskk.id.au>
#

from __future__ import print_function
import os

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
    'install_requires': [],
}

import re
import sys
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
    m = re.compile(r' (\d+) rows; (\d+) columns').search(stty)
    if m:
      rows, columns = int(m.group(1)), int(m.group(2))
    else:
      rows, columns = None, None
  return WinSize( rows, columns )

_ti_setup = False

def setupterm(*args):
  global _ti_setup
  if _ti_setup is False:
    termstr = None
    fd = None
    if args:
      termstr = args.pop(0)
      if args:
        fd = args.pop(0)
        if args:
          raise ValueError("extra arguments after termstr and fd: %r" % (args,))
    if termstr is None:
      import os
      termstr = os.environ['TERM']
    if fd is None:
      fd = sys.stdout.fileno()
    import curses
    curses.setupterm(termstr, fd)
    _ti_setup = True

def statusline_bs(text, reverse=False, xpos=None, ypos=None):
  ''' Return a byte string to update the status line.
  '''
  from curses import tigetstr, tparm, tigetflag
  setupterm()
  if tigetflag('hs'):
    seq = ( tigetstr('tsl'),
            tigetstr('dsl'),
            tigetstr('rev') if reverse else b'',
            text.encode(),
            tigetstr('fsl')
          )
  else:
    # save cursor position, position, reverse, restore position
    if xpos is None:
      xpos = 0
    if ypos is None:
      ypos = 0
    seq = ( tigetstr('sc'),   # save cursor position
            tparm(tigetstr("cup"), xpos, ypos),
            tigetstr('rev') if reverse else b'',
            text.encode(),
            tigetstr('el'),
            tigetstr('rc')
          )
  return b''.join(seq)

def statusline(text, fd=None, reverse=False, xpos=None, ypos=None, noflush=False):
  ''' Update the status line.
  '''
  if fd is None:
    fd = sys.stdout.fileno()
  os.write(fd, statusline_bs(text, reverse=reverse, xpos=xpos, ypos=ypos))
