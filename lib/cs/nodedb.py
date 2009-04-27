#!/usr/bin/python -tt
#
# A database of nodes with attributes.
#       - Cameron Simpson <cs@zip.com.au> 25dec2008
#

from __future__ import with_statement
from cs.mappings import isUC_, parseUC_sAttr
import cs.sh
from cs.misc import the, uniq, seq
import sqlalchemy
from sqlalchemy import create_engine, \
                       MetaData, Table, Column, Index, Integer, String, \
                       select
from sqlalchemy.orm import mapper, sessionmaker
from sqlalchemy.sql import and_, or_, not_
from weakref import WeakValueDictionary
from contextlib import closing
from types import StringTypes
import tempfile
import sys
if sys.hexversion < 0x02060000:
  from sets import Set as set
  import simplejson as json
else:
  import json
import os
import unittest
import re

INVALID = "INVALID"     # sentinel, distinct from None

# regexp to match TYPE:name with optional #id suffix
re_NODEREF = re.compile(r'([A-Z]+):([^:#]+)(#([0-9]+))?')
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

def NODESTable(metadata, name=None):
  if name is None:
    name='NODES'
  return Table(name, metadata,
               Column('ID', Integer, primary_key=True, nullable=False),
               Column('NAME', String(64)),
               Column('TYPE', String(64), nullable=False),
              )

def ATTRSTable(metadata, name=None):
  if name is None:
    name='ATTRS'
  return Table(name, metadata,
               Column('ID', Integer, primary_key=True, nullable=False),
               Column('NODE_ID', Integer, nullable=False, index=True),
               Column('ATTR', String(64), nullable=False, index=True),
               # mysql max index key len is 1000, so VALUE only 900
               Column('VALUE', String(900), index=True),
              )

def columnInValues(column, values):
  # return an SQLAlchemy condition that tests a column
  # for integral values
  assert len(values) > 0, "columnInValues(%s, %s) with no values" % (column, values)
  if not isinstance(values,list):
    values = values[:]

  # trivial case with one value
  if len(values) == 1:
    return column == values[0]

  vmin = min(values)
  vmax = max(values)
  cond = column.between(vmin, vmax)

  # maybe it is a contiguous range
  # if so, we can return just the between clause
  if vmax-vmin+1 == len(values):
    contiguous = True
    values.sort()
    for i in xrange(len(values)):
      if values[i] != vmin+i:
        contiguous = False
        break
    if contiguous:
      return cond

  # the complicated case: select by range and check membership
  cond = and_(cond, "%s in (%s)" % (column, ",".join(str(v) for v in values)))
  return cond

class Attr(object):
  def __init__(self, N, name, value, _A=None):
    ''' Instantiate a new attribute with optional backing store.
    '''
    self.node = N
    self.NAME = name
    self.VALUE = value
    self.__set_attr(_A)

  def __str__(self):
    return "Attr:%s=%s" % (self.NAME, self.VALUE)

  def __set_attr(self, _A):
    self._attr = _A
    ##self.node.nodedb._setAttrBy_Attr(self, _A)
    if _A is not None:
      # record mapping from _Attr to Attr
      self.node.nodedb._note_Attr(_A, self)

  def _changed(self):
    self.node._changed()

  def __setattr__(self, attr, value):
    if attr == 'NAME' or attr == 'VALUE':
      self._changed()
    self.__dict__[attr]=value

  def apply(self):
    ''' Apply pending changes to the backend.
        Does not imply a commit to the database.
    '''
    value = self.VALUE
    _attr = self._attr
    assert _attr is not INVALID, "apply called on discarded Attr(%s)" % (self,)
    if hasattr(value, 'ID'):
      if _attr is None:
        assert value.ID is not None
        _A = self.node.nodedb._Attr(self.node.ID, self.NAME+'_ID', str(value.ID))
        self.node.nodedb.session.add(_A)
        self.__set_attr(_A)
      else:
        assert _attr.NAME == self.NAME+'_ID'
        if _attr.VALUE != value.ID:
          _attr.VALUE = value.ID
    else:
      if _attr is None:
        _A = self.node.nodedb._Attr(self.node.ID, self.NAME, str(value))
        self.node.nodedb.session.add(_A)
        self.__set_attr(_A)
      else:
        if _attr.VALUE != value:
          _attr.VALUE = value

  def discard(self):
    ''' Mark this attribute as invalid.
    '''
    assert self._attr is not INVALID, "repeated call to discard(%s)" % (self,)
    if self._attr is not None:
      self.node.nodedb.session.delete(self._attr)
    self._attr = INVALID

class AttrMap(dict):
  def __init__(self, node, preattrs=None):
    self.__node = node
    dict.__init__(self)
    if preattrs is not None:
      for attr, values in preattrs.items():
        dict.__setitem__(self, attr, values)

  def __setitem__(self, attr, values):
    if attr in self:
      for A in dict.__getitem__(self, attr):
        A.discard()
    dict.__setitem__(self, attr,
                     [ Attr(self.__node, attr, v) for v in values ]
                    )

  def __getitem__(self, attr):
    return [ A.VALUE for A in dict.__getitem__(self, attr) ]

class Node(object):
  ''' A node in the node db.
      A node has the following attributes:
        ID, the db id - made at need; try to avoid using it
        NAME, the node name
        TYPE, the node type
      Other uppercase attributes are node attributes.
  '''

  def __init__(self, nodedb, name, nodetype, _node=None):
    ''' Initialise a new Node.
        We are supplied:
          _node, an sqlalchemy row object in the NODES table
          nodedb, the NodeDB with which this Node is associated
          attrs, a sequence of sqlalchemy row objects in the ATTRS table
                 This may be None, in which case the setup is deferred.
    '''
    self.__dict__['nodedb'] = nodedb
    self.__dict__['NAME'] = name
    self.__dict__['TYPE'] = nodetype
    self.__set_node(_node)
    self.nodedb._noteNodeNameAndType(self)

  def apply(self):
    ''' Apply pending changes to the database.
        Does not do a database commit.
    '''
    _node = self._node
    if _node is None:
      _node = self.__load_node()
    else:
      if _node.NAME != self.NAME:
        _node.NAME = self.NAME
      if _node.TYPE != self.TYPE:
        _node.TYPE = self.TYPE
    for attr, values in self.attrs.items():
      for v in values:
        v.apply()
    self.nodedb._applied(self)

  def _changed(self):
    ''' Record this node as needing sync to backing store.
    '''
    self.nodedb._changed(self)

  def __set_node(self, _node):
    if _node is None:
      assert '_node' not in self.__dict__, \
                "_node already set: %s" % (self.__dict__['_node'],)
      self.__dict__['_node'] = None
      self.nodedb._changed(self)
    else:
      assert '_node' not in self.__dict__ or self.__dict__['_node'] is None, \
                "_node already set: %s" % (self.__dict__['_node'],)
      assert self.NAME == _node.NAME and self.TYPE == _node.TYPE
      self.__dict__['_node'] = _node
      id = _node.ID
      self.__dict__['ID'] = id
      self.nodedb._noteNodeID(self)

  def __load_node(self):
    _node = self.nodedb._Node(self.NAME, self.TYPE)
    self.nodedb.session.add(_node)
    self.nodedb.session.flush()
    assert _node.ID is not None
    self.__set_node(_node)
    return _node

  def __loadattrs(self):
    _node = self._node
    if _node is None:
      attrs = AttrMap(self)
    else:
      attrs = {}
      nodedb = self.nodedb
      _attrs = nodedb.session.query(nodedb._Attr) \
                             .filter(nodedb._Attr.NODE_ID == _node.ID) \
                             .all()
      nodedb.session.add_all(_attrs)
      for _A in _attrs:
        attr = nodedb.getAttrBy_Attr(self, _A, None)
        if attr is None:
          name = _A.ATTR
          value = _A.VALUE
          if name.endswith('_ID'):
            name_id = name
            name = name[:-3]
            value = nodedb.nodeById(int(value))
          attr = Attr(self, name, value, _A)
        attrs.setdefault(name,[]).append(attr)
      attrs = AttrMap(self, attrs)
    self.__dict__['attrs'] = attrs
    return attrs

  def __eq__(self,other):
    return self.NAME == other.NAME \
       and self.TYPE == other.TYPE \
       and self.attrs == other.attrs
  def __str__(self):
    return "%s:%s" % (self.TYPE, self.NAME)
  def __repr__(self):
    return "%s:%s#%s" % (self.TYPE, self.NAME, self.ID)

  def __hasattr__(self,attr):
    k, plural = parseUC_sAttr(attr)
    if k is None:
      return False
    if k in ('ID', 'NAME', 'TYPE'):
      return not plural
    if k.endswith('_ID'):
      return k[:-3] in self.attrs
    return k in self.attrs

  def __getattr__(self,attr):
    # fetch attrs on demand
    if attr == 'attrs':
      return self.__loadattrs()
    k, plural = parseUC_sAttr(attr)
    assert k is not None, "no attribute \"%s\"" % (attr,)
    if k in ('ID','TYPE','NAME'):
      assert not plural, "can't pluralise .%s" % (k,)
      if k == 'ID':
        _node = self._node
        if _node is None:
          _node = self.__load_node()
        return _node.ID
      assert False, "__getattr__(%s), but %s should exist!" % (attr, attr)
    if k not in self.attrs:
      values=()
    else:
      values=self.attrs[k]
    if plural:
      return tuple(values)
    if len(values) != 1:
      raise IndexError, "k=%s, plural=%s, values=%s" % (k,plural,values)
    return the(values)

  def __setattr__(self, attr, value):
    k, plural = parseUC_sAttr(attr)
    assert k is not None and k not in ('ID', 'TYPE'), \
                "refusing to set .%s" % (k,)
    if k == 'NAME':
      assert not plural
      name = self.NAME
      if name != value:
        nodetype = self.TYPE
        nodedb = self.nodedb
        assert value is None or (value, nodetype) not in nodedb, \
                "%s:%s already exists in nodedb" % (nodetype, value)
        self.__dict__['NAME'] = value
        self.nodedb._changeNodeNameAndType(self, name, nodetype)
        if self._node is not None:
          self._node.NAME = value
      return
    if not plural:
      value=(value,)
    self.attrs[k]=value

  def __delattr__(self,attr):
    k, plural = parseUC_sAttr(attr)
    assert k is not None and k not in ('ID', 'TYPE', 'NAME')
    del self.attrs[k]

  def keys(self):
    ''' Node attribute names, excluding ID, NAME, TYPE.
    '''
    return self.attrs.keys()

  def get(self, attr, dflt):
    return self.attrs.get(attr, dflt)

  def parentsByAttr(self, attr, nodetype=None):
    ''' Return parent Nodes whose .attr field mentions this Node.
        The optional parameter 'nodetype' constrains the result to nodes
        of the specified TYPE.
        WARNING: this implies a database commit.
    '''
    if not attr.endswith('_ID'):
      attr += '_ID'
    self.apply()      # should happen anyway
    self.nodedb.commit()
    nodes = self.nodedb.nodesByAttrValue(attr, str(self.ID))
    if nodetype is not None:
      nodes = [ N for N in nodes if N.TYPE == nodetype ]
    return nodes

  def textdump(self, ofp):
    ofp.write("# %s\n" % (repr(self),))
    attrnames = self.attrs.keys()
    attrnames.sort()
    opattr = None
    for attr in attrnames:
      pattr = attr
      for value in self.attrs[attr]:
        if hasattr(value,'ID'):
          if attr == "SUB"+self.TYPE and value.TYPE == self.TYPE:
            pvalue = value.NAME
          elif attr == value.TYPE:
            pvalue = value.NAME
          else:
            pvalue = str(value)
        else:
          m = re_BAREURL.match(value)
          if m is not None and m.end() == len(value):
            pvalue = value
          else:
            pvalue = json.dumps(value)
        if opattr is not None and opattr == pattr:
          pattr = ''
        else:
          opattr = pattr
        ofp.write('%-15s %s\n' % (pattr, pvalue))

  def gettoken(self, attr, valuetxt, createSubNodes=False):
    ''' Method to extract a token from the start of a string.
        It is intended to be subclassed to add recognition for
        such things as IP addresses, etc.
    '''
    m = re_STRING.match(valuetxt)
    if m is not None:
      value = json.loads(m.group())
      return value, valuetxt[m.end():]

    m = re_INT.match(valuetxt)
    if m is not None:
      value = int(m.group())
      return value, valuetxt[m.end():]

    m = re_BAREURL.match(valuetxt)
    if m is not None:
      value = m.group()
      return value, valuetxt[m.end():]

    m = re_NODEREF.match(valuetxt)
    if m is not None:
      value = self.nodedb.nodeByNameAndType(m.group(2),
                                             m.group(1),
                                             doCreate=createSubNodes)
      return value, valuetxt[m.end():]

    m = re_NAME.match(valuetxt)
    if m is not None:
      if attr == "SUB"+self.TYPE:
        value = self.nodedb.nodeByNameAndType(m.group(),
                                               self.TYPE,
                                               doCreate=createSubNodes)
      else:
        value = self.nodedb.nodeByNameAndType(m.group(),
                                               attr,
                                               doCreate=createSubNodes)
      return value, valuetxt[m.end():]

    raise ValueError, "can't tokenise: %s" % (valuetxt,)

  def tokenise(self, attr, valuetxt, createSubNodes=False):
    values = []
    valuetxt = valuetxt.strip()
    while len(valuetxt) > 0:
      value, valuetxt = self.gettoken(attr, valuetxt, createSubNodes=createSubNodes)
      values.append(value)
      valuetxt = valuetxt.lstrip()
      assert len(valuetxt) == 0 or valuetxt.startswith(','), \
        "expected comma, got \"%s\"" % (valuetxt,)
      if valuetxt.startswith(','):
        valuetxt = valuetxt[1:].lstrip()
      assert len(valuetxt) == 0 or not valuetxt.startswith(','), \
        "unexpected second comma at \"%s\"" % (valuetxt,)
    return values

  def assign(self, assignment, createSubNodes=False):
    ''' Take a string of the form ATTR=values and apply it.
    '''
    attr, valuetxt = assignment.split('=', 1)
    assert isUC_(attr), "invalid attribute name \"%s\"" % (attr, )
    values = self.tokenise(attr, valuetxt, createSubNodes=createSubNodes)
    setattr(self, attr+'s', values)

  def textload(self, ifp, createSubNodes=False):
    attrs = {}
    prev_attr = None
    for line in ifp:
      assert line[-1] == '\n', "%s: unexpected EOF" % (str(ifp),)
      line = line[:-1].rstrip()
      if len(line) == 0:
        continue
      ch1 = line[0]
      if ch1 == '#':
        continue
      if ch1.isspace():
        assert prev_attr is not None, "%s: unexpected indented line" % (str(ifp),)
        attr = prev_attr
        value = line.lstrip()
      else:
        attr, value = line.split(None, 1)
        prev_attr = attr
        assert isUC_(attr), "%s: invalid attribute name \"%s\"" % (str(ifp), attr)
      assert not attr.endswith('_ID'), "%s: invalid attribute name \"%s\" - foo_ID forbidden" % (str(ifp), attr)

      ovalue = value
      for value in self.tokenise(attr, ovalue, createSubNodes=createSubNodes):
        attrs.setdefault(attr, []).append(value)

    oldattrnames = self.attrs.keys()
    oldattrnames.sort()
    for attr in oldattrnames:
      if attr.endswith('_ID'):
        attr = attr[:-3]
      attrs.setdefault(attr, ())
    attrnames = attrs.keys()
    attrnames.sort()
    for attr in attrnames:
      plattr = attr+'s'
      value = tuple(attrs[attr])
      ovalue = getattr(self, plattr)
      if ovalue != value:
        setattr(self, plattr, value)

  def edit(self, editor=None, createSubNodes=False):
    if editor is None:
      editor = os.environ.get('EDITOR', 'vi')
    if sys.hexversion < 0x02060000:
      T = tempfile.NamedTemporaryFile()
    else:
      T = tempfile.NamedTemporaryFile(delete=False)
    self.textdump(T)
    T.flush()
    qname = cs.sh.quotestr(T.name)
    os.system("%s %s" % (editor, qname))
    with closing(open(T.name)) as ifp:
      self.textload(ifp, createSubNodes=createSubNodes)
    os.remove(T.name)
    T.close()

# TODO: make __enter__/__exit__ for running a session?
class NodeDB(object):
  def __init__(self, engine, nodes=None, attrs=None):
    if nodes is None:
      nodes = 'NODES'
    if attrs is None:
      attrs = 'ATTRS'
    if type(engine) in StringTypes:
      engine = create_engine(engine, echo=len(os.environ.get('DEBUG','')) > 0)
    metadata=MetaData()
    if type(nodes) in StringTypes:
      nodes=NODESTable(metadata,name=nodes)
    self.nodes=nodes
    Index('nametype', nodes.c.NAME, nodes.c.TYPE)
    if type(attrs) is str:
      attrs=ATTRSTable(metadata,name=attrs)
    self.attrs=attrs
    Index('attrvalue', attrs.c.ATTR, attrs.c.VALUE)
    self.engine=engine
    self.conn=engine.connect()
    metadata.create_all(engine)
    self.__Session=sessionmaker(bind=engine)
    session=self.session=self.__Session()
    self._nodeMap=WeakValueDictionary()
    self._attrMap=WeakValueDictionary()
    self._changedNodes = set()
    class _Attr(object):
      ''' An object mapper class for a single attribute row.
      '''
      def __init__(self, node_id, attr, value):
        self.NODE_ID=node_id
        self.ATTR=attr
        self.VALUE=value
      def __str__(self):
        return "_Attr:{NODE_ID: %s, ATTR: %s, VALUE: %s}" \
               % (self.NODE_ID, self.ATTR, self.VALUE)
      __repr__=__str__
    mapper(_Attr, attrs)
    self._Attr=_Attr
    class _Node(object):
      ''' An object mapper class for a node.
      '''
      def __init__(self,name,nodetype):
        self.NAME=name
        self.TYPE=nodetype
      def newattr(self,attr,value):
        return _Attr(self.ID,attr,value)
    mapper(_Node, nodes)
    self._Node=_Node

  def _note_Attr(self, _A, attr):
    assert _A.ID not in self._attrMap
    self._attrMap[_A.ID] = attr

  def getAttrBy_Attr(self, N, _A, doCreate=False):
    if _A.ID not in self._attrMap:
      if doCreate is None:
        return doCreate
      if doCreate:
        attr = Attr(N, _A.NAME, _A.VALUE, _A)
    return self._attrMap[_A.ID]

  def _noteNodeID(self, N):
    ''' Record the database NODE_ID->Node mapping in the nodeMap.
    '''
    id = N.ID
    nodeMap = self._nodeMap
    assert id not in nodeMap
    nodeMap[id] = N

  def _noteNodeNameAndType(self, N):
    name, nodetype = N.NAME, N.TYPE
    if name is not None:
      nodeMap = self._nodeMap
      assert (name, nodetype) not in nodeMap
      nodeMap[name, nodetype] = N

  def _changeNodeNameAndType(self, N, oldName, oldNodeType):
    if oldName is not None:
      del self._nodeMap[oldName, oldNodeType]
    self._noteNodeNameAndType(N)

  def _changed(self, node):
    ''' Record a Node as needing updates to the backing store.
    '''
    self._changedNodes.add(node)

  def _applied(self, N):
    ''' Record a Node as no longer needing updates to the backing store.
    '''
    self._changedNodes.discard(N)

  def apply(self):
    ''' Apply all outstanding updates.
        Does not imply a database commit.
    '''
    for N in self._changedNodes.copy():
      N.apply()

  def commit(self):
    ''' Apply all outstanding changes and do a database commit.
    '''
    self.apply()
    self.session.commit()

  def _newNode(self, name, nodetype, _node=None):
    ''' New Node factory method.
        Subclasses should override _newNode if they subclass Node.
    '''
    return Node(self, name, nodetype, _node=_node)

  def createNode(self, name, nodetype, attrs=None):
    ''' Create a new Node.
        Subclasses should override _newNode() if they subclass Node.
    '''
    assert name is None or (name, nodetype) not in self, \
                "node %s:%s already exists" % (nodetype, name)
    N=self._newNode(name, nodetype)
    # collate attributes
    attrlist=[]
    if attrs is not None:
      for k, vs in attrs.items():
        if isUC_(k):
          N.attrs[k] = vs
        else:
          raise ValueError, "invalid key \"%s\"" % k
    return N

  def _toNode(self, key):
    ''' Flexible get-a-node function.
        int -> nodeById(int)
        "#id" -> nodeById(int("id"))
        (name,nodetype) -> nodeByNameAndType(name,nodetype)
        "TYPE:name" -> nodeByNameAndType(name,nodetype)
    '''
    if type(key) is int:
      return self.nodeById(key)
    if type(key) in StringTypes:
      if key[0] == '#':
        return self.nodeById(int(key[1:]))
      if key[0].isupper():
        nodetype, name = key.split(':',1)
        return self.nodeByNameAndType(name, nodetype)
    elif len(key) == 2:
      return self.nodeByNameAndType(*key)
    raise ValueError, "can't map %s to Node" % (key,)

  def _nodesByIds(self, ids):
    ''' Take some NODES.ID values and return _Node objects.
    '''
    filter=columnInValues(self.nodes.c.ID, ids)
    _nodes=self.session.query(self._Node).filter(filter).all()
    self.session.add_all(_nodes)
    return _nodes

  def _nodesByNameAndType(self, name, nodetype):
    ''' Return database _Node objects.
    '''
    _nodes=self.session.query(self._Node).filter_by(NAME=name, TYPE=nodetype).all()
    self.session.add_all(_nodes)
    return _nodes

  def _nodesByType(self,nodetype):
    _nodes=self.session.query(self._Node).filter_by(TYPE=nodetype).all()
    self.session.add_all(_nodes)
    return _nodes

  def _node2Node(self, _node):
    ''' Match a _Node to a Node.
    '''
    nodeMap = self._nodeMap
    nodeid = _node.ID
    if nodeid in nodeMap:
      return nodeMap[nodeid]
    name, nodetype = _node.NAME, _node.TYPE
    if (name, nodetype) in nodeMap:
      N = nodeMap[name, nodetype]
      N._set_node(_node)
      return N
    return self._newNode(_node.NAME, _node.TYPE, _node)

  def _nodes2Nodes(self, _nodes):
    ''' A generator to take _Nodes and yield Nodes.
        Note that yielded Nodes may not be in the same order as the _Nodes.
    '''
    return [ self._node2Node(_node) for _node in _nodes ]

  def nodesByIds(self, ids):
    ''' Return the Nodes from the corresponding list if Node.ID values.
        Note: the returned nodes may not be in the same order as the ids.
    '''
    missingIds = []
    nodeMap=self._nodeMap
    for id in ids:
      if id in nodeMap:
        yield nodeMap[id]
      else:
        missingIds.append(id)
    if len(missingIds) > 0:
      self.apply()
      for _node in self._nodesByIds(missingIds):
        # we examine the cache because it can get populated by our caller
        # between yields
        yield self._node2Node(_node)

  def nodeById(self, id):
    return the(self.nodesByIds((id,)))

  def nodeByNameAndType(self, name, nodetype, doCreate=False):
    ''' Return the node of the specified name and nodetype.
        The optional parameter doCreate defaults to False.
        If there is no such node and doCreate is None, return None.
        If there is no such node and doCreate is true, create the node and return it.
        Otherwise raise IndexError.
    '''
    try:
      return self._nodeMap[name, nodetype]
    except KeyError:
      pass
    nodes = tuple(self._nodes2Nodes(self._nodesByNameAndType(name, nodetype)))
    if len(nodes) > 0:
      return the(nodes)
    if doCreate is None:
      return None
    if doCreate:
      return self.createNode(name, nodetype)
    raise IndexError, "no node matching NAME=%s and TYPE=%s" % (name, nodetype)

  def nodesByType(self, nodetype):
    return self._nodes2Nodes(self._nodesByType(nodetype))

  def nodesByAttrValue(self,attr,value):
    ''' Return nodes with an attribute value as specified.
    '''
    nodeids = [ A.NODE_ID
                for A in self.session.query(self._Attr)
                                     .filter_by(ATTR=attr, VALUE=value)
                                     .all()
              ]
    return self.nodesByIds(uniq(nodeids))

  def __contains__(self,key):
    try:
      N=self[key]
    except IndexError:
      return False
    return True

  def __getitem__(self,key):
    N=self._toNode(key)
    # should not happen, but the code tested for it :-(
    assert N is not None
    return N

class TestAll(unittest.TestCase):
  def setUp(self):
    db=self.db=NodeDB('sqlite:///:memory:')
    self.host1=db.createNode('host1','HOST')
    self.host2=db.createNode('host2','HOST')
    db.session.flush()

  def testAssign(self):
    db=self.db
    host1=self.host1
    host1.ATTR1=3
    self.assertEqual(host1.ATTR1s, (3,))
    host1.ATTR2s=[5,6,7]
    self.assertEqual(host1.ATTR2s, (5,6,7))
    self.assertRaises(IndexError, getattr, host1, 'ATTR2')

  def testTextAssign(self):
    self.host1.assign("IPADDR=\"172.10.0.1\"")
    self.assertEqual(self.host1.IPADDR, "172.10.0.1")
    self.host1.textdump(sys.stdout); sys.stdout.flush()

  def testAssignNode(self):
    db=self.db
    self.host1.HOST=self.host2
    assert self.host1.HOST is self.host2

  def testRename(self):
    db=self.db
    self.host1.NAME='newname'
    db.session.flush()
    self.assertEqual(db.nodeByNameAndType('newname','HOST').ID, self.host1.ID)

  def testToNode(self):
    db=self.db
    self.assertEqual(db._toNode(1).ID, 1)
    self.assertEqual(db._toNode("#1").ID, 1)
    self.assertEqual(db._toNode(("host2","HOST")).ID, 2)
    self.assertEqual(db._toNode("HOST:host2").ID, 2)

  def testParentsByAttr(self):
    self.host1.SUBHOST=self.host2
    parents=self.host2.parentsByAttr('SUBHOST')
    parents=list(parents)
    self.assertEqual(the(parents).ID, self.host1.ID)

  def testTextload(self):
    from StringIO import StringIO
    nic1 = self.db.createNode('nic1', 'NIC')
    nic2 = self.db.createNode('nic2', 'NIC')
    self.db.commit()
    H = self.host1
    text = StringIO("".join( (
              'SUBHOST        host1, host2\n',
              'NIC            nic1, nic2\n', ) ))
    H.textload(text)
    H.textdump(sys.stdout)

  def testTextDump(self):
    self.host1.SUBHOST=self.host2
    self.host1.textdump(sys.stdout)

  def _dont_testTextEdit(self):
    self.host1.SUBHOSTs=(self.host2, self.host1)
    self.host1.edit()
    self.host1.textdump(sys.stdout)

if __name__ == '__main__':
  print 'SQLAlchemy version =', sqlalchemy.__version__
  unittest.main()
