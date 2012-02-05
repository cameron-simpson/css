#!/usr/bin/python
#
# A Store using a KyotoCabinet backend.
#       - Cameron Simpson <cs@zip.com.au> 05feb2012
#

from cs.kyoto import KyotoCabinet
from .store import IndexedFileStore, MappingStore

class KyotoCabinetIndexedFileStore(IndexedFileStore):
  ''' An IndexedFileStore attached to a KyotoCabinet index.
  '''

  def _getIndex(self):
    return KyotoIndex(os.path.join(self.dirpath, "index.kch"))

class KyotoCabinetStore(MappingStore):

  def __init__(self, dbpath, name=None, capacity=None):
    if name is None:
      name = "KyotoCabinetStore(%s)" % (dbpath,)
    self.kydb = KyotoCabinet(dbpath)
    MappingStore.__init__(self, self.kydb, name=name, capacity=capacity)

  def sync(self):
    self.kydb.sync()
