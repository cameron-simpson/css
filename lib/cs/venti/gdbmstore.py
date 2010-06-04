#!/usr/bin/python
#
# Block stores.
#       - Cameron Simpson <cs@zip.com.au>
#

from __future__ import with_statement
import os.path
from thread import allocate_lock
from cs.venti.store import IndexedFileStore

class GDBMStore(IndexedFileStore):
  ''' An IndexedFileStore attached to a GDBM index.
  '''
  def __init__(self, dir, capacity=None):
    IndexedFileStore.__init__(self, dir, capacity=capacity)

  def _getIndex(self):
    return GDBMIndex(os.path.join(self.dir, "index.gdbm"))

class GDBMIndex(object):
  ''' A GDBM index for a GDBMStore.
  '''
  def __init__(self, tcpath):
    import gdbm
    self._lock=allocate_lock()
    self._db=gdbm.open(tc,"cf")

  def flush(self):
    pass

  def sync(self):
    self._db.sync()

  def __setitem__(self, h, entry):
    with self._lock:
      self._db[h] = entry

  def __getitem__(self, h):
    # fetch and decode
    with self._lock:
      return self._db[h]

  def __contains__(self, h):
    with self._lock:
      return h in self._db
