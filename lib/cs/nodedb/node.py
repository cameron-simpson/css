#!/usr/bin/python
#

import sys
if sys.hexversion < 0x02060000:
  from sets import Set as set
import itertools
import unittest
from cs.lex import str1
from cs.misc import the
from cs.mappings import parseUC_sAttr

class _AttrList(list):
  
  def __init__(self, node, key):
    ''' Initialise an _AttrList, a list subtype that understands Nodes
        and .ATTR[s] attribute access and drives a backend.
        `node` is the node to which this _AttrList is attached.
        `key` is the _singular_ form of the attribute name.
    '''
    list.__init__(self)
    self.node = node
    self.key = key
    self.nodedb = node.nodedb

  def __intern(self, value):
    if isinstance(value, Node):
      return value
    if type(value) is str:
      if value.startswith(':'):
        return ':'+value
    return value

  def __extern(self, stored):
    if type(stored) is not str:
      return stored
    if stored.startswith('::'):
      return stored[1:]
    if not stored.startswith(':'):
      return stored
    assert False, '":TYPE:NAME" et al not yet implemented'

  def _detach(self, noBackend=False):
    assert self.node is not None, "_detach() of unattached _AttrList: %s" % (self,)
    if not noBackend:
      N = self.node
      self.nodedb._backend.delAttr(N.type, N.name, self.key)
    self.node = None

  def append(self, value, noBackend=False):
    if not noBackend:
      N = self.node
      self.nodedb._backend.extendAttr(N.type, N.name, self.key, (value,))
    list.append(self, self.__intern(value))

  def extend(self, values, noBackend=False):
    # turn iterator into tuple
    if not noBackend and type(values) not in (list, tuple):
      values = tuple(values)
    if not noBackend:
      N = self.node
      self.nodedb._backend.extendAttr(N.type, N.name, self.key, values)
    list.extend(self, [ self.__intern(value) for value in values ])

  def __getitem__(self, index):
    assert type(index) is int, "non-int indices not yet supported: "+repr(index)
    return self.__extern(list.__getitem__(self, index))

  def __setitem__(self, index, value, noBackend=False):
    assert type(index) is int, "non-int indices not yet supported: "+repr(index)
    if not noBackend:
      N = self.node
      self.nodedb._backend.delAttr(N.type, N.name, self.key)
      self.nodedb._backend.extendAttr(N.type, N.name, self.key, self)
    list.__setitem__(self, index, self.__intern(value))

  def __getattr__(self, attr):
    k, plural = parseUC_sAttr(attr)
    if k:
      ks = k+'s'
      hits = itertools.chain(*[ N[ks] for N in self ])
      if plural:
        return list(hits)
      return the(hits)
    raise AttributeError, repr(attr)

class Node(dict):
  ''' A Node dictionary.
      Entries are _AttrLists, keyed by attribute name in plural form.
  '''

  def __init__(self, t, name, nodedb):
    self.type = str1(t)
    self.name = name
    self.nodedb = nodedb

  def __repr__(self):
    return "%s:%s:%s" % (self.type, self.name, dict.__repr__(self))

  def __str__(self):
    return self.type+":"+self.name

  def __eq__(self, other):
    return self.name == other.name and self.type == other.type

  def __hash__(self):
    return hash(self.name)^hash(self.type)^id(self.nodedb)

  def __get(self, k, plural):
    assert k is not None
    ks = k+'s'
    if ks not in self:
      row = self[ks] = _AttrList(self, ks)
    else:
      row = dict.__getitem__(self, ks)
    if plural:
      return row
    if len(row) == 1:
      return row[0]
    return None

  def __getitem__(self, item):
    k, plural = parseUC_sAttr(item)
    if k:
      value = self.__get(k, plural)
      if value is not None:
        return value
    raise KeyError, repr(item)

  def __setitem__(self, item, value):
    k, plural = parseUC_sAttr(item)
    if k is None:
      raise KeyError, repr(item)
    ks = k+'s'
    row = _AttrList(self, k)
    if plural:
      row.extend(value)
    else:
      row.append(value)
    dict.__setitem__(self, ks, row)
    self.nodedb._backend.delAttr(self.type, self.name, k)
    self.nodedb._backend.extendAttr(self.type, self.name, k, row)

  def __delitem__(self, item):
    k, plural = parseUC_sAttr(item)
    if k is None:
      raise KeyError, repr(item)
    ks = k+'s'
    if not plural:
      if len(self[ks]) != 1:
        raise KeyError, repr(item)
    dict.__delitem__(self, ks)

  def __hasattr__(self, attr):
    k, plural = parseUC_sAttr(item)
    if k:
      ks = k+'s'
      if ks not in self:
        return False
      return len(self[ks]) > 0
    return dict.__hasattr__(self, attr)
  def __getattr__(self, attr):
    k, plural = parseUC_sAttr(attr)
    if k:
      value = self.__get(k, plural)
      if value is None:
        raise AttributeError, attr
      return value
    return dict.__getattr__(self, attr)

  def __setattr__(self, attr, value):
    k, plural = parseUC_sAttr(attr)
    if k is None:
      dict.__setattr__(self, attr, value)
    else:
      self[attr] = value

def nodekey(*args):
  ''' Convert some sort of key to a (TYPE, NAME) tuple.
      Sanity check the values.
      Return (TYPE, NAME).
  '''
  if len(args) == 2:
    t, name = args
    assert type(t) is str
    assert type(name) is str
  elif len(args) == 1:
    item = args[0]
    if type(item) is str:
      # TYPE:NAME
      t, name = item.split(':', 1)
    else:
      # (TYPE, NAME)
      t, name = item
      assert type(t) is str
      assert type(name) is str
    assert t.isupper()
    assert len(name) > 0
    k, plural = parseUC_sAttr(t)
    assert k is not None and not plural
  else:
    raise TypeError, "newNode() takes (TYPE, NAME) args or a single arg: args=%s" % ( args, )
  return t, name

class NodeDB(dict):

  def __init__(self, backend=None):
    dict.__init__(self)
    if backend is None:
      backend = _NoBackend(self)
    self._backend = backend
    backend.set_nodedb(self)
    self.__nodesByType = {}

  def _noteNode(self, N):
    ''' Update the cross reference tables for a new Node.
    '''
    t = N.type
    byType = self.__nodesByType
    if t not in byType:
      byType[t] = set()
    byType[t].add(N)

  def _forgetNode(self, N):
    ''' Update the cross reference tables for removal of a Node.
    '''
    self.__nodesByType[N.type].remove(N)

  def types(self):
    ''' Return a list of the types in use.
    '''
    byType = self.__nodesByType
    return [ t for t in byType.keys() if byType[t] ]

  def __contains__(self, item):
    key = nodekey(item)
    return dict.__contains__(self, key)

  def __getitem__(self, item):
    key = nodekey(item)
    N = dict.__getitem__(self, key)
    assert isinstance(N, Node), "__getitem(%s) got non-Node: %s" % (item, repr(N))
    return N

  def __setitem__(self, item, N):
    assert isinstance(N, Node), "tried to store non-Node: %s" % (repr(N),)
    key = nodekey(item)
    assert key == (N.type, N.name), \
           "tried to store Node(%s:%s) as key (%s:%s)" \
             % (N.type, N.name, key[0], key[1])
    if key in self:
      self._forgetNode(self[key])
    dict.__setitem__(self, key, N)
    self._noteNode(N)

  def newNode(self, *args):
    t, name = nodekey(*args)
    N = self._makeNode(t, name)
    self._backend.newNode(t, name)
    return N

  def _makeNode(self, t, name):
    assert (t, name) not in self, 'newNode(%s): %s already exists' % (", ".join(args), (t, name), )
    N = self[t, name] = Node(t, name, self)
    return N

class Backend(object):
  ''' Base class for NodeDB backends.
  '''

  def set_nodedb(self, nodedb):
    ''' Set the nodedb controlling this backend.
        Called by NodeDB.__init__().
    '''
    assert not hasattr(self, 'nodedb')
    self.nodedb = nodedb

  def newNode(self, t, name):
    raise NotImplemented

  def delNode(self, t, name):
    raise NotImplemented

  def extendAttr(self, t, name, attr, values):
    raise NotImplemented

  def delAttr(self, t, name, attr):
    raise NotImplemented

class _NoBackend(Backend):
  ''' Dummy backend for emphemeral in-memory NodeDBs.
  '''
  def newNode(self, t, name):
    pass
  def delNode(self, t, name):
    pass
  def extendAttr(self, t, name, attr, values):
    pass
  def delAttr(self, t, name, attr):
    pass

class TestAll(unittest.TestCase):

  def setUp(self):
    self.backend=_NoBackend()
    self.db=NodeDB(backend=self.backend)

  def test00newNode(self):
    H = self.db.newNode('HOST', 'foo')
    self.assertEqual(len(H.ATTR1s), len(()) )
    self.assertRaises(AttributeError, getattr, H, 'ATTR2')

if __name__ == '__main__':
  import sqlalchemy
  print 'SQLAlchemy version =', sqlalchemy.__version__
  unittest.main()
