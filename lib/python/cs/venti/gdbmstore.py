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
  def __init__(self, dirpath, capacity=None):
    IndexedFileStore.__init__(self, dirpath, capacity=capacity)

  def _getIndex(self):
    return GDBMIndex(os.path.join(self.dirpath, "index.gdbm"))

class GDBMIndex(object):
  ''' A GDBM index for a GDBMStore.
  '''
  def __init__(self, gdbmpath):
    import gdbm
    self._lock = allocate_lock()
    self._db = gdbm.open(gdbmpath,"cf")

  def flush(self):
    pass

  def sync(self):
    self._db.sync()

  def __setitem__(self, h, entry):
    ##D("GDBM store %s" % (`h`,))
    with self._lock:
      self._db[h] = entry

  def __getitem__(self, h):
    ##D("GDBM fetch %s" % (`h`,))
    with self._lock:
      return self._db[h]

  def __contains__(self, h):
    with self._lock:
      return h in self._db
