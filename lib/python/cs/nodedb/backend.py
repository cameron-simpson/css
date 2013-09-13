#!/usr/bin/python
#
# Backend base classes.
#       - Cameron Simpson <cs@zip.com.au>
#

from threading import Condition
import unittest
from cs.logutils import D, OBSOLETE, debug
from cs.obj import O
from cs.threads import locked_property
from cs.excutils import unimplemented
from cs.debug import Lock, RLock, Thread
from cs.py3 import Queue, Queue_Full as Full, Queue_Empty as Empty

class _BackendMappingMixin(O):
  ''' A mapping interface to be presented by all Backends.
  '''

  def len(self):
    return len(self.keys())

  @unimplemented
  def iterkeys(self):
    ''' Yield (type, name) tuples for all nodes in the backend database.
    '''
    pass

  def keys(self):
    return list(self.iterkeys())

  def iteritems(self):
    ''' Yield ( (type, name), node_dict ) tuples for all nodes in
        the backend database.
    '''
    for key in self.iterkeys():
      yield key, self[key]

  def items(self):
    return list(self.iteritems())

  def itervalues(self):
    ''' Yield node_dict for all nodes in the backend database.
    '''
    for key in self.iterkeys():
      yield self[key]

  def values(self):
    return list(self.itervalues())

  @unimplemented
  def __getitem__(self, key):
    ''' Return a dict with a mapping of attr => values for the
        specified node key.
    '''
    pass

  def get(self, key, default):
    try:
      value = self[key]
    except KeyError:
      return default
    return value

  @unimplemented
  def __setitem__(self, key, node_dict):
    pass

  @unimplemented
  def __delitem__(self, key):
    pass

  __hash__ = None

  def __eq__(self, other):
    keys = set(self.keys())
    okeys = set(other.keys())
    if keys != okeys:
      raise Error
      ##print >>sys.stderr, "1: keys[%s] != okeys[%s]" % (keys, okeys)
      ##sys.stderr.flush()
      return False
    for k in keys:
      if self[k] != other[k]:
        raise Error
        ##print >>sys.stderr, "2: %s != %s" % (self[k], other[k])
        ##sys.stderr.flush()
        return False
    return True

  def __ne__(self, other):
    return not self == other

class _BackendUpdateQueue(O):
  ''' Mixin to supplied the update queue and associated facilities.
  '''

  def __init__(self):
    self._updateQ = Queue(1024)
    self._update_count = 0
    self._updated_count = 0
    self._queue_updates = True
    self._update_lock = RLock()
    self._update_cond = Condition(self._update_lock)

  @locked_property
  def _update_thread(self):
    T = Thread(target=self._monitor, args=(self._updateQ,))
    debug("start monitor thread...")
    T.start()
    return T

  def _queue(self, row):
    ''' Queue the supplied row (TYPE, NAME, ATTR, VALUE) for the backend update thread.
    '''
    if self._queue_updates:
      if self.readonly:
        debug("readonly: do not queue %r", row)
      elif self.closed:
        raise RuntimeError("%s closed: not queuing %r" % (self, row))
      else:
        debug("queue %r", row)
        self._updateQ.put(row)
        with self._lock:
          self._update_count += 1

  def sync(self):
    ''' Wait for the update queue to complete to the current update_count.
    '''
    with self._lock:
      update_count = self._update_count
    while True:
      with self._update_lock:
        debug("polling self._updated_count (%d) - need (%d)", self._updated_count, update_count)
        ready = self._updated_count >= update_count
        if ready:
          break
        debug("sync: not ready, waiting for another notification")
        self._update_cond.wait()

class Backend(_BackendMappingMixin, _BackendUpdateQueue):
  ''' Base class for NodeDB backends.
  '''

  def __init__(self, readonly):
    self.nodedb = None
    self.readonly = readonly
    self.changed = False
    self.closed = False
    if not readonly:
      _BackendUpdateQueue.__init__(self)
    self._lock = Lock()

  def nodedata(self):
    ''' Yield node data in:
          type, name, attrmap
        form.
    '''
    for k, attrmap in self.iteritems():
      k1, k2 = k
      yield k1, k2, attrmap

  def apply_to(self, nodedb):
    ''' Apply the nodedata from this backend to a NodeDB.
        This can be overridden by subclasses to provide some backend specific
        efficient implementation.
    '''
    nodedb.apply_nodedata(self.nodedata())

  @OBSOLETE
  def totext(self, value):
    ''' Hook for subclasses that might do special encoding for their backend.
        Discouraged.
        Instead, subtypes of NodeDB should register extra types they store
        using using NodeDB.register_attr_type().
        See cs/venti/nodedb.py for an example.
    '''
    return self.nodedb.totext(value)

  @OBSOLETE
  def fromtext(self, value):
    ''' Hook for subclasses that might do special decoding for their backend.
        Discouraged.
    '''
    ##assert False, "OBSOLETE"
    return self.nodedb.fromtext(value)

  def close(self):
    ''' Basic close: sync, detach from NodeDB, mark as closed.
    '''
    self.closed = True
    self.sync()
    self._update_thread.join()
    self.nodedb = None

  def setAttr(self, t, name, attr, values):
    ''' Save the full contents of this attribute list.
    '''
    self.delAttr(t, name, attr)
    if values:
      self.extendAttr(t, name, attr, values)

  @unimplemented
  def extendAttr(self, t, name, attr, values):
    ''' Append values to the named attribute.
    '''
    pass

  @unimplemented
  def delAttr(self, t, name, attr):
    ''' Remove all values from the named attribute.
    '''
    pass

class TestAll(unittest.TestCase):

  def setUp(self):
    self.db = NodeDB(backend=None)

if __name__ == '__main__':
  unittest.main()
