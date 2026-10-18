#!/usr/bin/env python3

''' Print a nice process tree.
'''

import os

from psutil import AccessDenied, NoSuchProcess, Process, ZombieProcess

from cs.gimmicks import warning
from cs.printt import printt

class _DummyProcess:

  def __init__(self, pid: int):
    self.pid = pid

  def name(self):
    return f'<pid:{self.pid}>'

def ps_table(
    pid=None, ancestor=None, *, as_row=None, recurse=False, seen=None
):
  if pid is None:
    pid = os.getpid()
  if isinstance(pid, int):
    P = Process(pid)
  elif isinstance(pid, Process):
    P = pid
    pid = P.pid
  else:
    raise TypeError(
        f'unhandled type {pid.__class__}, expected int or process,  for {pid=}'
    )
  if seen is None:
    seen = {pid}
  else:
    seen.add(pid)
  if as_row is None:

    def as_row(P: Process) -> list:
      try:
        argv = P.cmdline()
      except AccessDenied:
        argv = [P.name(), '<....>']
      except NoSuchProcess:
        argv = [P.name(), '<MISSING>']
      except ZombieProcess:
        argv = [P.name(), '<ZOMBIE>']
      ##print("AS_ROW", P)
      ##breakpoint()
      return [P.name(), P.pid, ' '.join(argv)]

  P = Process(pid)
  table = [as_row(P)]
  if recurse:
    children = P.children()
    if children:
      children_table = []
      for child in sorted(children, key=lambda child: child.pid):
        subtable = ps_table(child, recurse=child.pid not in seen, seen=seen)
        children_table.extend(subtable)
      table.append(tuple(children_table))
  if ancestor is not None:
    apid = pid
    while apid != ancestor:
      parent = P.parent()
      if parent is None:
        warning(f'ps_table: {ancestor=} is not an ancestor of {pid=}')
        break
  return table

def ps(pid=None, ancestor=None, *, as_row=None, recurse=False, seen=None):
  table = ps_table(pid, ancestor, as_row=as_row, recurse=recurse, seen=seen)
  printt(['ARGV0', 'PID', 'CMDLINE'], *table)

if __name__ == '__main__':
  import sys
  ps()
  ps(1, recurse=True)
  sys.exit(0)
