#!/usr/bin/python
#

r'''
Assorted process and subprocess management functions.
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
    'install_requires': [],
}

# maximum number of bytes usable in the argv list for the exec*() functions
# 262144 below is from MacOS El Capitan "sysctl kern.argmax", then
# halved because even allowing for the size of the environment this
# can be too big. Unsure why.
MAX_ARGV = 262144 / 2

def stop(pid, signum=SIGTERM, wait=None, do_SIGKILL=False):
  ''' Stop the process specified by `pid`, optionally await its demise.

      Parameters:
      * `pid`: process id.
        If `pid` is a string, treat as a process id file and read the
        process id from it.
      * `signum`: the signal to send, default `signal.SIGTERM`.
      * `wait`: whether to wait for the process, default `None`.
        If `None`, return `True` (signal delivered).
        If `0`, wait indefinitely until the process exits as tested by
        `os.kill(pid, 0)`.
        If greater than 0, wait up to `wait` seconds for the process to die;
        if it exits, return `True`, otherwise `False`;
      * `do_SIGKILL`: if true (default `False`),
        send the process `signal.SIGKILL` as a final measure before return.
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

      Parameters:
      * `path`: the path to the pid file.
      * `pid`: the process id to write, defautl from `os.getpid`.
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

      Parameters:
      * `path`: the path to the process id file.
      * `pid`: the process id to store in the pid file,
        default from `os.etpid`.

      Writes the process id file at the start
      and removes the process id file at the end.
  '''
  write_pidfile(path, pid=pid)
  try:
    yield
  finally:
    remove_pidfile(path)

def run(argv, logger=None, pids=None, **kw):
  ''' Run a command. Optionally trace invocation.
      Return result of subprocess.call.

      Parameters:
      * `argv`: the command argument list
      * `pids`: if supplied and not None,
        call .add and .remove with the subprocess pid around the execution

      Other keyword arguments are passed to `subprocess.call`.
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
  ''' Pipe text from a command.
      Optionally trace invocation.
      Return the `Popen` object with `.stdout` decoded as text.

      Parameters:
      * `argv`: the command argument list
      * `binary`: if true (default false)
        return the raw stdout instead of a text wrapper
      * `trace`: if true (default `False`),
        if `trace` is `True`, recite invocation to stderr
        otherwise presume that `trace` is a stream
        to which to recite the invocation.
      * `keep_stdin`: if true (default `False`)
        do not attach the command's standard input to the null device.
        The default behaviour is to do so,
        preventing commands from accidentally
        consuming the main process' input stream.

      Other keyword arguments are passed to the `io.TextIOWrapper`
      which wraps the command's output.
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
      raise ValueError(
          "binary mode: extra keyword arguments not supported: %r" % (kw,)
      )
  else:
    P.stdout = io.TextIOWrapper(P.stdout, **kw)
  if not keep_stdin and sp_devnull is None:
    devnull.close()
  return P

def pipeto(argv, trace=False, **kw):
  ''' Pipe text to a command.
      Optionally trace invocation.
      Return the Popen object with .stdin encoded as text.

      Parameters:
      * `argv`: the command argument list
      * `trace`: if true (default `False`),
        if `trace` is `True`, recite invocation to stderr
        otherwise presume that `trace` is a stream
        to which to recite the invocation.

      Other keyword arguments are passed to the `io.TextIOWrapper`
      which wraps the command's input.
  '''
  if trace:
    tracefp = sys.stderr if trace is True else trace
    pargv = ['+', '|'] + argv
    print(*pargv, file=tracefp)
  P = subprocess.Popen(argv, stdin=subprocess.PIPE)
  P.stdin = io.TextIOWrapper(P.stdin, **kw)
  return P

def groupargv(pre_argv, argv, post_argv=(), max_argv=None, encode=False):
  ''' Distribute the array `argv` over multiple arrays
      to fit within `MAX_ARGV`.
      Return a list of argv lists.

      Parameters:
      * `pre_argv`: the sequence of leading arguments
      * `argv`: the sequence of arguments to distribute; this may not be empty
      * `post_argv`: optional, the sequence of trailing arguments
      * `max_argv`: optional, the maximum length of each distributed
        argument list, default: MAX_ARGV
      * `encode`: default False.
        If true, encode the argv sequences into bytes for accurate tallying.
        If `encode` is a Boolean,
        encode the elements with their .encode() method.
        If `encode` is a `str`, encode the elements with their `.encode()`
        method with `encode` as the encoding name;
        otherwise presume that `encode` is a callable
        for encoding each element.

      The returned argv arrays will contain the encoded element values.
  '''
  if not argv:
    raise ValueError("argv may not be empty")
  if max_argv is None:
    max_argv = MAX_ARGV
  if encode:
    if isinstance(encode, bool):
      pre_argv = [arg.encode() for arg in pre_argv]
      argv = [arg.encode() for arg in argv]
      post_argv = [arg.encode() for arg in post_argv]
    elif isinstance(encode, str):
      pre_argv = [arg.encode(encode) for arg in pre_argv]
      argv = [arg.encode(encode) for arg in argv]
      post_argv = [arg.encode(encode) for arg in post_argv]
    else:
      pre_argv = [encode(arg) for arg in pre_argv]
      argv = [encode(arg) for arg in argv]
      post_argv = [encode(arg) for arg in post_argv]
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
            "cannot fit argument into argv: available=%d, len(arg)=%d: %r" %
            (available, len(arg), arg)
        )
      argvs.append(pre_argv + per + post_argv)
      available = max_argv - pre_nbytes - post_nbytes
      per = []
    per.append(arg)
    available -= nbytes
  if per:
    argvs.append(pre_argv + per + post_argv)
  return argvs

if __name__ == '__main__':
  for test_max_argv in 64, 20, 16, 8:
    print(
        test_max_argv,
        repr(
            groupargv(
                ['cp', '-a'], ['a', 'bbbb', 'ddddddddddddd'], ['end'],
                max_argv=test_max_argv,
                encode=True
            )
        )
    )
