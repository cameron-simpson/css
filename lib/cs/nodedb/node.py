#!/usr/bin/python
#

import re
import sys
if sys.hexversion < 0x02060000:
  from sets import Set as set
  import simplejson as json
else:
  import json
import itertools
import unittest
from cs.lex import str1
from cs.misc import the
from cs.mappings import parseUC_sAttr
from cs.logutils import error

# regexp to match TYPE:name
re_NODEREF = re.compile(r'([A-Z]+):([^:#]+)')
# regexp to match a bareword name
re_NAME = re.compile(r'[a-z][a-z0-9]*(?![a-zA-Z0-9_])')
# JSON string expression, lenient
re_STRING = re.compile(r'"([^"\\]|\\.)*"')
# JSON simple integer
re_INT = re.compile(r'-?[0-9]+')
# "bare" URL
re_BAREURL = re.compile(r'[a-z]+://[-a-z0-9.]+/[-a-z0-9_.]+')
# regexp to match name(, name)*
re_NAMELIST = re.compile(r'([a-z][a-z0-9]+)(\s*,\s*([a-z][a-z0-9]+))*')
# regexp to do comma splits
re_COMMASEP = re.compile(r'\s*,\s*')

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

  def _detach(self, noBackend=False):
    assert self.node is not None, "_detach() of unattached _AttrList: %s" % (self,)
    if not noBackend:
      N = self.node
      self.nodedb._backend.delAttr(N, self.key)
    self.node = None

  def append(self, value, noBackend=False):
    if not noBackend:
      N = self.node
      self.nodedb._backend.extendAttr(N, self.key, (value,))
    list.append(self, value)

  def extend(self, values, noBackend=False):
    # turn iterator into tuple
    if not noBackend and type(values) not in (list, tuple):
      values = tuple(values)
    if not noBackend:
      N = self.node
      self.nodedb._backend.extendAttr(N, self.key, values)
    list.extend(self, values)

##def __getitem__(self, index):
##  assert type(index) is int, "non-int indices not yet supported: "+repr(index)
##  return list.__getitem__(self, index)

  def __setitem__(self, index, value, noBackend=False):
    assert type(index) is int, "non-int indices not yet supported: "+repr(index)
    if not noBackend:
      N = self.node
      self.nodedb._backend.delAttr(N, self.key)
      self.nodedb._backend.extendAttr(N, self.key, self)
    list.__setitem__(self, index, value)

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

  def __init__(self, t, name, nodedb, readonly=False):
    self.type = str1(t)
    self.name = name
    self.nodedb = nodedb
    self.readonly = readonly

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
      row = _AttrList(self, ks)
      dict.__setitem__(self, ks, row)
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
    assert k not in ('NAME', 'TYPE'), "forbidden ATTR \"%s\"" % (item,)
    ks = k+'s'
    row = _AttrList(self, k)
    if plural:
      if value:
        row.extend(value)
    else:
      row.append(value)
    if hasattr(self, ks):
      self.nodedb._backend.delAttr(self, k)
    if row:
      self.nodedb._backend.extendAttr(self, k, row)
    dict.__setitem__(self, ks, row)

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
    raise AttributeError, attr

  def __setattr__(self, attr, value):
    k, plural = parseUC_sAttr(attr)
    if k is None:
      dict.__setattr__(self, attr, value)
    else:
      self[attr] = value

  def parentsByAttr(self, attr, t=None):
    return self.nodedb.nodeParentsByAttr(self, attr, t)

  def attrValueText(self, attr, value):
    ''' Return "printable" form of a an attribute value.
    '''
    if isinstance(value, Node):
      if attr == "SUB"+self.type and value.type == self.type:
        pvalue = value.name
      elif attr == value.type:
        pvalue = value.name
      else:
        pvalue = str(value)
    else:
      m = re_BAREURL.match(value)
      if m is not None and m.end() == len(value):
        pvalue = value
      else:
        if value.isdigit() and str(int(value)) == value:
          pvalue = int(value)
        else:
          pvalue = json.dumps(value)
    return pvalue

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
    assert t.isupper(), "TYPE should be upper case, got \"%s\"" % (t,)
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
    self.__nodesByType = {}
    backend.set_nodedb(self)

  def _createNode(self, t, name):
    ''' Factory method to make a new Node (or Node subclass instance).
        Subclasses of NodeDB should use this to make Nodes of appropriate
        types.
    '''
    return Node(t, name, self)

  def close(self):
    self._backend.close()

  def nodesByType(self, t):
    return self.__nodesByType.get(t, ())

  def nodeParentsByAttr(self, N, attr, t=None):
    # TODO: make this efficient - it's currently brute force
    k, plural = parseUC_sAttr(attr)
    assert k and not plural, "bad attribute name \"%s\"" % (attr,)
    ks = k + 's'
    if t:
      Ps = self.nodesByType(t)
    else:
      Ps = self.values()
    return [ P for P in Ps if N in P[ks] ]

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

  def get(self, item, default=None):
    try:
      return self[item]
    except KeyError:
      return default

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
    self._backend.newNode(N)
    self[t, name] = N
    return N

  def _makeNode(self, t, name):
    assert (t, name) not in self, 'newNode(%s): %s already exists' % (", ".join(args), (t, name), )
    N = self[t, name] = self._createNode(t, name)
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
    self._preload()

  def _preload(self):
    raise NotImplementedError

  def close(self):
    raise NotImplementedError

  def nodeByTypeName(self, t, name):
    ''' Map (type,name) to Node.
    '''
    return self.nodedb[t, name]

  def serialise(self, value):
    ''' Convert a value for external string storage.
    '''
    if isinstance(value, Node):
      assert value.type.find(':') < 0, \
             "illegal colon in TYPE \"%s\"" % (value.type,)
      return ":%s:%s" % (value.type, value.name)
    t = type(value)
    assert t in (str,int), repr(t)+" "+repr(value)+" "+repr(Node)
    if t is str:
      if value.startswith(':'):
        return ':'+value
      return value
    if t is int:
      s = str(value)
      assert s[0].isdigit()
      return ':' + s
    raise ValueError, "can't serialise(%s)" % (repr(value),)

  def deserialise(self, value):
    ''' Convert a stored string into a value.
    '''
    if not value.startswith(':'):
      # plain string
      return value
    if len(value) < 2:
      raise ValueError, "unparsable value \"%s\"" % (value,)
    v = value[1:]
    if value.startswith('::'):
      # :string-with-leading-colon
      return v
    if v[0].isdigit():
      # :int
      return int(v)
    if v[0].isupper():
      # TYPE:NAME
      if v.find(':', 1) < 0:
        raise ValueError, "bad :TYPE:NAME \"%s\"" % (value,)
      t, name = v.split(':', 1)
      return self.nodeByTypeName(t, name)
    raise ValueError, "unparsable value \"%s\"" % (value,)

  def newNode(self, N):
    raise NotImplementedError

  def delNode(self, N):
    raise NotImplementedError

  def extendAttr(self, N, attr, values):
    raise NotImplementedError

  def delAttr(self, N, attr):
    raise NotImplementedError

class _NoBackend(Backend):
  ''' Dummy backend for emphemeral in-memory NodeDBs.
  '''
  def _preload(self):
    pass
  def close(self):
    pass
  def newNode(self, N):
    pass
  def delNode(self, N):
    pass
  def extendAttr(self, N, attr, values):
    pass
  def delAttr(self, N, attr):
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

  def close(self):
    self._Q.close()

  def _drain(self):
    B =  self.backend
    for what, args in self._Q:
      if what == 'newNode':
        B.newNode(*args)
      elif what == 'delNode':
        B.delNode(*args)
      elif what == 'delAttr':
        B.delAttr(*args)
      elif what == 'extendAttr':
        B.extendAttr(*args)
      else:
        error("unsupported backend request \"%s\"(%s)" % (what, args))

  def newNode(self, N):
    self._Q.put( ('newNode', (N,)) )
  def delNode(self, N):
    self._Q.put( ('delNode', (N,)) )
  def extendAttr(self, N, attr, values):
    self._Q.put( ('extendNode', (N, attr, values)) )
  def delAttr(self, N, attr):
    self._Q.put( ('delAttr', (N, attr)) )

class TestAll(unittest.TestCase):

  def setUp(self):
    self.backend=_NoBackend()   # Backend_SQLAlchemy('sqlite:///:memory:')
    self.db=NodeDB(backend=self.backend)

  def test01serialise(self):
    H = self.db.newNode('HOST', 'foo')
    for value in 1, 'str1', ':str2', '::', H:
      sys.stderr.flush()
      s = self.backend.serialise(value)
      sys.stderr.flush()
      assert type(s) is str
      self.assert_(value == self.backend.deserialise(s))

  def test10newNode(self):
    H = self.db.newNode('HOST', 'foo')
    self.assertEqual(len(H.ATTR1s), len(()) )
    self.assertRaises(AttributeError, getattr, H, 'ATTR2')
    H2 = self.db['HOST:foo']
    self.assert_(H is H2, "made HOST:foo, but retrieving it got a different object")

  def test11setAttrs(self):
    H = self.db.newNode('HOST', 'foo')
    H.Xs = [1,2,3,4,5]

if __name__ == '__main__':
  unittest.main()
