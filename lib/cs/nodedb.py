#!/usr/bin/python -tt
#
# A database of nodes with attributes.
#       - Cameron Simpson <cs@zip.com.au> 25dec2008
#

from cs.mappings import SeqMapUC_Attrs, isUC_
from cs.misc import the
import sqlalchemy
from sqlalchemy import create_engine, \
                       MetaData, Table, Column, Integer, String, \
                       select
from sqlalchemy.orm import mapper, sessionmaker
from sqlalchemy.sql import and_, or_, not_
from weakref import WeakValueDictionary
import sys

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
    dict.__init__(self)
    self.__node=node
    self.__nodedb=nodedb
    self.__attrObjs=dict([ (A.ATTR, A) for A in attrs ])
    dict.__init__(self, [ (A.ATTR, tuple(A.VALUE)) for A in attrs ])

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
      Other uppercase attributes are node attributes presented via the
      cs.mappings.SeqMapUC_Attrs interface.
  '''
  def __init__(self,_node,nodedb,attrs):
    self.__dict__['_node']=_node
    self.__dict__['_attrs']=SeqMapUC_Attrs(AttrMap(_node,nodedb,attrs))
    self.__dict__['_nodedb']=nodedb
    id=_node.ID
    assert id not in nodedb.nodeMap
    nodedb.nodeMap[id]=self

  def __str__(self):
    return "%s:%s#%d%s" % (self.NAME,self.TYPE,self.ID,self._attrs)

  def _hasattr(self,attr):
    return hasattr(self._attrs,attr)
  def __hasattr__(self,attr):
    if attr in ('ID', 'NAME', 'TYPE'):
      return True
    return self._hasattr(attr)

  def __getattr__(self,attr):
    if attr == 'ID':
      return self._node.ID
    if attr == 'NAME':
      return self._node.NAME
    if attr == 'TYPE':
      return self._node.TYPE
    return getattr(self._attrs,attr)

  def __setattr__(self,attr,value):
    assert attr != 'ID'
    if attr == 'NAME':
      self._node.NAME=value
    elif attr == 'TYPE':
      self._node.TYPE=value
    else:
      setattr(self._attrs,attr,value)

  def __delattr__(self,attr):
    assert attr not in ('ID','NAME','TYPE')
    delattr(self._attrs,attr)

# TODO: make __enter__/__exit__ for running a session?
class NodeDB(object):
  def __init__(self,engine,nodes='NODES',attrs='ATTRS'):
    if type(engine) is str:
      engine = create_engine(engine, echo=True)
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
      nodeAttrs={}
      filter=fieldInValues(self.attrs.c.NODE_ID,missingIds)
      attrs=self.session.query(self._Attr).filter(filter).all()
      self.session.add_all(attrs)
      for attr in attrs:
        nodeAttrs.setdefault(attr.NODE_ID,[]).append(attr)
      # create the missing Node objects
      Ns.extend([ self._newNode(_node,nodeAttrs.get(_node.ID,()))
                  for _node in self._nodesByIds(missingIds)
                ])

    return Ns

  def nodesByIds(self,ids):
    nodeMap=self.nodeMap
    Ns=[ nodeMap[id] for id in ids if id in nodeMap ]
    missingIds=list(id for id in ids if id not in nodeMap)
    if len(missingIds) > 0:
      Ns.extend(self._nodes2Nodes(self._nodesByIds(missingIds),checkMap=False))
    return Ns

  def nodeById(self,id):
    return the(self.nodesByIds((id,)))

  def nodeByNameAndType(self,name,type):
    return the(self._nodes2Nodes(self._nodesByNameAndType(name,type),checkMap=True))

  def nodesByType(self,type):
    return self._nodes2Nodes(self._nodesByType(type),checkMap=True)

  def __contains__(self,key):
    try:
      N=self[key]
    except IndexError:
      return False
    return True
  def __getitem__(self,key):
    N=self._toNode(key)
    # should not happen, but the code tested for it
    assert N is not None
    return N

if __name__ == '__main__':
  print sqlalchemy.__version__
  db=NodeDB('sqlite:///:memory:')
  N=db.createNode('host1','HOST')
  db.session.flush()
  print str(N)
  N.ATTR1=3
  print str(N)
  ##db.session.flush()
  N.ATTR1=4
  print str(N)
  ##db.session.flush()
  N.NAME='newname'
  db.session.flush()
  print str(N)
  N.ATTR2=5
  N.ATTR3s=[5,6,7]
  N.ATTR2=7
  print str(N)
  db.session.flush()
  print "N.ID=%d" % N.ID
  print "1 in db: %s" % (1 in db)
  print "1 in db: %s" % (1 in db)
  print "9 in db: %s" % (9 in db)
  print db._toNode(1)
  print db._toNode("#1")
  print db._toNode("newname","HOST")
  print db._toNode("HOST:newname")
  print "N.ATTRXs=%s" % (N.ATTRXs,)
  print "N.ATTR2=%s" % N.ATTR2
  print "N.ATTR3es=%s" % (N.ATTR3es,)
  print "N.ATTR3=%s" % N.ATTR3
