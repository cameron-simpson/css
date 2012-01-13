#!/usr/bin/python

import os
from thread import allocate_lock
from types import StringTypes
import sys
import sqlalchemy
from sqlalchemy import create_engine, \
                       MetaData, Table, Column, Index, Integer, String, \
                       select
from sqlalchemy.orm import mapper, sessionmaker
from sqlalchemy.sql import and_, or_, not_, asc
from sqlalchemy.sql.expression import distinct
from cs.logutils import Pfx, error, warning, debug
from . import NodeDB, Backend

def NODESTable(metadata, name=None):
  ''' Set up an SQLAlchemy Table for the nodes.
  '''
  if name is None:
    name='NODES'
  return Table(name, metadata,
               Column('ID', Integer, primary_key=True, nullable=False),
               Column('NAME', String(64)),
               Column('TYPE', String(64), nullable=False),
              )

def ATTRSTable(metadata, name=None):
  ''' Set up an SQLAlchemy Table for the attributes.
  '''
  if name is None:
    name='ATTRS'
  return Table(name, metadata,
               Column('ID', Integer, primary_key=True, nullable=False),
               Column('NODE_ID', Integer, nullable=False, index=True),
               Column('ATTR', String(64), nullable=False, index=True),
               # mysql max index key len is 1000, so VALUE only 900
               Column('VALUE', String(900), index=True),
              )

class Backend_SQLAlchemy(Backend):

  def __init__(self, engine, nodes=None, attrs=None, readonly=False):
    self._lock = allocate_lock()
    self.readonly = readonly
    if nodes is None:
      nodes = 'NODES'
    if attrs is None:
      attrs = 'ATTRS'
    if type(engine) in StringTypes:
      engine = create_engine(engine, echo=len(os.environ.get('DEBUG','')) > 0)
    metadata=MetaData()
    metadata.bind = engine
    if type(nodes) in StringTypes:
      nodes=NODESTable(metadata, name=nodes)
    self.nodes=nodes
    Index('nametype', nodes.c.NAME, nodes.c.TYPE)
    if type(attrs) is str:
      attrs=ATTRSTable(metadata, name=attrs)
    self.attrs=attrs
    Index('attrvalue', attrs.c.ATTR, attrs.c.VALUE)
    self.engine=engine
    metadata.create_all()

    # forward and reverse (type, name) <=> node_id mappings
    self.__nodekeysByID = {}
    self.__IDbyTypeName = {}

  def close(self):
    debug("Backend_SQLAlchemy.close()")

  def _noteNodeKey(self, t, name, node_id):
    ''' Remember the mapping between db node_id and (type, name).
    '''
    nodekey = (t, name)
    self.__nodekeysByID[node_id] = nodekey
    self.__IDbyTypeName[nodekey] = node_id

  def __setitem__(self, nodekey, N):
    t, name = nodekey
    with self._lock:
      node_id = self.__IDbyTypeName.get(nodekey)
      if node_id is not None:
        self._forgetNode( t, name, node_id )
    node_id = self._node_id( t, name )
    for attr in N.keys():
      self.saveAttrs(N[attrs])

  def _node_id(self, t, name):
    ''' Return the db node_id for the supplied type and name.
        Create a new node_id if unknown.
    '''
    if (t, name) not in self.__IDbyTypeName:
      assert not self.nodedb.readonly
      ins = self.nodes.insert().values(TYPE=t, NAME=name).execute()
      node_id = ins.lastrowid
      self._noteNodeKey(t, name, node_id)
    return node_id

  def _forgetNode(self, t, name, node_id):
    ''' Forget the mapping between db node_id and (type, name).
    '''
    del self.__nodekeysByID[node_id]
    del self.__IDbyTypeName[t, name]

  def __delitem__(self, nodekey):
    assert not self.nodedb.readonly
    t, name = nodekey
    node_id = self.__IDbyTypeName[t, name]
    self.attrs.delete(self.attrs.c.NODE_ID == node_id).execute()
    self.nodes.delete(self.nodes.c.NODE_ID == node_id).execute()
    self._forgetNode(t, name, node_id)

  def nodedata(self):
    ''' Pull all node data from the database.
    '''
    with Pfx("%s.nodedata()..." % (self,)):
      nodes = self.nodes
      attrs = self.attrs
      byID = self.__nodekeysByID
      # load node data
      for node_id, t, name in select( [ nodes.c.ID,
                                        nodes.c.TYPE,
                                        nodes.c.NAME
                                      ] ).execute():
        self._noteNodeKey(t, name, node_id)
      # load Node attributes
      # TODO: order by NODE_ID, ATTR and use .extend in batches
      onode_id = None
      for node_id, attr, value in select( [ attrs.c.NODE_ID,
                                            attrs.c.ATTR,
                                            attrs.c.VALUE,
                                          ] ) \
                                  .order_by(asc(attrs.c.NODE_ID)) \
                                  .execute():
        if node_id not in byID:
          error("invalid NODE_ID(%s): ignore %s=%s" % (node_id, attr, value))
          continue
        t, name = byID[node_id]
        if onode_id is None or onode_id != node_id:
          # yield previous node
          if onode_id is not None:
            ot, oname = byID[onode_id]
            yield ot, oname, attrmap
          # commence new node
          onode_id = node_id
          attrmap = {}
        attrmap.setdefault(attr, []).append(value)
      # yield final node
      if onode_id is not None:
        ot, oname = byID[onode_id]
        yield ot, oname, attrmap

  def iteritems(self):
    for t, name, attrmap in self.nodedata():
      yield (t, name), attrmap

  def iterkeys(self):
    nodes = self.nodes
    # load node keys
    for node_id, t, name in select( [ nodes.c.TYPE,
                                      nodes.c.NAME
                                    ] ).execute():
      yield t, name

##  def fromtext(self, text):
##    if text.startswith(':#'):
##      warning("deprecated :#node_id serialisation: %s" % (text,))
##      node_id = int(text[2:])
##      N = self.__nodekeysByID[int(text[2:])]
##      warning("  UPDATE %s SET VALUE=\"%s\" WHERE VALUE=\"%s\""
##           % (self.attrs, ":%s:%s" % (N.type, N.name), text))
##      return N
##    return Backend.fromtext(self, text)

  def sync(self):
    raise NotImplementedError

  def close(self):
    warning("cs.nodedb.sqla.Backend_SQLAlchemy.close() unimplemented")

  def extendAttr(self, t, name, attr, values):
    assert not self.nodedb.readonly
    node_id = self.__IDbyTypeName[t, name]
    ins_values = [ { 'NODE_ID': node_id,
                     'ATTR':    attr,
                     'VALUE':   self.totext(value),
                   } for value in values ]
    self.attrs.insert().execute(ins_values)

  def set1Attr(self, t, name, attr, value):
    # special case, presumes there's only one VALUE
    assert not self.nodedb.readonly
    node_id = self.__IDbyTypeName[t, name]
    self.attrs.update().where(and_(self.attrs.c.NODE_ID == node_id,
                                   self.attrs.c.ATTR == attr)) \
                       .values(VALUE=value) \
                       .execute()

  def delAttr(self, t, name, attr):
    assert not self.nodedb.readonly
    node_id = self.__IDbyTypeName[t, name]
    self.attrs.delete(and_(self.attrs.c.NODE_ID == node_id,
                           self.attrs.c.ATTR == attr)).execute()

if __name__ == '__main__':
  import cs.nodedb.sqla_tests
  cs.nodedb.sqla_tests.selftest(sys.argv)
