#!/usr/bin/python
#
# Facilities for terminals.
#       - Cameron Simpson <cs@cskk.id.au>
#

''' Functions related to terminals.
'''

from __future__ import print_function
from collections import namedtuple
import os
import re
from subprocess import Popen, PIPE
import sys
from cs.gimmicks import warning

__version__ = '20200521'

DISTINFO = {
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Environment :: Console",
        "Operating System :: POSIX",
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
        "Topic :: Terminals",
    ],
    'install_requires': ['cs.gimmicks'],
}

WinSize = namedtuple('WinSize', 'rows columns')

def ttysize(fd):
  ''' Return a (rows, columns) tuple for the specified file descriptor.

      If the window size cannot be determined, None will be returned
      for either or both of rows and columns.

      This function relies on the UNIX `stty` command.
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
  return WinSize(rows, columns)

_ti_setup = False

def setupterm(*args):
  ''' Run curses.setupterm, needed to be able to use the status line.
      Uses a global flag to avoid doing this twice.
  '''
  global _ti_setup
  if _ti_setup:
    return True
  termstr = None
  fd = None
  if args:
    args = list(args)
    termstr = args.pop(0)
    if args:
      fd = args.pop(0)
      if args:
        raise ValueError("extra arguments after termstr and fd: %r" % (args,))
  if termstr is None:
    termstr = os.environ['TERM']
  if fd is None:
    fd = sys.stdout.fileno()
  import curses
  curses.setupterm(termstr, fd)
  _ti_setup = True
  return True

def statusline_bs(text, reverse=False, xpos=None, ypos=None):
  ''' Return a byte string to update the status line.
  '''
  from curses import tigetstr, tparm, tigetflag
  setupterm()
  if tigetflag('hs'):
    seq = (
        tigetstr('tsl'),
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
    seq = (
        tigetstr('sc'),   # save cursor position
        tparm(tigetstr("cup"), xpos, ypos),
        tigetstr('rev') if reverse else b'',
        text.encode(),
        tigetstr('el'),
        tigetstr('rc')
    )
  return b''.join(seq)

def statusline(text, fd=None, reverse=False, xpos=None, ypos=None):
  ''' Update the status line.
  '''
  if fd is None:
    fd = sys.stdout.fileno()
  os.write(fd, statusline_bs(text, reverse=reverse, xpos=xpos, ypos=ypos))

def status(msg, *args, **kwargs):
  ''' Write a message to the terminal's status line.

      Parameters:
      * `msg`: message string
      * `args`: if not empty, the message is %-formatted with `args`
      * `file`: optional keyword argument specifying the output file.
        Default: `sys.stderr`.

      Hack: if there is no status line use the xterm title bar sequence :-(
  '''
  if args:
    msg = msg % args
  f = kwargs.pop('file', None)
  if kwargs:
    raise ValueError("unexpected keyword arguments: %r" % (kwargs,))
  if f is None:
    f = sys.stderr
  try:
    has_ansi_status = f.has_ansi_status
  except AttributeError:
    try:
      import curses
    except ImportError:
      has_ansi_status = None
    else:
      curses.setupterm()
      has_status = curses.tigetflag('hs')
      if has_status == -1:
        warning(
            'status: curses.tigetflag(hs): not a Boolean capability, presuming false'
        )
        has_ansi_status = None
      elif has_status > 0:
        has_ansi_status = (
            curses.tigetstr('to_status_line'),
            curses.tigetstr('from_status_line')
        )
      else:
        warning('status: hs=%s, presuming false', has_status)
        has_ansi_status = None
    f.has_ansi_status = has_ansi_status
  if has_ansi_status:
    msg = has_ansi_status[0] + msg + has_ansi_status[1]
  else:
    msg = '\033]0;' + msg + '\007'
  f.write(msg)
  f.flush()
