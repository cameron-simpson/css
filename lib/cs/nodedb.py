#!/usr/bin/python -tt
#
# A database of nodes with attributes.
#       - Cameron Simpson <cs@zip.com.au> 25dec2008
#

from cs.mappings import isUC_, parseUC_sAttr
import cs.sh
from cs.misc import the, uniq
import sqlalchemy
from sqlalchemy import create_engine, \
                       MetaData, Table, Column, Integer, String, \
                       select
from sqlalchemy.orm import mapper, sessionmaker
from sqlalchemy.sql import and_, or_, not_
from weakref import WeakValueDictionary
from contextlib import closing
import tempfile
import sys
import os
import unittest
import json
import re

re_NODEREF = re.compile(r'([A-Z]+):([^:#]+)(#([0-9]+))?')

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
               Column('NODE_ID', Integer, nullable=False),
               Column('ATTR', String(64), nullable=False),
               Column('VALUE', String(1024)),
              )

def fieldInValues(field,values):
  # return an SQLAlchemy condition that tests a field
  # for integral values
  assert len(values) > 0
  if not isinstance(values,list):
    values=values[:]

  # trivial case with one value
  if len(values) == 1:
    return field == values[0]

  vmin=min(values)
  vmax=max(values)
  cond="%s between %d and %d" % (field, vmin, vmax)

  # maybe it is a contiguous range
  # if so, we can return just the between clause
  if vmax-vmin+1 == len(values):
    contiguous=True
    values.sort()
    for i in xrange(len(values)):
      if values[i] != vmin+i:
        contiguous=False
        break
    if contiguous:
      return cond

  # the complicated case: select by range and check membership
  cond=and_(cond, "%s in (%s)" % (field, ",".join(str(v) for v in values)))
  return cond

class AttrMap(dict):
  ''' A dictionary to manage node attributes.
      It applies changes to the db and then mirrors them in the in-memory dict.
  '''
  def __init__(self,node,nodedb,attrs):
    ''' Initialise an AttrMap.
        We are supplied:
          node, the enclosing Node object
          nodedb, the associated NodeDB object
          attrs, a sequence of sqlalchemy row objects in the ATTRS table
        We must still collate the attrs into ATTR->(VALUE,...) pairs
        for storage in the dict.
    '''
    dict.__init__(self)
    self.__node=node
    self.__nodedb=nodedb
    # collate the attrs by ATTR value
    values_map={}
    attrobjs_map={}
    for A in attrs:
      values_map.setdefault(A.ATTR,[]).append(A.VALUE)
      attrobjs_map.setdefault(A.ATTR,[]).append(A)
    dict.__init__(self, values_map.items())
    self.__attrObjs=dict(attrobjs_map.items())

  def __setitem__(self,attr,value):
    ''' Set the attribute 'attr' to 'value'.
    '''
    if attr in self:
      del self[attr]
    self.__attrObjs[attr]=[ self.__node.newattr(attr, v) for v in value ]
    self.__nodedb.session.add_all(self.__attrObjs[attr])
    # store a tuple so that we can't do append()s etc
    dict.__setitem__(self, attr, tuple(value))

  def __delitem__(self,attr):
    ''' Remove the named attibute.
    '''
    self.__nodedb.session.flush()
    for attrObj in self.__attrObjs[attr]:
      self.__nodedb.session.delete(attrObj)
    dict.__delitem__(self,attr)

class Node(object):
  ''' A node in the node db.
      A node has the following attributes:
        ID, the db id
        NAME, the node name
        TYPE, the node type
      Other uppercase attributes are node attributes.
  '''

  _MODE_DIRECT = 0              # FOO is just a cigar
  _MODE_BY_ID = 1               # node where node.ID in self.FOO_IDs

  def __init__(self,_node,nodedb,attrs):
    ''' Initialise a new Node.
        We are supplied:
          _node, an sqlalchemy row object in the NODES table
          nodedb, the NodeDB with which this Node is associated
          attrs, a sequence of sqlalchemy row objects in the ATTRS table
    '''
    self.__dict__['_node']=_node
    self.__dict__['_attrs']=AttrMap(_node,nodedb,attrs)
    self.__dict__['_nodedb']=nodedb
    id=_node.ID
    if id in nodedb.nodeMap:
        print >>sys.stderr, "WARNING: Node.__init__: replacing %s with %s" % (nodedb.nodeMap[id],self)
    nodedb.nodeMap[id]=self

  def __eq__(self,other):
    return self.ID == other.ID
  def __str__(self):
    return "%s:%s" % (self.TYPE,self.NAME)
  def __repr__(self):
    return "%s:%s#%d" % (self.TYPE,self.NAME,self.ID)

  def __getattr__(self,attr):
    mode, k, plural = self._parseAttr(attr)
    if mode is None:
      return getattr(self._node, attr)
    if mode == Node._MODE_DIRECT:
      if k not in self._attrs:
        values=()
      else:
        values=self._attrs[k]
    elif mode == Node._MODE_BY_ID:
      values=self._nodedb.nodesByIds(self._attrs[k+"_ID"])
    else:
      assert False, " unimplemented mode %s (attr=%s: k=%s, plural=%s)" \
                        % (mode, attr, k, plural)
    if plural:
      return tuple(values)
    return the(values)

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

  def __hasattr__(self,attr):
    mode, k, plural = self._parseAttr(attr)
    if mode is None:
      return attr in ('ID', 'NAME', 'TYPE')

    if mode == Node._MODE_DIRECT:
      ks = self._attrs.get(k, ())
      return len(ks) > 0

    if mode == Node._MODE_BY_ID:
      return True

    return False

  def __setattr__(self,attr,value):
    mode, k, plural = self._parseAttr(attr)
    if mode is None:
      assert k != 'ID', "cannot set node.ID"
      if k == 'NAME':
        self._node.NAME=value
      elif attr == 'TYPE':
        self._node.TYPE=value
      else:
        assert False, "setattr(Node.%s): invalid attribute" % attr
      return

    if not plural:
      value=(value,)
    else:
      value=tuple(value)

    if mode == Node._MODE_DIRECT:
      if len(value) > 0 and hasattr(value[0], 'ID'):
        assert not k.endswith('_ID'), \
               "setattr(Node.%s): can't assign Nodes to _ID attributes" \
               % attr
        self._attrs[k+'_ID']=tuple( v.ID for v in value )
      else:
        self._attrs[k]=value
      return

    if mode == Node._MODE_BY_ID:
      self._attrs[k+'_ID']=tuple( v.ID for v in value )
      return

    assert False, "setattr(Node.%s): unsupported mode: %s" % (attr, mode)

  def __delattr__(self,attr):
    mode, k, plural = self._parseAttr(attr)
    assert mode is not None and mode == Node._MODE_DIRECT
    del self._attrs[k]

  def _parseAttr(self,attr):
    ''' Parse an attribute name and return mode, name, plural
          Non-intercepted name (bah): None, attr, None
          attr => k, plural
          FOO: if len(FOO) > 0:    _MODE_DIRECT, k, plural
               if len(FOO_ID) > 0, _MODE_BY_ID, k, plural
                                   node where node.ID in self.FOO_IDs
               return FOO          _MODE_DIRECT, k, plural
          FOO_ID:                  _MODE_DIRECT, k, plural
    '''
    k, plural = parseUC_sAttr(attr)
    if k is None \
    or k == 'TYPE' or k == 'NAME' or k == 'ID':
      # raw attr, not to be intercepted
      return None, attr, None
    if k.endswith('_ID'):
      return Node._MODE_DIRECT, k, plural
    _attrs=self._attrs
    ks = _attrs.get(k, ())
    if len(ks) > 0:
      # FOO really exists, use it
      return Node._MODE_DIRECT, k, plural
    k_IDs = _attrs.get(k+"_ID", ())
    if len(k_IDs) > 0:
      # node where node.ID in self.FOO_IDs
      return Node._MODE_BY_ID, k, plural
    # no FOO, no FOO_ID, use FOO directly
    return Node._MODE_DIRECT, k, plural

  def textdump(self, ofp):
    ofp.write("# %s\n" % (repr(self),))
    attrnames = self._attrs.keys()
    attrnames.sort()
    opattr = None
    for attr in attrnames:
      pattr = attr
      for value in self._attrs[attr]:
        pvalue = value
        if attr.endswith('_ID'):
          pvalue = str(self._nodedb.nodeById(int(value)))
          pattr = attr[:-3]
        else:
          pvalue = json.dumps(value)
        if opattr is not None and opattr == pattr:
          pattr = ''
        else:
          opattr = pattr
        ofp.write('%-15s %s\n' % (pattr, pvalue))

  def textload(self, ifp):
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
          value = self._nodedb[node_key]
        else:
          # 
          try:
            value = json.loads(ovalue)
          except ValueError, e:
            print >>sys.stderr, "json.loads(%s) error: %s" % (ovalue,e,)
            raise ValueError, e
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

  def edit(self, editor=None):
    if editor is None:
      editor = os.environ.get('EDITOR', 'vi')
    T = tempfile.NamedTemporaryFile(delete=False)
    self.textdump(T)
    T.close()
    qname = cs.sh.quotestr(T.name)
    os.system("%s %s" % (editor, qname))
    with closing(open(T.name)) as ifp:
      self.textload(ifp)
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
    if nodes is None or type(nodes) is str:
      nodes=NODESTable(metadata,name=nodes)
    self.nodes=nodes
    if attrs is None or type(attrs) is str:
      attrs=ATTRSTable(metadata,name=attrs)
    self.attrs=attrs
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

  def _newNode(self,_node,attrs):
    return Node(_node,self,attrs)

  def createNode(self,name,type,attrs=None):
    ''' Create a new Node in the database.
    '''
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

    N=self._newNode(_node,())
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

  def _toNode(self,*keys):
    ''' Flexible get-a-node function.
        (int) -> nodeById(int)
        ("#id") -> nodeById(int("id"))
        (name,type) -> nodeByNameAndType(name,type)
        ("TYPE:name") -> nodeByNameAndType(name,type)
    '''
    if len(keys) == 1:
      key = keys[0]
      if type(key) is int:
        return self.nodeById(key)
      if type(key) is str:
        if key[0] == '#':
          return self.nodeById(int(key[1:]))
        if key[0].isupper():
          t, n = key.split(':',1)
          return self.nodeByNameAndType(n, t)
    elif len(keys) == 2:
      return self.nodeByNameAndType(*keys)
    raise ValueError, "can't map %s to Node" % (keys,)

  def _nodesByType(self,type):
    _nodes=self.session.query(self._Node).filter_by(TYPE=type).all()
    self.session.add_all(_nodes)
    return _nodes

  def _nodes2Nodes(self,_nodes,checkMap):
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
      # obtain the attributes of the missing nodes
      # obtain and track all the attribute rows for the nodes specified
      # by missingIds
      filter=fieldInValues(self.attrs.c.NODE_ID,missingIds)
      attrs=self.session.query(self._Attr).filter(filter).all()
      self.session.add_all(attrs)
      # collate the attribute rows by NODE_ID
      nodeAttrs={}
      for attr in attrs:
        nodeAttrs.setdefault(attr.NODE_ID,[]).append(attr)
      # create the missing Node objects
      # and add them to the Ns list of Nodes
      Ns.extend([ self._newNode(_node,nodeAttrs.get(_node.ID,()))
                  for _node in self._nodesByIds(missingIds)
                ])

    return Ns

  def nodesByIds(self,ids):
    nodeMap=self.nodeMap
    Ns=list(nodeMap[id] for id in ids if id in nodeMap)
    missingIds=list(id for id in ids if id not in nodeMap)
    if len(missingIds) > 0:
      Ns.extend(self._nodes2Nodes(self._nodesByIds(missingIds),checkMap=False))
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
    return self._nodes2Nodes(self._nodesByType(type),checkMap=True)

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
    self.assertEqual(db._toNode("host2","HOST").ID, 2)
    self.assertEqual(db._toNode("HOST:host2").ID, 2)

  def testParentsByAttr(self):
    self.host1.SUBHOST=self.host2
    parents=self.host2.parentsByAttr('SUBHOST')
    self.assertEqual(the(parents).ID, self.host1.ID)

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
