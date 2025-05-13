#!/usr/bin/python
#

r'''
Assorted process and subprocess management functions.

Not to be confused with the excellent
[psutil package](https://pypi.org/project/psutil/).
'''

import builtins
from contextlib import contextmanager
import errno
import io
from itertools import chain
import logging
import os
import shlex
from signal import SIGTERM, SIGKILL, signal
from subprocess import DEVNULL as subprocess_DEVNULL, PIPE, Popen, run as subprocess_run
import sys
import time

from cs.deco import fmtdoc, uses_cmd_options
from cs.gimmicks import trace, warning, DEVNULL
from cs.pfx import pfx_call

__version__ = '20250513'

DISTINFO = {
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.deco',
        'cs.gimmicks>=devnull',
        'cs.pfx',
    ],
}

# maximum number of bytes usable in the argv list for the exec*() functions
# 262144 below is from MacOS El Capitan "sysctl kern.argmax", then
# halved because even allowing for the size of the environment this
# can be too big. Unsure why.
MAX_ARGV = 262144 // 2

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
    it = iter(items())
  try:
    sig, handler = next(it)
  except StopIteration:
    pass
  else:
    with signal_handler(sig, handler,
                        call_previous=call_previous) as old_handler:
      _stacked[sig] = old_handler
      with signal_handlers(it, call_previous=call_previous,
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

@uses_cmd_options(doit=True, quiet=False)
def run(
    argv,
    *,
    check=True,
    doit: bool,
    input=None,
    logger=None,
    print=None,
    fold=None,
    quiet: bool,
    remote=None,
    ssh_exe=None,
    stdin=None,
    **subp_options,
):
  ''' Run a command via `subprocess.run`.
      Return the `CompletedProcess` result or `None` if `doit` is false.

      Positional parameter:
      * `argv`: the command line to run

      Note that `argv` is passed through `prep_argv(argv,remote=remote,ssh_exe=ssh_exe)`
      before use, allowing direct invocation with conditional parts.
      See the `prep_argv` function for details.

      Keyword parameters:
      * `check`: passed to `subprocess.run`, default `True`;
        NB: _unlike_ the `subprocess.run` default, which is `False`
      * `doit`: optional flag, default `True`;
        if false do not run the command and return `None`
      * `fold`: optional flag, passed to `print_argv`
      * `input`: default `None`: alternative to `stdin`;
        passed to `subprocess.run`
      * `logger`: optional logger, default `None`;
        if `True`, use `logging.getLogger()`;
        if not `None` or `False` trace using `print_argv`
      * `quiet`: default `False`; if false, print the command and its output
      * `remote`: optional remote target on which to run `argv`
      * `ssh_exe`: optional command string for the remote shell
      * `stdin`: standard input for the subprocess, default `subprocess.DEVNULL`;
        passed to `subprocess.run`

      Other keyword parameters are passed to `subprocess.run`.
  '''
  argv = prep_argv(*argv, remote=remote, ssh_exe=ssh_exe)
  if logger is True:
    logger = logging.getLogger()
  if not doit:
    if not quiet:
      if logger:
        trace("skip: %s", shlex.join(argv))
      else:
        if fold is None:
          fold = True
        print_argv(*argv, fold=fold, print=print)
    return None
  if not quiet:
    if logger:
      trace("+ %s", shlex.join(argv))
    else:
      if fold is None:
        fold = False
      print_argv(
          *argv,
          indent0="+ ",
          indent="  ",
          file=sys.stderr,
          fold=fold,
          print=print
      )
  if input is None:
    if stdin is None:
      stdin = subprocess_DEVNULL
  elif stdin is not None:
    raise ValueError("you may not specify both input and stdin")
  cp = pfx_call(
      subprocess_run,
      argv,
      check=check,
      input=input,
      stdin=stdin,
      **subp_options,
  )
  if cp.stderr:
    # TODO: is this a good thing? I have my doubts
    print(" stderr:")
    print(" ", cp.stderr.rstrip().replace("\n", "\n  "))
  if cp.returncode != 0:
    warning(
        "run fails, exit code %s from %s",
        cp.returncode,
        shlex.join(cp.args),
    )
  return cp

@uses_cmd_options(quiet=False, ssh_exe='ssh')
def pipefrom(
    argv,
    *,
    quiet: bool,
    remote=None,
    ssh_exe,
    text=True,
    stdin=DEVNULL,
    **popen_kw
):
  ''' Pipe text (usually) from a command using `subprocess.Popen`.
      Return the `Popen` object with `.stdout` as a pipe.

      Parameters:
      * `argv`: the command argument list
      * `quiet`: optional flag, default `False`;
        if true, print the command to `stderr`
      * `text`: optional flag, default `True`; passed to `Popen`.
      * `stdin`: optional value for `Popen`'s `stdin`, default `DEVNULL`
      Other keyword arguments are passed to `Popen`.

      Note that `argv` is passed through `prep_argv` before use,
      allowing direct invocation with conditional parts.
      See the `prep_argv` function for details.
  '''
  argv = prep_argv(*argv, remote=remote, ssh_exe=ssh_exe)
  if not quiet:
    print_argv(*argv, indent="+ ", end=" |\n", file=sys.stderr)
  return Popen(argv, stdout=PIPE, text=text, stdin=stdin, **popen_kw)

# TODO: text= parameter?
@uses_cmd_options(quiet=False, ssh_exe='ssh')
def pipeto(argv, *, quiet: bool, remote=None, ssh_exe, **kw):
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

      Note that `argv` is passed through `prep_argv` before use,
      allowing direct invocation with conditional parts.
      See the `prep_argv` function for details.
  '''
  argv = prep_argv(*argv)
  if not quiet:
    print_argv(*argv, indent="| ", file=sys.stderr)
  P = Popen(argv, stdin=PIPE)  # pylint: disable=consider-using-with
  P.stdin = io.TextIOWrapper(P.stdin, **kw)
  return P

@fmtdoc
def groupargv(pre_argv, argv, post_argv=(), max_argv=None, encode=False):
  ''' Distribute the array `argv` over multiple arrays
      to fit within `MAX_ARGV`.
      Return a list of argv lists.

      Parameters:
      * `pre_argv`: the sequence of leading arguments
      * `argv`: the sequence of arguments to distribute; this may not be empty
      * `post_argv`: optional, the sequence of trailing arguments
      * `max_argv`: optional, the maximum length of each distributed
        argument list, default from `MAX_ARGV`: `{MAX_ARGV}`
      * `encode`: default `False`.
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
  pre_nbytes = sum(len(arg) + 1 for arg in pre_argv)
  post_nbytes = sum(len(arg) + 1 for arg in post_argv)
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

@uses_cmd_options(ssh_exe='ssh')
def prep_argv(*argv, ssh_exe, remote=None):
  ''' A trite list comprehension to reduce an argument list `*argv`
      to the entries which are not `None` or `False`
      and to flatten other entries which are not strings.

      This exists ease the construction of argument lists
      with methods like this:

          >>> command_exe = 'hashindex'
          >>> hashname = 'sha1'
          >>> quiet = False
          >>> verbose = True
          >>> prep_argv(
          ...     command_exe,
          ...     quiet and '-q',
          ...     verbose and '-v',
          ...     hashname and ('-h', hashname),
          ... )
          ['hashindex', '-v', '-h', 'sha1']

      where `verbose` is a `bool` governing the `-v` option
      and `hashname` is either `str` to be passed with `-h hashname`
      or `None` to omit the option.

      If `remote` is not `None` it is taken to be a remote host on
      which to run `argv`. This is done via the `ssh_exe` argument,
      which defaults to the string `'ssh'`. The value of `ssh_exe`
      is a command string parsed with `shlex.split`. A new `argv`
      is computed as:

          [
              *shlex.split(ssh_exe),
              remote,
              '--',
              shlex.join(argv),
          ]
  '''
  argv = list(
      chain(
          *[
              ((arg,) if isinstance(arg, str) else arg)
              for arg in argv
              if arg is not None and arg is not False
          ]
      )
  )
  if remote is not None:
    argv = [
        *shlex.split(ssh_exe),
        remote,
        '--',
        shlex.join(argv),
    ]
  return argv

def print_argv(
    *argv,
    indent0=None,
    indent="",
    subindent="  ",
    end="\n",
    file=None,
    fold=False,
    print=None,
    as_str=str,
):
  r'''Print an indented possibly folded command line.

      Parameters:
      * `argv`: the arguments to print
      * `indent0`: optional indent for the first argument
      * `indent`: optional per line indent if `fold` is true
      * `subindent`: optional additional indent for the second and
        following lines, default `"  "`
      * `end`: optional line ending, default `"\n"`
      * `file`: optional output file, default `sys.stdout`
      * `fold`: optional fold mode, default `False`;
        if true then arguments are laid out over multiple lines
      * `print`: optional `print` callable, default `builtins.print`
      * `as_str`: optional callable to convert arguments to strings, default `str`;
        this can be `None` to avoid conversion
  '''
  if indent0 is None:
    indent0 = indent
  if file is None:
    file = sys.stdout
  if print is None:
    print = builtins.print
  pr_argv = []
  was_opt = False
  for i, arg in enumerate(argv):
    if as_str is not None:
      arg = as_str(arg)
    if i == 0:
      pr_argv.append(indent0)
      was_opt = False
    elif len(arg) >= 2 and arg.startswith('-'):
      if fold:
        # options get a new line
        pr_argv.append(" \\\n" + indent + subindent)
      else:
        pr_argv.append(" ")
      was_opt = True
    else:
      if was_opt:
        pr_argv.append(" ")
      elif fold:
        # nonoptions get a new line
        pr_argv.append(" \\\n" + indent + subindent)
      else:
        pr_argv.append(" ")
      was_opt = False
    pr_argv.append(shlex.quote(arg))
  print(*pr_argv, sep='', end=end, file=file)

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
