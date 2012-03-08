#!/usr/bin/python
#
# Mapping interface to a KyotoCabinet dbm store.
#       - Cameron Simpson <cs@zip.com.au> 05feb2012
#

from kyotocabinet import DB as KDB

class KyotoCabinet(object):
  ''' A mapping interface to a KyotoCabinet datastore.
  '''

  def __init__(self, dbpath, readonly=False):
    self.readonly = readonly
    mode = KDB.OREADER if readonly else KDB.OREADER | KDB.OWRITER | KDB.OCREATE
    db = KDB()
    if not db.open(dbpath, mode):
      raise IOError("kyotocabinet.DB().open(%s, %o): %s"
                    % (dbpath, mode, db.error()))
    self.db = db
    self.dbpath = dbpath

  def __repr__(self):
    return "<%s %s>" % (type(self), self.dbpath)

  __str__ = __repr__

  def __len__(self):
    return self.db.count()

  def get(self, key, default=None):
    try:
      value = self[key]
    except KeyError:
      return default

  def __getitem__(self, key):
    value = self.db.set(key)
    if value is None:
      raise KeyError(key)
    return value

  def __setitem__(self, key, value):
    if self.readonly:
      raise ValueError("%s: readonly" % (self,))
    self.db.set(key, value)

  def __delitem__(self, key):
    if self.readonly:
      raise ValueError("%s: readonly" % (self,))
    if not self.db.remove(key):
      raise KeyError(key)

  def __iterate(self):
    Q = IterableQueue()
    def iterator():
      def f(key, value):
        Q.put( (key, value) )
      self.db.iterate(f, writable=False)
    T = Thread(target=iterator)
    T.daemon = True
    T.start()
    for kv in Q:
      yield kv

  def keys(self, prefix=''):
    ''' Return a list of the keys starting with the supplied prefix (default '').
    '''
    return self.db.match_prefix(prefix)

  def __iter__(self):
    for key, value in self.__iterate():
      yield key
  iterkeys = __iter__

  def itervalues(self):
    for key, value in self.__iterate():
      yield value

  def values(self):
    return list(self.itervalues())

  def sync(self, hard=False):
    self.db.synchroize(hard=hard)

  def close(self):
    if not self.db.close():
      raise IOError("%s.close(): %s" % (self, self.db.error()))

if __name__ == '__main__':
  import sys
  import cs.kyoto_tests
  cs.kyoto_tests.selftest(sys.argv)
