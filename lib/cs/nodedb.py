#!/usr/bin/python -tt
#
# A database of nodes with attributes.
#       - Cameron Simpson <cs@zip.com.au> 25dec2008
#

from cs.mappings import SeqMapUC_Attrs, isUC_
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
  def __init__(self,node,nodedb):
    dict.__init__(self)
    self.__node=node
    self.__nodedb=nodedb
    session=nodedb.session
    self.__attrObjs={}
    # load up the attributes
    for attrObj in session.query(nodedb._Attr).filter_by(NODE_ID=node.ID):
      A.__addAttrObj(attrObj)
  def __addAttr(self,attrObj):
    attr=attrObj.ATTR
    self.__attrObjs.setdefault(attr,[]).append(attrObj)
    if attr not in self:
      dict.__setitem__(self,attr,[])
    dict.__getitem__(self,attr).append(attrObj.VALUE)
  def __setitem__(self,attr,value):
    if attr in self:
      del self[attr]
    self.__attrObjs[attr]=[ self.__node.newattr(attr,v) for v in value ]
    self.__nodedb.session.add_all(self.__attrObjs[attr])
    dict.__setitem__(self,attr,value)
  def __delitem__(self,attr):
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
  def __init__(self,_node,nodedb):
    self.__dict__['_node']=_node
    self.__dict__['_attrs']=SeqMapUC_Attrs(AttrMap(_node,nodedb))
    nodedb.nodeMap[_node.ID]=self

  def __str__(self):
    return "%s:%s#%d%s" % (self.NAME,self.TYPE,self.ID,self._attrs)

  def __hasattr__(self,attr):
    if attr in ('ID', 'NAME', 'TYPE'):
      return True
    return hasattr(self._attrs,attr)

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
  def __init__(self,engine,metadata,nodes='NODES',attrs='ATTRS'):
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

  def createNode(self,name,type):
    N=self._Node(name,type)
    self.session.add(N)
    self.session.flush()
    return Node(N,self)

  def _NodeById(self,id):
    return the(self.session.query(self._Node).filter_by(ID=id).all())
  def _NodesByIds(self,ids):
    return self.session.query(self._Node).filter_by(fieldInValues('ID',ids)).all()
  def nodeById(self,id):
    if id not in self.nodeMap:
      Node(self._NodeById(id), self)
    return self.nodeMap[id]

  def nodesByIds(self,ids):
    nodeMap=self.nodeMap
    Ns=[ nodeMap[id] for id in ids if id in nodeMap ]
    missing=[ id for id in ids if id not in nodeMap ]
    Ns.extend([ Node(_node,self) for _node in _NodesByIds(missing) ])
    return Ns

  def __getitem__(self,id):
    N=self.nodeById(id)
    if N is None:
      raise IndexError
    return N

if __name__ == '__main__':
  print sqlalchemy.__version__
  engine = create_engine('sqlite:///:memory:', echo=True)
  metadata = MetaData()
  db=NodeDB(engine,metadata)
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
  print "N.ATTRXs=%s" % (N.ATTRXs,)
  print "N.ATTR2=%s" % N.ATTR2
  print "N.ATTR3es=%s" % (N.ATTR3es,)
  print "N.ATTR3=%s" % N.ATTR3
