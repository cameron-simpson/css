#!/usr/bin/env python3

''' Utlity functions for working with tmux(1).
'''

from contextlib import contextmanager
from dataclasses import dataclass, field
from getopt import GetoptError
import os
from os.path import (
    isabs as isabspath,
    join as joinpath,
)
from shlex import join as shq
from stat import S_ISFIFO
from subprocess import CompletedProcess, PIPE, Popen
import sys
from tempfile import TemporaryDirectory
from threading import Lock
import time
from time import sleep
from typing import Callable, List, Optional

from icontract import require
from typeguard import typechecked

from cs.buffer import CornuCopyBuffer
from cs.cmdutils import BaseCommand
from cs.context import stackattrs
from cs.fs import HasFSPath
from cs.fsm import FSMTransitionEvent
from cs.logutils import info, warning
from cs.pfx import Pfx, pfx, pfx_call, pfx_method
from cs.psutils import run
from cs.queues import IterableQueue
from cs.resources import MultiOpenMixin
from cs.result import Result
from cs.threads import bg
from cs.upd import Upd

from cs.debug import trace, X, s, r

def quote(tmux_s: str):
  ''' Quote a string `tmux_s` for use in a tmux command.
  '''
  qs = tmux_s.replace('\\', '\\\\').replace("'", "\\'")
  return f"'{qs}'"

@dataclass
class TmuxControlItem:
  ''' A representation of an item from a tmux control flow
      eg a `b'%output'` line or `b'%begin'`...`b'%end'` sequence.
  '''

  unixtime: float  # timestamp
  arg0: bytes
  argv: List[bytes]
  output_data_chunks: List[bytes] = field(default_factory=list)
  unixtime_final: Optional[float] = None
  end_arg0: Optional[bytes] = None
  end_argv: Optional[List[bytes]] = None

  @property
  def ok(self):
    ''' A `begin`...`end` or `begin`...`error` is ok if it ended with `end`.
    '''
    return self.end_arg0 == b'end'

  @property
  def begin_unixtime(self):
    ''' The UNIX timestamp supplied with `begin`.
    '''
    return float(self.argv[0].decode('utf-8'))

  @property
  def begin_cmdnum(self):
    ''' The command number supplied with `begin`.
    '''
    return float(self.argv[1].decode('utf-8'))

  @property
  def end_unixtime(self):
    ''' The UNIX timestamp supplied with `end`.
    '''
    return float(self.end_argv[0].decode('utf-8'))

  @property
  def end_cmdnum(self):
    ''' The command number supplied with `end`.
    '''
    return float(self.end_argv[1].decode('utf-8'))

  @staticmethod
  def argv(bs) -> List[bytes]:
    ''' Decode a binary line commencing with `%`*word*
        into an argument list, omitting the `%`.
    '''
    if not bs.startswith(b'%'):
      raise ValueError(f'expected leading percent, got {bs!r}')
    return bs[1:].split()

  @classmethod
  @pfx_method
  def parse(cls, rf) -> "TmuxControlItem":
    ''' Read a single control item from the binary stream `rf`,
        return a `TmuxControlItem`.

        Raises `EOFError` if the binary `rf.readline()` fails.
    '''
    while True:
      try:
        bs = rf.readline()
      except ValueError as e:
        raise EOFError(f'error reading from rf={r(rf)}: {e}') from e
      now = time.time()
      if not bs:
        raise EOFError
      if not bs.startswith(b'%'):
        warning("no-%% line: %r", bs)
        continue
    arg0, *argv = cls.argv(bs)
    item = cls(unixtime=now, arg0=arg0, argv=argv)
    if arg0 == b'begin':
      while True:
        bs = rf.readline()
        if not bs:
          raise EOFError
        if bs.startswith((b'%end ', b'%error ')):
          break
        item.output_data_chunks.append(bs)
    end = time.time()
    end_arg0, *end_argv = cls.argv(bs)
    item.unixtime_final = end
    item.end_arg0 = end_arg0
    item.end_argv = end_argv
    return item

@dataclass
class TmuxCommandResponse:
  ''' A tmux control command response.
  '''

  item: TmuxControlItem
  notifications: List[TmuxControlItem] = field(default_factory=list)

  def __str__(self):
    return b''.join(self.output).decode('utf-8')

  def __iter__(self):
    for bs in self.output:
      yield bs.decode('utf-8')

  @property
  def output(self):
    ''' The output data.
    '''
    return self.item.output_data_chunks

  @classmethod
  def read_response(cls, rf, *, notify=None):
    ''' Read a tmux control response from `rf`.

        May raise `EOFError` from the `TmuxControlItem.parse()` call.
    '''
    notifications = []
    while True:
      try:
        item = TmuxControlItem.parse(rf)
      except EOFError:
        return None
      if item.arg0 == b'begin':
        break
      if notify:
        notify(item)
      notifications.append(item)
    return cls(item=item, notifications=notifications)

class TmuxControl(HasFSPath, MultiOpenMixin):
  ''' A class to control tmux(1) via its control socket.

      Trivial example:

          with TmuxControl() as tm:
              print(tm('list-sessions'))
              print(tm('list-panes'))

      Calling a `TmuxControl` with a tmux(1) command as above
      returns a `TmuxCommandResponse` instance.
      For trite use with `print()` its `__str__` method returns the output as a string.
  '''

  TMUX = 'tmux'

  def __init__(self, socketpath=None, notify=None):
    if socketpath is None:
      socketpath = self.get_socketpath()
    if notify is None:
      notify = self.default_notify
    self.fspath = socketpath
    self.notify = notify
    self._lock = Lock()

  @staticmethod
  def get_socketpath(tmpdir=None, subdir=None, name='default'):
    ''' Compute the path to a tmux(1) control socket.

        Parameters:
        * `tmpdir`: optional temp dir, default from `$TMUX_TMPDIR` or
          `/tmp` as per tmux(1)
        * `subdir`: optional subdirectory of `tmpdir` for the socket,
          default `tmux-`*uid*` as per tmux(1)
        * `name`: optional socket basename, default `'default'`
    '''
    if tmpdir is None:
      tmpdir = os.environ.get('TMUX_TMPDIR', '/tmp')
    if subdir is None:
      uid = os.geteuid()
      subdir = f'tmux-{uid}'
    return joinpath(tmpdir, subdir, name)

  @contextmanager
  def startup_shutdown(self):
    ''' Open/close the control socket.
    '''
    with Popen(
        [self.TMUX, '-S', self.fspath, '-C'],
        stdin=PIPE,
        stdout=PIPE,
    ) as P:
      try:
        pending = []  # queue of pending Results
        with stackattrs(self, rf=P.stdout, wf=P.stdin, pending=pending):
          workerT = bg(self._worker, args=(self.rf,))
          with stackattrs(self, workerT=workerT):
            yield
      finally:
        P.stdin.close()
        P.wait()

  def default_notify(self, bs: bytes):
    arg0, *args = TmuxCommandResponse.argv(bs)
    if arg0 == 'output':
      return
    info("%%%s %r", arg0, args)

  @pfx_method
  def _worker(self, rf):
    ''' Worker function to read the initial response
        and then all subsequent responses, using them to complete pending
        command `Result`s.
    '''
    pending = self.pending
    notify = self.notify
    lock = self._lock
    # read the initial empty response
    try:
      TmuxCommandResponse.read_response(rf, notify=notify)
    except EOFError as e:
      # no opening empty response
      warning("early EOF with %d outstanding requests: %s", len(pending), e)
      return
    while True:
      # collect the next response
      try:
        rsp = TmuxCommandResponse.read_response(rf, notify=notify)
      except EOFError as e:
        warning("EOF with %d outstanding requests: %s", len(pending), e)
        return
      if rsp is None:
        return
      try:
        with lock:
          R = pending.pop(0)
      except IndexError:
        warning("discarding unexpected TmuxCommandResponse: %r", rsp)
      else:
        # return response to caller
        R.result = rsp

  @require(lambda tmux_command: tmux_command and '\n' not in tmux_command)
  def submit(
      self,
      tmux_command: str,
      callback: Optional[Callable[Result, FSMTransitionEvent]] = None,
  ) -> Result:
    ''' Submit `tmux_command`, return a `Result`
        for collection of the `TmuxCommandResponse`.

        The optional `callback` parameter is a callable accepting
        a `Result` and an `FSMTransitionEvent` which will be called
        when the `Result` is done or cancelled.
    '''
    wf = self.wf
    R = Result(f'{tmux_command}')
    with self._lock:
      self.pending.append(R)
      R.dispatch()
      wf.write(tmux_command.encode('utf-8'))
      wf.write(b'\n')
      wf.flush()
    return R

  # TODO: worker thread to consume the control data and complete Results

  @pfx_method
  @typechecked
  def __call__(self, tmux_command: str) -> TmuxCommandResponse:
    ''' Submit `tmux_command`, return a `TmuxCommandResponse` when it completes.
    '''
    with self:
      R = self.submit(tmux_command)
      return R()

  def pane(self, argv, target_pane=None) -> "TmuxPane":
    ''' Dispatch the command `argv` in a new pane split from the current window.
        Return a `TmuxPane` for the new pane.
    '''
    if target_pane is None:
      target_pane = os.environ['TMUX_PANE']
    shcmd = shq(argv)
    with self:
      rsp = self(
          f'split-window -t {quote(target_pane)} -P -F "#{{pane_id}}" {quote(shcmd)}'
      )
      pane_id = str(rsp).strip()
    return TmuxPane(self, pane_id)

class TmuxPane:
  ''' State associated with an individual pane.
  '''

  def __init__(self, tmux: TmuxControl, pane_id: int):
    self.tmux = tmux
    self.pane_id = pane_id

  def __str__(self):
    return f'%{self.pane_id}'

  def select_layout(self, layout: str, even=False):
    self.tmux(f'select-layout -t {self} {"-E" if even else ""}')

  def pane(self, argv) -> "TmuxPane":
    return self.tmux.pane(argv, self.pane_id)

def tmux(tmux_command, *tmux_args) -> CompletedProcess:
  ''' Execute the tmux(1) command `tmux_command`.
  '''
  return run('tmux', tmux_command, *tmux_args, quiet=False)

class TmuxCommand(BaseCommand):

  @contextmanager
  def run_context(self, **kw):
    ''' Establish a `TmuxContol` instance as `self.options.tmux`.
    '''
    with super().run_context(**kw):
      with TmuxControl() as tmux:
        with stackattrs(self.options, tmux=tmux):
          yield

  def cmd_cli(self, argv):
    ''' Usage: {cmd}
          Interaction prompt for tmux(1) commands.
    '''
    if argv:
      raise GetoptError(f'extra arguments: {argv!r}')
    tmux = self.options.tmux
    while True:
      try:
        tmux_command = input('tmux> ').strip()
      except EOFError:
        break
      if not tmux_command:
        continue
      if tmux_command == 'exit':
        break
      rsp = pfx_call(tmux, tmux_command)
      print(rsp)
      print(repr(rsp))

  def cmd_pane(self, argv):
    ''' Usage: {cmd} [command [args...]]
          Dispatch command in a new pane.
          With no arguments, list the current panes.
    '''
    tmux = self.options.tmux
    if argv:
      pane = tmux.pane(argv)
      print(pane)
    else:
      rsp = tmux('list-panes')
      for line in rsp:
        sys.stdout.write(line)

  def cmd_output_pipe(sel, argv):
    ''' Usage: {cmd} pipe-path
          Stub command to copy its input to its output.
          This is a placeholder to assist the work_window() function
          by supplying an executable command to invoke.
    '''
    if not argv:
      raise GetoptError('missing pipe-path')
    pipe_path = argv.pop(0)
    if not S_ISFIFO(pfx_call(os.stat, pipe_path).st_mode):
      raise GetoptError(f'pipe-path {pipe_path!r}: not a named pipe')
    if argv:
      raise GetoptError(f'extra arguments: {argv!r}')
    wf = os.fdopen(sys.stdout.fileno(), 'wb')
    with pfx_call(open, pipe_path, 'rb') as pf:
      buf = CornuCopyBuffer.from_file(pf)
      while True:
        bs = buf.read1(1024)
        if not bs:
          break
        wf.write(bs)
        wf.flush()

def work_window(name: str, subpanes: List[str]):
  ''' Set up a window split into several panes.
  '''
  cp = tmux(
      'new-session',
      '-P',
      '-d',
      '-s',
      name,
      '',
      check=True,
      capture_output=True
  )
  new_win = cp.stdout.strip()
  X("new-session %r", new_win)
  tmux('set', '-t', new_win, '-w', 'remain-on-exit')
  for subpane in reversed(subpanes):
    tmux('split-window', '-t', new_win)

def run_pane(*argv) -> IterableQueue:
  Q = IterableQueue()
  shcmd = shq(argv)

if __name__ == '__main__':
  sys.exit(TmuxCommand(sys.argv).run())
