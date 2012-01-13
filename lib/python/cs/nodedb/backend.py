#!/usr/bin/python
#
# Backend base classes.
#       - Cameron Simpson <cs@zip.com.au>
#

import unittest

class _BackendMappingMixin(object):
  ''' A mapping interface to be presented by all Backends.
  '''

  def len(self):
    return len(self.keys())

  def keys(self):
    return list(self.iterkeys())

  def iterkeys(self):
    ''' Yield (type, name) tuples for all nodes in the backend database.
    '''
    raise NotImplementedError

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

  def __getitem__(self, key):
    ''' Return a dict with a mapping of attr => values for the
        specified node key.
    '''
    raise NotImplementedError

  def get(self, key, default):
    try:
      value = self[key]
    except KeyError:
      return default
    return value

  def __setitem__(self, key, node_dict):
    raise NotImplementedError

  def __delitem__(self, key):
    raise NotImplementedError

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

class Backend(_BackendMappingMixin):
  ''' Base class for NodeDB backends.
  '''

  def set_nodedb(self, nodedb):
    ''' Set the nodedb controlling this backend.
        Called by NodeDB.__init__().
    '''
    assert not hasattr(self, 'nodedb')
    self.nodedb = nodedb

  def nodedata(self):
    ''' Yield node data in:
          type, name, attrmap
        form.
    '''
    for k, attrmap in self.iteritems():
      yield k[0], k[1], attrmap

  def apply(self, nodedb):
    ''' Apply the nodedata from this backend to a NodeDB.
        This can be overridden by subclasses to provide some backend specific
        efficient implementation.
    '''
    nodedb.apply_nodedata(self.nodedata())

  def totext(self, value):
    ''' Hook for subclasses that might do special encoding for their backend.
        Discouraged.
        Instead, subtypes of NodeDB should register extra types they store
        using using NodeDB.register_attr_type().
        See cs/venti/nodedb.py for an example.
    '''
    return self.nodedb.totext(value)

  def fromtext(self, value):
    ''' Hook for subclasses that might do special decoding for their backend.
        Discouraged.
    '''
    ##assert False, "OBSOLETE"
    return self.nodedb.fromtext(value)

  def close(self):
    raise NotImplementedError

  def saveAttrs(self, attrs):
    ''' Save the full contents of this attribute list.
    '''
    N = attrs.node
    attr = attrs.attr
    self.delAttr(N.type, N.name, attr)
    if attrs:
      self.extendAttr(N.type, N.name, attr, attrs)

  def extendAttr(self, N, attr, values):
    ''' Append values to the named attribute.
    '''
    raise NotImplementedError

  def delAttr(self, N, attr):
    ''' Remove all values from the named attribute.
    '''
    raise NotImplementedError

  def set1Attr(self, N, attr, value):
    raise NotImplementedError

class _NoBackend(Backend):
  ''' Dummy backend for emphemeral in-memory NodeDBs.
  '''
  def close(self):
    pass
  def extendAttr(self, type, name, attr, values):
    pass
  def delAttr(self, type, name, attr):
    pass
  def set1Attr(self, type, name, attr, value):
    pass
  def iterkeys(self):
    if False:
      yield None

  def __getitem__(self, key):
    raise KeyError
  def __setitem__(self, key, N):
    pass
  def __delitem__(self, key):
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

  def sync(self):
    raise NotImplementedError

  def close(self):
    self._Q.close()
    self._T.join()
    self._T = None

  def _drain(self):
    for what, args in self._Q:
      what(*args)

  def newNode(self, N):
    self._Q.put( (self.backend.newNode, (N,)) )
  def delNode(self, N):
    self._Q.put( (self.backend.delNode, (N,)) )
  def extendAttr(self, N, attr, values):
    self._Q.put( (self.backend.extendAttr, (N, attr, values)) )
  def set1Attr(self, N, attr, value):
    self._Q.put( (self.backend.set1Attr, (N, attr, value)) )
  def delAttr(self, N, attr):
    self._Q.put( (self.backend.delAttr, (N, attr)) )

class TestAll(unittest.TestCase):

  def setUp(self):
    self.db = NodeDB(backend=None)

if __name__ == '__main__':
  unittest.main()
