#!/usr/bin/env python3

''' Utlity functions for working with tmux(1).
'''

from contextlib import closing, contextmanager
from dataclasses import dataclass, field
import os
from os import O_RDWR
from os.path import join as joinpath
from shlex import join as shq
from socket import socket, AF_UNIX, SHUT_RD, SHUT_WR, SOCK_STREAM
from subprocess import CompletedProcess, PIPE, Popen
from threading import Lock
from typing import List

from icontract import require
from typeguard import typechecked

from cs.context import stackattrs
from cs.fs import HasFSPath
from cs.logutils import info, warning
from cs.pfx import Pfx, pfx, pfx_call, pfx_method
from cs.psutils import run
from cs.queues import IterableQueue
from cs.resources import MultiOpenMixin

from cs.debug import trace, X, s, r

@dataclass
class TmuxCommandResponse:
  ''' A tmux control command response.
  '''

  number: int
  ok: bool
  begin_unixtime: float
  end_unixtime: float
  output: List[bytes]
  notifications: list = field(default_factory=list)

  def __str__(self):
    return b''.join(self.output).decode('utf-8')

  @staticmethod
  def argv(bs):
    ''' Decode a binary line commencing with `%`*word*
        into an argument list, omitting the `%`.
    '''
    if not bs.startswith(b'%'):
      raise ValueError(f'expected leading percent, got {bs!r}')
    return bs[1:].decode('utf-8').split()

  @classmethod
  def read_response(cls, rf, *, notify=None):
    ''' Read a tmux control response from `rf`.
    '''
    notifications = []
    while True:
      bs = rf.readline()
      if not bs:
        return None
      if not bs.startswith(b'%'):
        warning("no-%% line: %r", bs)
        continue
      arg0, *args = cls.argv(bs)
      if arg0 == 'begin':
        break
      info("notification: %r", bs)
      if notify:
        notify(bs)
      notifications.append(bs)
    unixtime_s, cmdnum_s, _ = args
    begin_unixtime = float(unixtime_s)
    begin_cmdnum = int(cmdnum_s)
    output = []
    while True:
      bs = rf.readline()
      if not bs:
        raise EOFError()
      if bs.startswith((b'%end ', b'%error ')):
        break
      output.append(bs)
    arg, *args = cls.argv(bs)
    ok = arg == 'end'
    unixtime_s, cmdnum_s, _ = args
    end_unixtime = float(unixtime_s)
    end_cmdnum = int(cmdnum_s)
    assert begin_cmdnum == end_cmdnum
    return cls(
        number=begin_cmdnum,
        ok=ok,
        begin_unixtime=begin_unixtime,
        end_unixtime=end_unixtime,
        output=output,
        notifications=notifications,
    )

  @cached_property
  def lines(self):
    ''' A tuple of `str` from the response output, including the trailing newline.
    '''
    return tuple(
        line_bs.decode('utf-8', errors='replace') for line_bs in self.output
    )

  @staticmethod
  def parse_session_line(line):
    ''' Parse a tmux(1) `list-sessions` response line
        into a `(id,annotations,parsed)` 3-tuple where:
        - `id` is the session id, and `int` if unnamed, a `str` if named
        - `annotations` a list of text parts of the "(text)" suffixes
        - `parsed` is a dict containing parsed values

        The `parsed` dict always contains:
        - `nwindows`, the number of windows in the session
        - `created`, the UNIX timestamp of when the session was created
        - `attached`, whether the session is currently attached
    '''
    id_s, etc = line.rstrip().split(': ', 1)
    try:
      session_id = int(id_s)
    except ValueError:
      session_id = id_s
    with Pfx("session %s", session_id):
      annotations = []
      parsed = dict(
          attached=False,
          created=None,
          nwindows=None,
          session_id=session_id,
      )
      if m := re.match(r'^(\d+) windows', etc):
        parsed['nwindows'] = int(m.group(1))
        etc = etc[m.end():]
      else:
        nwindows = None
      while etc:
        if m := re.match(r'^\s*\(([^()]+)\)', etc):
          annotation = m.group(1)
          annotations.append(annotation)
          etc = etc[m.end():]
          created_date = cutprefix(annotation, 'created ')
          if annotation == 'attached':
            parsed['attached'] = True
          elif created_date != annotation:
            try:
              dt = datetime.strptime(created_date, '%a %b %d %H:%M:%S %Y')
            except ValueError as e:
              warning("cannot parse created date %r: %s", created_date, e)
            else:
              parsed['created'] = dt.timestamp()
              parsed['created_dt'] = dt
          else:
            warning("unhandled annotation %r", annotation)
        else:
          warning("unparsed session information: %r", etc)
          break
    return session_id, annotations, parsed

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

  def __init__(self, socketpath=None, notify=None, tmux_exe=None):
    if socketpath is None:
      socketpath = self.get_socketpath()
    if notify is None:
      notify = self.default_notify
    if tmux_exe is None:
      tmux_exe = self.TMUX
    self.fspath = socketpath
    self.notify = notify
    self.tmux_exe = tmux_exe
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
        [self.tmux_exe, '-S', self.fspath, '-C'],
        stdin=PIPE,
        stdout=PIPE,
    ) as P:
      try:
        pending = []  # queue of pending Results
        with stackattrs(self, rf=P.stdout, wf=P.stdin, pending=pending):
          workerT = bg(self._worker, name='tmux response parser')
          with stackattrs(self, workerT=workerT):
            yield
      finally:
        P.stdin.close()
        workerT.join()
        P.wait()

  def default_notify(self, bs: bytes):
    arg0, *args = TmuxCommandResponse.argv(bs)
    if arg0 == 'output':
      return
    info("%%%s %r", arg0, args)

  def _worker(self):
    ''' Worker function to read the initial response
        and then all subsequent responses, using them to complete pending
        command `Result`s.
    '''
    rf = self.rf
    pending = self.pending
    notify = self.notify
    lock = self._lock
    while True:
      rsp = TmuxCommandResponse.read_response(rf, notify=notify)
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
  def submit(self, tmux_command: str) -> Result:
    ''' Submit `tmux_command`, return a `Result`
        for collection of the `TmuxCommandResponse`.
    '''
    wf = self.wf
    with self._lock:
      R = Result(f'{tmux_command}')
      self.pending.append(R)
      wf.write(tmux_command.encode('utf-8'))
      wf.write(b'\n')
      wf.flush()
    return R

  # TODO: worker thread to consume the control data and complete Results

  @pfx_method
  @typechecked
  def __call__(self, tmux_command: str) -> TmuxCommandResponse:
    with self:
      R = self.submit(tmux_command)
      return R()

def tmux(tmux_command, *tmux_args) -> CompletedProcess:
  ''' Execute the tmux(1) command `tmux_command`.
  '''
  return run('tmux', tmux_command, *tmux_args, quiet=False)

@trace
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
  with TmuxControl() as tm:
    print(tm('list-sessions'))
    print(tm('list-panes'))
