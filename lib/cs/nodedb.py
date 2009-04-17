#!/usr/bin/python -tt
#
# A database of nodes with attributes.
#       - Cameron Simpson <cs@zip.com.au> 25dec2008
#

from __future__ import with_statement
from cs.mappings import isUC_, parseUC_sAttr
import cs.sh
from cs.misc import the, uniq
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
import os
import unittest
import json
import re

# regexp to match TYPE:name with optional #id suffix
re_NODEREF = re.compile(r'([A-Z]+):([^:#]+)(#([0-9]+))?')
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
               Column('VALUE', String(1024)),
              )

def fieldInValues(column, values):
  # return an SQLAlchemy condition that tests a column
  # for integral values
  assert len(values) > 0
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

class AttrMap(dict):
  ''' A dictionary to manage node attributes.
      It applies changes to the db and then mirrors them in the in-memory dict.
  '''
  def __init__(self,node,nodedb,attrs):
    ''' Initialise an AttrMap, a mapping from attribute name to attribute
        names to a list of attribute values.
        We are supplied:
          node, the enclosing Node object
          nodedb, the associated NodeDB object
          attrs, a sequence of sqlalchemy row objects in the ATTRS table
        We keep the matching list of ATTRS row objects so that changes to the
        attributes can be effected in the database.
        There is one complication in the mix: 
          For attribute values named FOO_ID, we keep the values indexed
          under FOO, as direct Node links.
    '''
    dict.__init__(self)
    self.__node = node
    self.__nodedb = nodedb

    # prefetch any Nodes with a single query
    idlist = []
    for A in attrs:
      if A.ATTR.endswith('_ID'):
        idlist.append(int(A.VALUE))
    idmap = dict( [ (N.ID, N) for N in nodedb.nodesByIds(idlist) ] )

    # collate the attrs by ATTR value
    values_map = {}
    attrobjs_map = {}
    for A in attrs:
      attr = A.ATTR
      attrobjs_map.setdefault(attr, []).append(A)
      if attr.endswith('_ID'):
        values_map.setdefault(attr[:-3],[]).append(idmap[int(A.VALUE)])
      else:
        values_map.setdefault(attr,[]).append(A.VALUE)
    dict.__init__(self, values_map.items())
    self.__attrObjs = dict(attrobjs_map.items())

  def __delobjs(self, attr):
    objs = self.__attrObjs
    for attrObj in objs[attr]:
      self.__nodedb.session.delete(attrObj)
    del objs[attr]

  def __contains__(self, attr):
    if attr.endswith('_ID'):
      return attr[:-3] in self
    return dict.__contains__(self, attr)

  def __getitem__(self, attr):
    ''' Get the values stored for 'attr'.
    '''
    if attr.endswith('_ID'):
      vs = [ v.ID for v in self[attr[:-3]] ]
    else:
      vs = dict.__getitem__(self,attr)
    return vs

  def __setitem__(self, attr, values):
    ''' Set the attribute 'attr' to 'values'.
    '''
    if attr.endswith('_ID'):
      # assign list of ints to FOO_ID
      self[attr[:-3]] = [ self.__nodedb.nodeById(ID) for ID in values ]
      return

    objs = self.__attrObjs
    idattr = attr+'_ID'
    if idattr in objs:
      assert attr not in self, "both %s and %s exist" % (attr, objattr)
      self.__delobjs(idattr)
    elif attr in objs:
      self.__delobjs(attr)

    if len(values) == 0:
      return

    if hasattr(values[0], 'ID'):
      objattr = idattr
      newobjs = [ self.__node.newattr(objattr, v.ID) for v in values ]
    else:
      objattr = attr
      newobjs = [ self.__node.newattr(attr, v) for v in values ]

    self.__nodedb.session.add_all(newobjs)
    self.__attrObjs[objattr] = newobjs

    # store a tuple so that we can't do append()s etc
    dict.__setitem__(self, attr, tuple(values))

  def __delitem__(self, attr):
    ''' Remove the named attibute.
    '''
    self.__setitem__(attr, ())

class Node(object):
  ''' A node in the node db.
      A node has the following attributes:
        ID, the db id
        NAME, the node name
        TYPE, the node type
      Other uppercase attributes are node attributes.
  '''

  def __init__(self,_node,nodedb,attrs=None):
    ''' Initialise a new Node.
        We are supplied:
          _node, an sqlalchemy row object in the NODES table
          nodedb, the NodeDB with which this Node is associated
          attrs, a sequence of sqlalchemy row objects in the ATTRS table
                 This may be None, in which case the setup is deferred.
    '''
    self.__dict__['_node'] = _node
    if attrs is not None:
      self.__dict__['_attrs'] = AttrMap(_node,nodedb,attrs)
    self.__dict__['_nodedb'] = nodedb
    id=_node.ID
    if id in nodedb.nodeMap:
        print >>sys.stderr, "WARNING: Node.__init__: replacing %s with %s" % (nodedb.nodeMap[id],self)
    nodedb.nodeMap[id]=self

  def __load_attrs(self):
    nodedb = self._nodedb
    attrs = nodedb.session.query(nodedb._Attr).filter(nodedb._Attr.NODE_ID == self.ID).all()
    nodedb.session.add_all(attrs)
    _attrs = self.__dict__['_attrs'] = AttrMap(self._node, nodedb, attrs)
    return _attrs
  def __eq__(self,other):
    return self.ID == other.ID
  def __str__(self):
    return "%s:%s" % (self.TYPE, self.NAME)
  def __repr__(self):
    return "%s:%s#%d" % (self.TYPE, self.NAME, self.ID)

  def __hasattr__(self,attr):
    k, plural = parseUC_sAttr(attr)
    if k is None:
      return False
    if k in ('ID', 'NAME', 'TYPE'):
      return not plural
    if k.endswith('_ID'):
      return k[:-3] in self._attrs
    return k in self._attrs

  def __getattr__(self,attr):
    sys.stderr.flush()
    # fetch _attrs on demand
    if attr == '_attrs':
      return self.__load_attrs()
    k, plural = parseUC_sAttr(attr)
    assert k is not None, "no attribute \"%s\"" % (attr,)
    if k in ('ID','TYPE','NAME'):
      assert not plural, "can't pluralise .%s" % (k,)
      return getattr(self._node, k)
    if k not in self._attrs:
      values=()
    else:
      values=self._attrs[k]
    if plural:
      return tuple(values)
    if len(values) != 1:
      raise IndexError, "k=%s, plural=%s, values=%s" % (k,plural,values)
    return the(values)

  def __setattr__(self,attr,value):
    k, plural = parseUC_sAttr(attr)
    assert k is not None and k != 'ID', "refusing to set .%s" % (k,)
    if k == 'NAME':
      assert not plural
      self._node.NAME=value
    elif attr == 'TYPE':
      assert not plural
      self._node.TYPE=value
    else:
      if not plural:
        value=(value,)
      self._attrs[k]=value

  def __delattr__(self,attr):
    k, plural = parseUC_sAttr(attr)
    assert k is not None and k not in ('ID', 'TYPE', 'NAME')
    del self._attrs[k]

  def keys(self):
    ''' Node attribute names, excluding ID, NAME, TYPE.
    '''
    return self._attrs.keys()

  def get(self, attr, dflt):
    return self._attrs.get(attr, dflt)

  def parentsByAttr(self, attr, type=None):
    ''' Return parent Nodes whose .attr field mentions this Node.
        The optional parameter 'type' constrains the result to nodes
        of the specified TYPE.
    '''
    if not attr.endswith('_ID'):
      attr += '_ID'
    nodes = self._nodedb.nodesByAttrValue(attr, str(self.ID))
    if type is not None:
      nodes = [ N for N in nodes if N.TYPE == type ]
    return nodes

  def textdump(self, ofp):
    ofp.write("# %s\n" % (repr(self),))
    attrnames = self._attrs.keys()
    attrnames.sort()
    opattr = None
    for attr in attrnames:
      pattr = attr
      for value in self._attrs[attr]:
        if hasattr(value,'ID'):
          pvalue=str(value)
        else:
          pvalue = json.dumps(value)
        if opattr is not None and opattr == pattr:
          pattr = ''
        else:
          opattr = pattr
        ofp.write('%-15s %s\n' % (pattr, pvalue))

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
      ovalue = value
      if attr.endswith('_ID'):
        # node.ID
        value = self._nodedb.nodeById(int(ovalue))
        attr = attr[:-3]
      else:
        m = re_NODEREF.match(ovalue)
        if m is not None:
          # node ref
          node_key = ":".join( (m.group(1), m.group(2)) )
          value = self._nodedb.nodeByNameAndType(m.group(2), m.group(1), doCreate=createSubNodes)
        else:
          m = re_NAMELIST.match(ovalue)
          if m is not None:
            # name list
            if attr == ("SUB%s"%self.TYPE):
              for name in re_COMMASEP.split(ovalue):
                value = self._nodedb.nodeByNameAndType(name, self.TYPE, doCreate=createSubNodes)
                attrs.setdefault(attr, []).append(value)
            else:
              for name in re_COMMASEP.split(ovalue):
                value = self._nodedb.nodeByNameAndType(name,attr,doCreate=createSubNodes)
                attrs.setdefault(attr, []).append(value)
            continue
          try:
            value = json.loads(ovalue)
          except ValueError, e:
            value = ovalue
          t = type(value)
          if t is int:
            value = str(value)
          elif type(value) not in StringTypes:
            raise ValueError, "%s: %s: forbidden JSON expression (not int or str): %s" % (str(ifp), attr, value)

      attrs.setdefault(attr, []).append(value)

    oldattrnames = self._attrs.keys()
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
    T = tempfile.NamedTemporaryFile(delete=False)
    self.textdump(T)
    T.close()
    qname = cs.sh.quotestr(T.name)
    os.system("%s %s" % (editor, qname))
    with closing(open(T.name)) as ifp:
      self.textload(ifp, createSubNodes=createSubNodes)
    os.remove(T.name)

# TODO: make __enter__/__exit__ for running a session?
class NodeDB(object):
  def __init__(self, engine, nodes=None, attrs=None):
    if nodes is None:
      nodes = 'NODES'
    if attrs is None:
      attrs = 'ATTRS'
    if type(engine) is str:
      engine = create_engine(engine, echo=len(os.environ.get('DEBUG','')) > 0)
    metadata=MetaData()
    if type(nodes) is str:
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
    self.nodeMap=WeakValueDictionary()
    class _Attr(object):
      ''' An object mapper class for a single attribute row.
      '''
      def __init__(self,node_id,attr,value):
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
      def __init__(self,name,type):
        self.NAME=name
        self.TYPE=type
      def newattr(self,attr,value):
        return _Attr(self.ID,attr,value)
    mapper(_Node, nodes)
    self._Node=_Node

  def commit(self):
    self.session.commit()

  def _newNode(self,_node,attrs=None):
    return Node(_node,self,attrs)

  def createNode(self,name,type,attrs=None):
    ''' Create a new Node in the database.
    '''
    assert (name, type) not in self, "node %s:%s already exists" % (type, name)

    attrlist=[]
    if attrs is not None:
      for k, vs in attrs.items():
        if isUC_(k):
          attrlist.append( (k, [v for v in vs]) )
        else:
          raise ValueError, "invalid key \"%s\"" % k

    _node=self._Node(name,type)
    self.session.add(_node)
    self.session.flush()
    assert _node.ID is not None

    N=self._newNode(_node)
    for k, vs in attrlist:
      setattr(N,k+"s",vs)

    return N

  def _nodesByIds(self,ids):
    ''' Take some NODES.ID values and return _Node objects.
    '''
    filter=fieldInValues(self.nodes.c.ID,ids)
    _nodes=self.session.query(self._Node).filter(filter).all()
    self.session.add_all(_nodes)
    return _nodes

  def _nodesByNameAndType(self,name,type):
    _nodes=self.session.query(self._Node).filter_by(NAME=name,TYPE=type).all()
    self.session.add_all(_nodes)
    return _nodes

  def _toNode(self,key):
    ''' Flexible get-a-node function.
        int -> nodeById(int)
        "#id" -> nodeById(int("id"))
        (name,type) -> nodeByNameAndType(name,type)
        "TYPE:name" -> nodeByNameAndType(name,type)
    '''
    if type(key) is int:
      return self.nodeById(key)
    if type(key) in StringTypes:
      if key[0] == '#':
        return self.nodeById(int(key[1:]))
      if key[0].isupper():
        t, n = key.split(':',1)
        return self.nodeByNameAndType(n, t)
    elif len(key) == 2:
      return self.nodeByNameAndType(*key)
    raise ValueError, "can't map %s to Node" % (key,)

  def _nodesByType(self,type):
    _nodes=self.session.query(self._Node).filter_by(TYPE=type).all()
    self.session.add_all(_nodes)
    return _nodes

  def _nodes2Nodes(self, _nodes, checkMap):
    ''' Take some _Node objects and return Nodes.
    '''
    ids=list(N.ID for N in _nodes)
    if checkMap:
      nodeMap=self.nodeMap
      # load of the Nodes we already know about
      Ns=[ nodeMap[id] for id in ids if id in nodeMap ]
      # get the NODES.ID values of the nodes not yet cached
      missingIds=list(id for id in ids if id not in nodeMap)
    else:
      Ns=[]
      missingIds=ids

    if len(missingIds) > 0:
      Ns.extend([ self._newNode(_node)
                  for _node in self._nodesByIds(missingIds)
                ])

    return Ns

  def nodesByIds(self,ids):
    ''' Return the Nodes from the corresponding list if Node.ID values.
        Note: the returned nodes may not be in the same order as the ids.
    '''
    nodeMap=self.nodeMap
    Ns=list(nodeMap[id] for id in ids if id in nodeMap)
    missingIds=list(id for id in ids if id not in nodeMap)
    if len(missingIds) > 0:
      Ns.extend(self._nodes2Nodes(self._nodesByIds(missingIds), checkMap=False))
    return Ns

  def nodeById(self,id):
    return the(self.nodesByIds((id,)))

  def nodeByNameAndType(self,name,type,doCreate=False):
    ''' Return the node of the specified name and type.
        The optional parameter doCreate defaults to False.
        If there is no such node and doCreate is None, return None.
        If there is no such node and doCreate is true, create the node and return it.
        Otherwise raise IndexError.
    '''
    nodes = self._nodes2Nodes(self._nodesByNameAndType(name,type),checkMap=True)
    if len(nodes) > 0:
      return the(nodes)
    if doCreate is None:
      return None
    if doCreate:
      return self.createNode(name, type)
    raise IndexError, "no node matching NAME=%s and TYPE=%s" % (name, type)

  def nodesByType(self,type):
    return self._nodes2Nodes(self._nodesByType(type), checkMap=True)

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

  def testAssignNode(self):
    db=self.db
    self.host1.HOST=self.host2
    self.assertEqual(self.host1.HOST_ID, self.host2.ID)
    self.assertEqual(self.host1.HOST.ID, self.host2.ID)

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

  def _dont_testTextDump(self):
    self.host1.SUBHOST=self.host2
    self.host1.textdump(sys.stdout)

  def _dont_testTextEdit(self):
    self.host1.SUBHOSTs=(self.host2, self.host1)
    self.host1.edit()
    self.host1.textdump(sys.stdout)

if __name__ == '__main__':
  print 'SQLAlchemy version =', sqlalchemy.__version__
  unittest.main()
