#!/usr/bin/python
#
# Backend base classes.
#       - Cameron Simpson <cs@zip.com.au>
#

import unittest
from cs.logutils import D, OBSOLETE
from cs.misc import unimplemented, O

class _BackendMappingMixin(O):
  ''' A mapping interface to be presented by all Backends.
  '''

  def len(self):
    return len(self.keys())

  def keys(self):
    return list(self.iterkeys())

  @unimplemented
  def iterkeys(self):
    ''' Yield (type, name) tuples for all nodes in the backend database.
    '''
    pass

  def items(self):
    return list(self.iteritems())

  def iteritems(self):
    ''' Yield ( (type, name), node_dict ) tuples for all nodes in
        the backend database.
    '''
    for key in self.iterkeys():
      yield key, self[key]

  def values(self):
    return list(self.itervalues())

  def itervalues(self):
    ''' Yield node_dict for all nodes in the backend database.
    '''
    for key in self.iterkeys():
      yield self[key]

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

class Backend(_BackendMappingMixin):
  ''' Base class for NodeDB backends.
  '''

  def __init__(self, readonly):
    self.nodedb = None
    self.readonly = readonly
    self.changed = False

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
    self.sync()
    self.nodedb = None
    self.closed = True

  @unimplemented
  def sync(self):
    pass

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

class _QBackend(Backend):
  ''' A backend to accept updates and queue them for asynchronous
      completion via another backend.
  '''

  def __init__(self, backend, maxq=None):
    if maxq is None:
      maxq = 1024
    else:
      assert maxq > 0
    self.backend = backend
    self._Q = IterableQueue(maxq)
    self._T = Thread(target=self._drain)
    self._T.start()

  def close(self):
    self._Q.close()
    self._T.join()
    self._T = None

  def _drain(self):
    for what, args in self._Q:
      what(*args)

  def newNode(self, t, name):
    self._Q.put( (self.backend.newNode, (t, name,)) )
  def delNode(self, t, name):
    self._Q.put( (self.backend.delNode, (t, name,)) )
  def extendAttr(self, t, name, attr, values):
    self._Q.put( (self.backend.extendAttr, (t, name, attr, values)) )
  def delAttr(self, t, name, attr):
    self._Q.put( (self.backend.delAttr, (t, name, attr)) )

class TestAll(unittest.TestCase):

  def setUp(self):
    self.db = NodeDB(backend=None)

if __name__ == '__main__':
  unittest.main()
