#!/usr/bin/python
#
# Convenience functions to work with processes.
#       - Cameron Simpson <cs@cskk.id.au> 02sep2011
#

r'''
Assorted process management functions.

* stop: stop a process with a signal (SIGTERM), await its demise.
* write_pidfile: save a process pid to a file
* remove_pidfile: truncate and remove a pid file
* PidFileManager: context manager for a pid file
* run: run a command and optionally trace its dispatch.
* pipefrom: dispatch a command with standard output connected to a pipe
* pipeto: dispatch a command with standard input connected to a pipe
'''

from __future__ import print_function
from contextlib import contextmanager
import errno
import io
import os
from signal import SIGTERM, SIGKILL
import subprocess
import sys
import time
from cs.pfx import Pfx

DISTINFO = {
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
    ],
}

def stop(pid, signum=SIGTERM, wait=None, do_SIGKILL=False):
  ''' Stop the process specified by `pid`.
      If `pid` is a string, treat as a process id file and read the
      process id from it.
      Send the process the signal `signum`, default signal.SIGTERM.
      If `wait` is unspecified or None, return True (signal delivered).
      If `wait` is 0, wait indefinitely until the process exits as
      tested by os.kill(pid, 0).
      If `wait` is greater than 0, wait up to `wait` seconds for
      the process to die; if it exits, return True, otherwise False;
      if `do_SIGKILL` is true then send the process signal.SIGKILL
      as a final measure before return.
  '''
  if isinstance(pid, str):
    with Pfx(pid):
      return stop(int(open(pid).read().strip()))
  os.kill(pid, signum)
  if wait is None:
    return True
  assert wait >= 0, "wait (%s) should be >= 0" % (wait,)
  now = time.time()
  then = now + wait
  while True:
    time.sleep(0.1)
    if wait == 0 or time.time() < then:
      try:
        os.kill(pid, 0)
      except OSError as e:
        if e.errno != errno.ESRCH:
          raise
        # process no longer present
        return True
    else:
      if do_SIGKILL:
        try:
          os.kill(pid, SIGKILL)
        except OSError as e:
          if e.errno != errno.ESRCH:
            raise
      return False

def write_pidfile(path, pid=None):
  ''' Write a process id to a pid file.
      `path`: the path to the pid file.
      `pid`: the process id to write, defautl from os.getpid.
  '''
  if pid is None:
    pid = os.getpid()
  with open(path, "w") as pidfp:
    print(pid, file=pidfp)

def remove_pidfile(path):
  ''' Truncate and remove a pidfile, permissions permitting.
  '''
  try:
    with open(path, "w"):
      pass
    os.remove(path)
  except OSError as e:
    if e.errno != errno.EPERM:
      raise

@contextmanager
def PidFileManager(path, pid=None):
  ''' Context manager for a pid file.
  '''
  write_pidfile(path, pid)
  yield
  remove_pidfile(path)

def run(argv, trace=False, **kw):
  ''' Run a command. Optionally trace invocation. Return result of subprocess.call.
      `argv`: the command argument list
      `trace`: Default False. If True, recite invocation to stderr.
        Otherwise presume a stream to which to recite the invocation.
  '''
  if trace:
    tracefp = sys.stderr if trace is True else trace
    pargv = ['+'] + argv
    print(*pargv, file=tracefp)
  return subprocess.call(argv, **kw)

def pipefrom(argv, trace=False, binary=False, keep_stdin=False, **kw):
  ''' Pipe text from a command. Optionally trace invocation. Return the Popen object with .stdout decoded as text.
      `argv`: the command argument list
      `binary`: if true (default false) return the binary stdout instead of a text wrapper
      `trace`: Default False. If True, recite invocation to stderr.
        Otherwise presume a stream to which to recite the invocation.
      The command's stdin is attached to the null device.
      Other keyword arguments are passed to io.TextIOWrapper.
  '''
  if trace:
    tracefp = sys.stderr if trace is True else trace
    pargv = ['+'] + list(argv) + ['|']
    print(*pargv, file=tracefp)
  popen_kw = {}
  if not keep_stdin:
    sp_devnull = getattr(subprocess, 'DEVNULL', None)
    if sp_devnull is None:
      devnull = open(os.devnull, 'wb')
    else:
      devnull = sp_devnull
    popen_kw['stdin'] = devnull
  P = subprocess.Popen(argv, stdout=subprocess.PIPE, **popen_kw)
  if binary:
    if kw:
      raise ValueError("binary mode: extra keyword arguments not supported: %r", kw)
  else:
    P.stdout = io.TextIOWrapper(P.stdout, **kw)
  if not keep_stdin and sp_devnull is None:
    devnull.close()
  return P

def pipeto(argv, trace=False, **kw):
  ''' Pipe text to a command. Optionally trace invocation. Return the Popen object with .stdin encoded as text.
      `argv`: the command argument list
      `trace`: Default False. If True, recite invocation to stderr.
        Otherwise presume a stream to which to recite the invocation.
      Other keyword arguments are passed to io.TextIOWrapper.
  '''
  if trace:
    tracefp = sys.stderr if trace is True else trace
    pargv = ['+', '|'] + argv
    print(*pargv, file=tracefp)
  P = subprocess.Popen(argv, stdin=subprocess.PIPE)
  P.stdin = io.TextIOWrapper(P.stdin, **kw)
  return P
