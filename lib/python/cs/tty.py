#!/usr/bin/python
#
# Facilities for terminals.
#       - Cameron Simpson <cs@cskk.id.au>
#

''' Functions related to terminals.
'''

from __future__ import print_function
from collections import namedtuple
from contextlib import contextmanager
import errno
import os
import re
from subprocess import Popen, PIPE
import sys
from termios import tcsetattr, tcgetattr, TCSANOW
from cs.gimmicks import warning

__version__ = '20201102-post'

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
  P.stdout.close()
  xit = P.wait()
  del P
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
  global _ti_setup  # pylint: disable=global-statement
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
  import curses  # pylint: disable=import-outside-toplevel
  curses.setupterm(termstr, fd)
  _ti_setup = True
  return True

def statusline_bs(text, reverse=False, xpos=None, ypos=None):
  ''' Return a byte string to update the status line.
  '''
  from curses import tigetstr, tparm, tigetflag  # pylint: disable=import-outside-toplevel
  setupterm()
  if tigetflag('hs'):
    seq = (
        tigetstr('tsl'),
        tigetstr('dsl'),
        tigetstr('rev') if reverse else b'',
        text.encode(),
        tigetstr('fsl'),
    )
  else:
    # save cursor position, position, reverse, restore position
    if xpos is None:
      xpos = 0
    if ypos is None:
      ypos = 0
    seq = (
        tigetstr('sc'),  # save cursor position
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
      import curses  # pylint: disable=import-outside-toplevel
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

_termios_modes_names = {
    name: index
    for index, name in
    enumerate(('iflag', 'oflag', 'cflag', 'lflag', 'ispeed', 'ospeed', 'cc'))
}

# pylint: disable=too-many-branches
def modify_termios(fd=0, set_modes=None, clear_modes=None, strict=False):
  ''' Apply mode changes to a tty.
      Return the previous tty modes as from `termios.tcgetattr`
      or `None` if the changes could not be applied.
      If `strict`, raise an exception instead of returning `None`.

      Parameters:
      * `fd`: optional tty file descriptor, default `0`.
      * `set_modes`: an optional  mapping of attribute name to new value
        for values to set
      * `clear_modes`: an optional  mapping of attribute name to new value
        for values to clear
      * `strict`: optional flag, default `False`;
        if true, raise exceptions from failed `tcgetattr` and `tcsetattr` calls
        otherwise issue a warning if the errno is not `ENOTTY` and proceed.
        This aims to provide ease of use in batch mode by default
        while providing a mode to fail overtly if required.

      The attribute names are from
      `iflag`, `oflag`, `cflag`, `lflag`, `ispeed`, `ospeed`, `cc`,
      corresponding to the list entries defined by the `termios.tcgetattr`
      call.

      For `set_modes`, the attributes `ispeed`, `ospeed` and `cc`
      are applied directly;
      the other attributes are binary ORed into the existing modes.

      For `clear_modes`, the attributes `ispeed`, `ospeed` and `cc`
      cannot be cleared;
      the other attributes are binary removed from the existing modes.

      For example, to turn off the terminal echo during some operation:

          old_modes = apply_termios(clear_modes={'lflag': termios.ECHO}):
              ... do something with tty echo disabled ...
          if old_modes:
              termios.tcsetattr(fd, termios.TCSANOW, old_modes)
  '''
  if set_modes:
    if not all(map(lambda k: k in _termios_modes_names, set_modes.keys())):
      raise ValueError(
          "set_modes: invalid mode keys: known=%r, supplied=%r" %
          (sorted(_termios_modes_names.keys()), set_modes)
      )
  if clear_modes:
    if not all(map(lambda k: k in _termios_modes_names, clear_modes.keys())):
      raise ValueError(
          "clear_modes: invalid mode keys: known=%r, supplied=%r" %
          (sorted(_termios_modes_names.keys()), clear_modes)
      )
    for k in 'ispeed', 'ospeed', 'cc':
      if k in clear_modes:
        raise ValueError("clear_modes: cannot clear %r" % (k,))
  try:
    original_modes = tcgetattr(fd)
  except OSError as e:
    if strict:
      raise
    if e.errno != errno.ENOTTY:
      warning("tcgetattr(%d): %s", fd, e)
    original_modes = None
  restore_modes = None
  if original_modes:
    new_modes = list(original_modes)
    if set_modes:
      for k, v in set_modes.items():
        i = _termios_modes_names[k]
        if k in ('ispeed', 'ospeed', 'cc'):
          new_modes[i] = v
        else:
          new_modes[i] |= v
    if clear_modes:
      for k, v in clear_modes.items():
        i = _termios_modes_names[k]
        new_modes[i] &= ~v
    if new_modes == original_modes:
      restore_modes = None
    else:
      try:
        tcsetattr(fd, TCSANOW, new_modes)
      except OSError as e:
        if strict:
          raise
        warning("tcsetattr(%d,TCSANOW,%r): %e", fd, new_modes, e)
      else:
        restore_modes = original_modes
  return restore_modes

@contextmanager
def stack_termios(fd=0, set_modes=None, clear_modes=None, strict=False):
  ''' Context manager to apply and restore changes to a tty.
      Yield the previous tty modes as from `termios.tcgetattr`
      or `None` if the changes could not be applied.
      If `strict`, raise an exception instead of yielding `None`.

      Parameters:
      * `fd`: optional tty file descriptor, default `0`.
      * `set_modes`: an optional  mapping of attribute name to new value
        for values to set
      * `clear_modes`: an optional  mapping of attribute name to new value
        for values to clear
      * `strict`: optional flag, default `False`;
        if true, raise exceptions from failed `tcgetattr` and `tcsetattr` calls
        otherwise issue a warning if the errno is not `ENOTTY` and proceed.
        This aims to provide ease of use in batch mode by default
        while providing a mode to fail overtly if required.

      The attribute names are from
      `iflag`, `oflag`, `cflag`, `lflag`, `ispeed`, `ospeed`, `cc`,
      corresponding to the list entries defined by the `termios.tcgetattr`
      call.

      For `set_modes`, the attributes `ispeed`, `ospeed` and `cc`
      are applied directly;
      the other attributes are binary ORed into the existing modes.

      For `clear_modes`, the attributes `ispeed`, `ospeed` and `cc`
      cannot be cleared;
      the other attributes are binary removed from the existing modes.

      For example, to turn off the terminal echo during some operation:

          with stack_termios(clear_modes={'lflag': termios.ECHO}):
              ... do something with tty echo disabled ...
  '''
  try:
    restore_modes = modify_termios(
        fd, set_modes=set_modes, clear_modes=clear_modes, strict=strict
    )
    yield restore_modes
  finally:
    if restore_modes:
      try:
        tcsetattr(fd, TCSANOW, restore_modes)
      except OSError as e:
        if strict:
          raise
        warning("tcsetattr(%d,TCSANOW,%r): %e", fd, restore_modes, e)
