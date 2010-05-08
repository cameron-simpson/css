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
from thread import allocate_lock
import unittest
from cs.lex import str1
from cs.misc import the
from cs.mappings import parseUC_sAttr
from cs.logutils import Pfx, error, warn, info

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

class _AttrList(list):
  
  def __init__(self, node, key):
    ''' Initialise an _AttrList, a list subtype that understands Nodes
        and .ATTR[s] attribute access and drives a backend.
        `node` is the node to which this _AttrList is attached.
        `key` is the _singular_ form of the attribute name.

        TODO: we currently do not rely on the backend to preserve ordering so
              lots of operations just ask the backend to totally resave the
              attribute list.
    '''
    list.__init__(self)
    self.node = node
    self.key = key
    self.nodedb = node.nodedb

  def _detach(self, noBackend=False):
    assert False, "SURPRISE! call to _detach()"
    assert self.node is not None, "_detach() of unattached _AttrList: %s" % (self,)
    if not noBackend:
      N = self.node
      self.nodedb._backend.delAttr(N, self.key)
    self.node = None

  def __delitem__(self, *args):
    value = list.__delitem__(self, *args)
    self.nodedb._backend.saveAttrs(self)
    return value

  def __delslice__(self, *args):
    value = list.__delslice__(self, *args)
    self.nodedb._backend.saveAttrs(self)
    return value

  def __iadd__(self, *args):
    value = list.__iadd__(self, *args)
    self.nodedb._backend.saveAttrs(self)
    return value

  def __imul__(self, *args):
    value = list.__imul__(self, *args)
    self.nodedb._backend.saveAttrs(self)
    return value

  def __setitem__(self, *args):
    value = list.__setitem__(self, *args)
    self.nodedb._backend.saveAttrs(self)
    return value

  def __setslice__(self, *args):
    value = list.__setslice__(self, *args)
    self.nodedb._backend.saveAttrs(self)
    return value

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

  def insert(self, *args):
    value = list.insert(self, *args)
    self.nodedb._backend.saveAttrs(self)
    return value

  def pop(self, *args):
    value = list.pop(self, *args)
    self.nodedb._backend.saveAttrs(self)
    return value

  def remove(self, *args):
    value = list.remove(self, *args)
    self.nodedb._backend.saveAttrs(self)
    return value

  def reverse(self, *args):
    value = list.reverse(self, *args)
    if self:
      self.nodedb._backend.saveAttrs(self)
    return value

  def sort(self, *args):
    value = list.sort(self, *args)
    if self:
      self.nodedb._backend.saveAttrs(self)
    return value

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
    raise AttributeError, str(self)+'.'+repr(attr)

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
      row = _AttrList(self, ks)
      dict.__setitem__(self, ks, row)
    else:
      row = dict.__getitem__(self, ks)
    if plural:
      return row
    if len(row) == 1:
      return row[0]
    return None

  def get(self, item, default=None):
    try:
      return self[item]
    except KeyError:
      return default

  def __getitem__(self, item):
    if item in self:
      # fast track direct access to plural members
      return dict.__getitem__(self, item)
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
        raise AttributeError, str(self)+'.'+repr(attr)
      return value
    raise AttributeError, str(self)+'.'+repr(attr)

  def __setattr__(self, attr, value):
    k, plural = parseUC_sAttr(attr)
    if k is None:
      dict.__setattr__(self, attr, value)
    else:
      self[attr] = value

  def parentsByAttr(self, attr, t=None):
    ''' Return all "parent" Nodes P where P."attr"s contains self.
        If `t` is supplied and not None, select only parents of type `t`.
    '''
    # TODO: make this efficient - it's currently brute force
    k, plural = parseUC_sAttr(attr)
    assert k and not plural, "bad attribute name \"%s\"" % (attr,)
    ks = k + 's'
    if t:
      Ps = self.nodedb.nodesByType(t)
    else:
      Ps = self.nodedb.values()
    return [ P for P in Ps if self in P[ks] ]

  def gettoken(self, attr, valuetxt, createSubNodes=False):
    ''' Method to extract a token from the start of a string.
        It is intended to be overridden by subclasses to add recognition for
        domain specific things such as IP addresses.
    '''
    # "foo"
    m = re_STRING.match(valuetxt)
    if m:
      value = json.loads(m.group())
      return value, valuetxt[m.end():]

    # int
    m = re_INT.match(valuetxt)
    if m:
      value = int(m.group())
      return value, valuetxt[m.end():]

    # http://foo/bah etc
    m = re_BAREURL.match(valuetxt)
    if m:
      value = m.group()
      return value, valuetxt[m.end():]

    # TYPE:NAME
    m = re_NODEREF.match(valuetxt)
    if m:
      value = self.nodedb.nodeByTypeName(m.group(1),
                                         m.group(2),
                                         doCreate=createSubNodes)
      return value, valuetxt[m.end():]

    # NAME
    m = re_NAME.match(valuetxt)
    if m:
      if attr == "SUB"+self.type:
        value = self.nodedb.nodeByTypeName(self.type,
                                           m.group(),
                                           doCreate=createSubNodes)
      else:
        value = self.nodedb.nodeByTypeName(attr,
                                           m.group(),
                                           doCreate=createSubNodes)
      return value, valuetxt[m.end():]

    raise ValueError, "can't gettoken: %s" % (valuetxt,)

  def update(self, new_attrs, delete_missing=False):
    with Pfx("%s.update" % (self,)):
      # add new attributes
      new_attr_names = new_attrs.keys()
      new_attr_names.sort()
      for attr in new_attr_names:
        k, plural = parseUC_sAttr(attr)
        if not k:
          error("%s.applyAttrs: ignore non-ATTRs: %s" % (self, attr))
          continue
        ks = k+'s'
        if ks not in self:
          info("new .%s=%s" % (ks, new_attrs[attr]))
          self[ks] = new_attrs[attr]

      # change or possibly remove old attributes
      old_attr_names = self.keys()
      old_attr_names.sort()
      for attr in old_attr_names:
        k, plural = parseUC_sAttr(attr)
        if not k or not plural:
          info("%s.applyAttrs: ignore non-ATTRs old attr: %s" % (self, attr))
          continue
        if k.endswith('_ID'):
          error("%s.applyAttrs: ignoring bad old ATTR_ID: %s" % (self, attr))
          continue
        ks = k+'s'
        if ks in new_attrs:
          if self[ks] != new_attrs[ks]:
            info("set .%s=%s" % (ks, new_attrs[attr]))
            ##print >>sys.stderr, "OLD:"
            ##for v in self[ks]: print >>sys.stderr, repr(v)
            ##print >>sys.stderr, "NEW:"
            ##for v in new_attrs[attr]: print >>sys.stderr, repr(v)
            self[ks] = new_attrs[attr]
        elif delete_missing:
          info("del .%s=%s" % (ks, new_attrs[attr]))
          del self[ks]

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

  _key = ('_', '_')     # index of metadata node

  def __init__(self, backend=None, readonly=False):
    dict.__init__(self)
    if backend is None:
      backend = _NoBackend(self)
    self._backend = backend
    self.__nodesByType = {}
    backend.set_nodedb(self)
    self.readonly = readonly
    self._lock = allocate_lock()

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

  def nodeByTypeName(self, t, name, doCreate=False):
    if doCreate and (t, name) not in self:
      return self.newNode(t, name)
    return self[t, name]

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
    assert N.nodedb is self, "tried to store foreign Node: %s" % (repr(N),)
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
    assert (t, name) not in self, 'newNode(%s, %s): already exists' % (t, name)
    N = self[t, name] = self._createNode(t, name)
    return N

  @property
  def _(self):
    ''' Obtain the metadata node, creating it if necessary.
    '''
    key = NodeDB._key
    try:
      return self[key]
    except KeyError:
      return newNode(key)

  def seq(self):
    ''' Obtain a new sequence number for this NodeDB.
    '''
    N_ = self._
    with self._lock:
      i = N_.get('SEQ', 0)
      i += 1
      N_['SEQ'] = i
    return i

  def otherDB(self, dburl):
    ''' Take a database URL or sequence number and return:
          ( db, seq )
        where db is the NodeDB and seq is the sequence number
        in this NodeDB associated with the other NodeDB.
    '''
    N_ = self._
    if type(dburl) is str:
      db = NodeDBFromURL(dburl)
      for Ndb in N_.DBs:
        if Ndb.URL == dburl:
          # the dburl is known - return now
          return db, Ndb.SEQ
      # new URL - record it for posterity
      Ndb = newNode('_NODEDB', str(ndb))
      s = self.seq()
      Ndb.URL = dburl
      Ndb.SEQ = s
      return db, s

    # presumably we were handed an int, the db sequence number
    s = dburl
    for Ndb in N_.DBs:
      if Ndb.SEQ == s:
        return NodeDBFromURL(Ndb.URL)
    raise ValueError, "unknown DB sequence number: %s; _.DBs = %s" % (s, N_.DBs)

_NodeDBsByURL = {}

def NodeDBFromURL(url, readonly=False):
  ''' Factory method to return singleton NodeDB instances.
  '''
  if url in _NodeDBsByURL:
    return _NodeDBsByURL[url]

  if url.startswith('/'):
    # filesystem pathname
    # recognise some extensions and recurse
    # otherwise reject
    base = os.path.basename(url)
    _, ext = os.path.splitext(base)
    if ext == '.tch':
      return NodeDBFromURL('file-tch://'+url, readonly=readonly)
    if ext == '.sqlite':
      return NodeDBFromURL('sqlite://'+url, readonly=readonly)
    import sys
    print >>sys.stderr, "ext =", ext
    raise ValueError, "unsupported NodeDB URL: "+url

  if url.startswith('file-tch://'):
    from cs.nodedb.tokcab import Backend_TokyoCabinet
    dbpath = url[11:]
    backend = Backend_TokyoCabinet(dbpath, readonly=readonly)
    db = NodeDB(backend, readonly=readonly)
    _NodeDBsByURL[url] = db
    return db

  if url.startswith('sqlite:') or url.startswith('mysql:'):
    # TODO: direct sqlite support, skipping SQLAlchemy?
    from cs.nodedb.sqla import Backend_SQLAlchemy
    dbpath = url
    backend = Backend_SQLAlchemy(dbpath, readonly=readonly)
    db = NodeDB(backend, readonly=readonly)
    _NodeDBsByURL[url] = db
    db.url = url
    return db

  raise ValueError, "unsupported NodeDB URL: "+url

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
      if value.nodedb is self.nodedb:
        return ":%s:%s" % (value.type, value.name)
      odb, s = self.nodedb.otherDB(value.nodedb.url)
      return ":+%d:%s:%s" % (s, value.type, value.name)
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
    if v[0] == '+':
      # :+seq:TYPE:NAME
      # obtain foreign Node from other NodeDB
      s, t, name = v[1:].split(':', 2)
      return self.otherDB(int(s))[t, name]
    raise ValueError, "unparsable value \"%s\"" % (value,)

  def newNode(self, N):
    raise NotImplementedError

  def delNode(self, N):
    raise NotImplementedError

  def saveAttrs(self, attrs):
    ''' Save the full contents of this attribute list.
    '''
    N = attrs.node
    attr = attrs.key
    self.delAttr(N, attr)
    if attrs:
      self.extendAttr(N, attr, attrs)

  def extendAttr(self, N, attr, values):
    ''' Append values to the named attribute.
    '''
    raise NotImplementedError

  def delAttr(self, N, attr):
    ''' Remove all values from the named attribute.
    '''
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
