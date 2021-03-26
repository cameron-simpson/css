#!/usr/bin/python

''' Assorted debugging assistance functions.
'''

from binascii import hexlify
from collections import namedtuple
from threading import (
    Lock as threading_Lock,
    RLock as threading_RLock,
    current_thread,
)
from cs.fileutils import shortpath
from cs.logutils import warning
from cs.py.stack import caller, stack_dump
from cs.tty import ttysize
from cs.upd import print
from cs.x import X

def dump_Block(block, indent=''):
  ''' Dump a Block.
  '''
  X("%s%s", indent, block)
  if block.indirect:
    indent += '  '
    subblocks = block.subblocks
    X(
        "%sindirect %d subblocks, span %d bytes", indent, len(subblocks),
        len(block)
    )
    for B in subblocks:
      dump_Block(B, indent=indent)

def dump_Dirent(E, indent='', recurse=False, not_dir=False):
  ''' Dump a Dirent.
  '''
  X("%s%r", indent, E)
  if E.isdir and not not_dir:
    indent += '  '
    for name in sorted(E.keys()):
      E2 = E[name]
      dump_Dirent(E2, indent, recurse=recurse, not_dir=not recurse)

def dump_chunk(data, leadin, max_width=None, one_line=False):
  ''' Dump a data chunk.
  '''
  if max_width is None:
    _, columns = ttysize(1)
    if columns is None:
      columns = 80
    max_width = columns - 1
  leadin += ' %5d' % (len(data),)
  leadin2 = ' ' * len(leadin)
  data_width = max_width - len(leadin)
  slice_size = (data_width - 1) // 3
  assert slice_size > 0
  doff = 0
  while doff < len(data):
    doff2 = doff + slice_size
    chunk = data[doff:doff2]
    hex_text = hexlify(chunk).decode('utf-8')
    txt_text = ''.join(
        c if c.isprintable() else '.' for c in chunk.decode('iso8859-1')
    )
    print(leadin, txt_text, hex_text)
    if one_line:
      break
    leadin = leadin2
    doff = doff2

def dump_Store(S, indent=''):
  ''' Dump a description of a Store.
  '''
  from .cache import FileCacheStore
  from .store import MappingStore, ProxyStore, DataDirStore
  X("%s%s:%s", indent, type(S).__name__, S.name)
  indent += '  '
  if isinstance(S, DataDirStore):
    X("%sdir = %s", indent, shortpath(S._datadir.topdirpath))
  elif isinstance(S, FileCacheStore):
    X("%sdatadir = %s", indent, shortpath(S.cache.dirpath))
  elif isinstance(S, ProxyStore):
    for attr in 'save', 'read', 'save2', 'read2', 'copy2':
      backends = getattr(S, attr)
      if backends:
        backends = sorted(backends, key=lambda S: S.name)
        X(
            "%s%s = %s", indent, attr,
            ','.join(backend.name for backend in backends)
        )
        for backend in backends:
          dump_Store(backend, indent + '  ')
  elif isinstance(S, MappingStore):
    mapping = S.mapping
    X("%smapping = %s", indent, type(mapping))
  else:
    X("%sUNRECOGNISED Store type", indent)

_LockContext = namedtuple("LockContext", "caller thread")

class DebuggingLock:
  ''' A wrapper for a threading Lock or RLock
      to notice contention and report contending uses.
  '''

  def __init__(self, recursive=False):
    self.recursive = recursive
    self.trace_acquire = False
    self._lock = threading_RLock() if recursive else threading_Lock()
    self._held = None

  def __repr__(self):
    return "%s(lock=%r,held=%s)" % (
        type(self).__name__, self._lock, self._held
    )

  def acquire(self, timeout=-1, _caller=None):
    ''' Acquire the lock and note the caller who takes it.
    '''
    if _caller is None:
      _caller = caller()
    lock = self._lock
    hold = _LockContext(_caller, current_thread())
    if timeout != -1:
      warning(
          "%s:%d: lock %s: timeout=%s", hold.caller.filename,
          hold.caller.lineno, lock, timeout
      )
    contended = False
    if True:
      if lock.acquire(0):
        lock.release()
      else:
        contended = True
        held = self._held
        warning(
            "%s:%d: lock %s: waiting for contended lock, held by %s:%s:%d",
            hold.caller.filename, hold.caller.lineno, lock, held.thread,
            held.caller.filename, held.caller.lineno
        )
    acquired = lock.acquire(timeout=timeout)
    if contended:
      warning(
          "%s:%d: lock %s: %s", hold.caller.filename, hold.caller.lineno, lock,
          "acquired" if acquired else "timed out"
      )
    self._held = hold
    if acquired and self.trace_acquire:
      X("ACQUIRED %r", self)
      stack_dump()
    return acquired

  def release(self):
    ''' Release the lock and forget who took it.
    '''
    self._held = None
    self._lock.release()

  def __enter__(self):
    ##X("%s.ENTER...", type(self).__name__)
    self.acquire(_caller=caller())
    ##X("%s.ENTER: acquired, returning self", type(self).__name__)
    return self

  def __exit__(self, exc_type, exc_val, exc_tb):
    self.release()
    return False

  def _is_owned(self):
    lock = self._lock
    return lock._is_owned()
