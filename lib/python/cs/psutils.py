#!/usr/bin/python
#

r'''
Assorted process and subprocess management functions.
'''

from builtins import print as builtin_print
from contextlib import contextmanager
import errno
import io
import logging
import os
import shlex
from signal import SIGTERM, SIGKILL, signal
from subprocess import PIPE, Popen, run as subprocess_run
import sys
import time

from cs.gimmicks import DEVNULL
from cs.logutils import trace, warning
from cs.pfx import pfx_call
from cs.upd import Upd, print  # pylint: disable=redefined-builtin

__version__ = '20220606'

DISTINFO = {
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.gimmicks>=devnull',
        'cs.logutils',
        'cs.pfx',
        'cs.upd',
    ],
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
    return stop(int(open(pid, encoding='ascii').read().strip()))
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

@contextmanager
def signal_handler(sig, handler, call_previous=False):
  ''' Context manager to push a new signal handler,
      yielding the old handler,
      restoring the old handler on exit.
      If `call_previous` is true (default `False`)
      also call the old handler after the new handler on receipt of the signal.

      Parameters:
      * `sig`: the `int` signal number to catch
      * `handler`: the handler function to call with `(sig,frame)`
      * `call_previous`: optional flag (default `False`);
        if true, also call the old handler (if any) after `handler`
  '''
  if call_previous:
    # replace handler() with a wrapper to call both it and the old handler
    handler0 = handler

    def handler(sig, frame):  # pylint:disable=function-redefined
      ''' Call the handler and then the previous handler if requested.
      '''
      handler0(sig, frame)
      if callable(old_handler):
        old_handler(sig, frame)

  old_handler = signal(sig, handler)
  try:
    yield old_handler
  finally:
    # restiore the previous handler
    signal(sig, old_handler)

@contextmanager
def signal_handlers(sig_hnds, call_previous=False, _stacked=None):
  ''' Context manager to stack multiple signal handlers,
      yielding a mapping of `sig`=>`old_handler`.

      Parameters:
      * `sig_hnds`: a mapping of `sig`=>`new_handler`
        or an iterable of `(sig,new_handler)` pairs
      * `call_previous`: optional flag (default `False`), passed
        to `signal_handler()`
  '''
  if _stacked is None:
    _stacked = {}
  try:
    items = sig_hnds.items
  except AttributeError:
    # (sig,hnd),... from iterable
    it = iter(sig_hnds)
  else:
    # (sig,hnd),... from mapping
    it = items()
  try:
    sig, handler = next(it)
  except StopIteration:
    pass
  else:
    with signal_handler(sig, handler,
                        call_previous=call_previous) as old_handler:
      _stacked[sig] = old_handler
      with signal_handlers(sig_hnds, call_previous=call_previous,
                           _stacked=_stacked) as stacked:
        yield stacked
    return
  yield _stacked

def write_pidfile(path, pid=None):
  ''' Write a process id to a pid file.

      Parameters:
      * `path`: the path to the pid file.
      * `pid`: the process id to write, defautl from `os.getpid`.
  '''
  if pid is None:
    pid = os.getpid()
  with open(path, 'w', encoding='ascii') as pidfp:
    print(pid, file=pidfp)

def remove_pidfile(path):
  ''' Truncate and remove a pidfile, permissions permitting.
  '''
  try:
    with open(path, "wb"):  # pylint: disable=unspecified-encoding
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

def run(argv, doit=True, logger=None, quiet=False, **subp_options):
  ''' Run a command via `subprocess.run`.
      Return the `CompletedProcess` result or `None` if `doit` is false.

      Parameters:
      * `argv`: the command line to run
      * `doit`: optional flag, default `True`;
        if false do not run the command and return `None`
      * `logger`: optional logger, default `None`;
        if `True`, use `logging.getLogger()`;
        if not `None` or `False` trace using `print_argv`
      * `quiet`: default `False`; if true, do not print the command or its output
      * `subp_options`: optional mapping of keyword arguments
        to pass to `subprocess.run`
  '''
  if logger is True:
    logger = logging.getLogger()
  if not doit:
    if not quiet:
      if logger:
        trace("skip: %s", shlex.join(argv))
      else:
        with Upd().above():
          print_argv(*argv, fold=True)
    return None
  with Upd().above():
    if not quiet:
      if logger:
        trace("+ %s", shlex.join(argv))
      else:
        print_argv(*argv)
    cp = pfx_call(subprocess_run, argv, **subp_options)
    if cp.stdout and not quiet:
      builtin_print(" ", cp.stdout.rstrip().replace("\n", "\n  "))
    if cp.stderr:
      builtin_print(" stderr:")
      builtin_print(" ", cp.stderr.rstrip().replace("\n", "\n  "))
  if cp.returncode != 0:
    warning(
        "run fails, exit code %s from %s",
        cp.returncode,
        shlex.join(cp.args),
    )
  return cp

def pipefrom(argv, quiet=False, binary=False, keep_stdin=False, **kw):
  ''' Pipe text from a command.
      Optionally trace invocation.
      Return the `Popen` object with `.stdout` decoded as text.

      Parameters:
      * `argv`: the command argument list
      * `binary`: if true (default false)
        return the raw stdout instead of a text wrapper
      * `quiet`: optional flag, default `False`;

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
  if not quiet:
    print_argv(*argv, indent="+ ", end=" |\n", file=sys.stderr)
  popen_kw = {}
  if not keep_stdin:
    popen_kw['stdin'] = DEVNULL
  P = Popen(argv, stdout=PIPE, **popen_kw)  # pylint: disable=consider-using-with
  if binary:
    if kw:
      raise ValueError(
          "binary mode: extra keyword arguments not supported: %r" % (kw,)
      )
  else:
    P.stdout = io.TextIOWrapper(P.stdout, **kw)
  return P

def pipeto(argv, quiet=False, **kw):
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
  if not quiet:
    print_argv(*argv, indent="| ", file=sys.stderr)
  P = Popen(argv, stdin=PIPE)  # pylint: disable=consider-using-with
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

def print_argv(
    *argv, indent="", subindent="  ", end="\n", file=None, fold=False
):
  ''' Print an indented possibly folded command line.
  '''
  if file is None:
    file = sys.stdout
  was_opt = False
  for i, arg in enumerate(argv):
    if i == 0:
      file.write(indent)
      was_opt = False
    elif len(arg) >= 2 and arg.startswith('-'):
      if fold:
        # options get a new line
        file.write(" \\\n" + indent + subindent)
      else:
        file.write(" ")
      was_opt = True
    else:
      if was_opt:
        file.write(" ")
      elif fold:
        # nonoptions get a new line
        file.write(" \\\n" + indent + subindent)
      else:
        file.write(" ")
      was_opt = False
    file.write(shlex.quote(arg))
  file.write(end)

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
