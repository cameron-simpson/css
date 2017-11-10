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
* groupargv: break up argv lists to fit within the maximum argument limit
'''

from __future__ import print_function
from contextlib import contextmanager
import errno
import io
import logging
import os
from signal import SIGTERM, SIGKILL
import subprocess
import sys
import time

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

# maximum number of bytes usable in the argv list for the exec*() functions
MAX_ARGV = 262144

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

def run(argv, logger=None, pids=None, **kw):
  ''' Run a command. Optionally trace invocation. Return result of subprocess.call.
      `argv`: the command argument list
      `pids`: if supplied and not None, call .add and .remove with
              the subprocess pid around the execution
      Other keyword arguments are passed to subprocess.call.
  '''
  if logger is True:
    logger = logging.getLogger()
  try:
    if logger:
      pargv = ['+'] + argv
      logger.info("RUN COMMAND: %r", pargv)
    P = subprocess.Popen(argv, **kw)
    if pids is not None:
      pids.add(P.pid)
    returncode = P.wait()
    if pids is not None:
      pids.remove(P.pid)
    if returncode != 0:
      if logger:
        logger.error("NONZERO EXIT STATUS: %s: %r", returncode, pargv)
    return returncode
  except BaseException as e:
    if logger:
      logger.exception("RUNNING COMMAND: %s", e)
    raise

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

def groupargv(pre_argv, argv, post_argv=(), max_argv=None, encode=False):
  ''' Distribute the array `argv` over multiple arrays to fit within `MAX_ARGV`. Return a list of argv lists.
      `pre_argv`: the sequence of leading arguments
      `argv`: the sequence of arguments to distribute
      `post_argv`: optional, the sequence of trailing arguments
      `max_argv`: optional, the maximum length of each distributed
        argument list, default: MAX_ARGV
      `encode`: default False; if truthy, encode the argv sequences
        into bytes for accurate tallying. If `encode` is a Boolean,
        encode the elements with their .encode() method; if `encode`
        is a str, encode the elements with their .encode() method
        with `encode` as the encoding name; otherwise presume that
        `encode` is a callable for encoding the element.
        The returned argv arrays will contain the encoded element values.
  '''
  if max_argv is None:
    max_argv = MAX_ARGV
  if encode:
    if isinstance(encode, bool):
      pre_argv = [ arg.encode() for arg in pre_argv ]
      argv = [ arg.encode() for arg in argv ]
      post_argv = [ arg.encode() for arg in post_argv ]
    elif isinstance(encode, str):
      pre_argv = [ arg.encode(encode) for arg in pre_argv ]
      argv = [ arg.encode(encode) for arg in argv ]
      post_argv = [ arg.encode(encode) for arg in post_argv ]
    else:
      pre_argv = [ encode(arg) for arg in pre_argv ]
      argv = [ encode(arg) for arg in argv ]
      post_argv = [ encode(arg) for arg in post_argv ]
  else:
    pre_argv = list(pre_argv)
    post_argv = list(post_argv)
  pre_nbytes = sum([len(arg) + 1 for arg in pre_argv])
  post_nbytes = sum([len(arg) + 1 for arg in post_argv])
  argvs = []
  available = max_argv - pre_nbytes - post_nbytes
  per = []
  for arg in argv:
    nbytes = len(arg) + 1
    if available - nbytes < 0:
      if not per:
        raise ValueError(
            "cannot fit argument into argv: available=%d, len(arg)=%d: %r"
            % (available, len(arg), arg))
      argvs.append(pre_argv + per + post_argv)
      available = max_argv - pre_nbytes - post_nbytes
      per = [arg]
    else:
      per.append(arg)
      available -= nbytes
  if per:
    argvs.append(pre_argv + per + post_argv)
  return argvs

if __name__ == '__main__':
  for max_argv in 64, 20, 16, 8:
    print(max_argv, repr(groupargv(['cp', '-a'], ['a', 'bbbb', 'ddddddddddddd'], ['end'], max_argv=max_argv, encode=True)))
