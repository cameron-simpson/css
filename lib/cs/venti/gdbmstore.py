#!/usr/bin/python
#
# Block stores.
#       - Cameron Simpson <cs@zip.com.au>
#

from __future__ import with_statement
import sys
import os.path
import time
from zlib import compress
from thread import allocate_lock
from cs.logutils import debug
from cs.cache import LRU
from cs.serialise import toBS, fromBS, fromBSfp
from cs.venti import tohex
from cs.venti.store import IndexedFileStore
from cs.venti.datafile import scanFile, getBlock, addBlock

class GDBMStore(IndexedFileStore):
  ''' An IndexedFileStore attached to a GDBM index.
  '''
  def __init__(self, dir, capacity=None):
    debug("GDBMStore.__init__...")
    IndexedFileStore.__init__(self, dir, capacity=capacity)

  def _getIndex(self):
    return GDBMIndex(os.path.join(self.dir, "index.gdbm"))

class GDBMIndex(object):
  ''' A GDBM index for a GDBMStore.
  '''
  def __init__(self, gdbmpath):
    import gdbm
    self.lock=allocate_lock()
    self.__db=gdbm.open(gdbmpath,"cf")

  def flush(self):
    pass

  def sync(self):
    debug("GDBMIndex.__db.sync()")
    self.__db.sync()

  def __setitem__(self,h,noz):
    with self.lock:
      self.__db[h]=noz

  def __getitem__(self,h):
    # fetch and decode
    with self.lock:
      return self.__db[h]

  def __contains__(self,h):
    with self.lock:
      return h in self.__db
