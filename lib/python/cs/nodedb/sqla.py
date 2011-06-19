#!/usr/bin/python

import os
from types import StringTypes
import unittest
import sys
import sqlalchemy
from sqlalchemy import create_engine, \
                       MetaData, Table, Column, Index, Integer, String, \
                       select
from sqlalchemy.orm import mapper, sessionmaker
from sqlalchemy.sql import and_, or_, not_
from sqlalchemy.sql.expression import distinct
from cs.logutils import error, warn
from . import NodeDB, Backend
from .node import TestAll as NodeTestAll

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

  def _noteNodeKey(self, t, name, node_id):
    nodekey = (t, name)
    self.__nodekeysByID[node_id] = nodekey
    self.__IDbyTypeName[nodekey] = node_id

  def _node_id(self, t, name):
    if (t, name) not in self.__IDbyTypeName:
      assert not self.nodedb.readonly
      ins = self.nodes.insert().values(TYPE=t, NAME=name).execute()
      node_id = ins.lastrowid
      self._noteNodeKey(t, name, node_id)
    return node_id

  def _forgetNode(self, t, name, node_id):
    del self.__nodekeysByID[node_id]
    del self.__IDbyTypeName[t, name]

  def nodedata(self):
    ''' Pull all node data from the database.
    '''
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
      if onode_id is None or onode_id == node_id:
        if onode_id is not None:
          ot, oname = byID[onode_id]
          yield ot, oname, attrmap
        onode_id = node_id
        attrmap = {}
      attrmap.set_default(attr, []).append(value)
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
##      warn("deprecated :#node_id serialisation: %s" % (text,))
##      node_id = int(text[2:])
##      N = self.__nodekeysByID[int(text[2:])]
##      warn("  UPDATE %s SET VALUE=\"%s\" WHERE VALUE=\"%s\""
##           % (self.attrs, ":%s:%s" % (N.type, N.name), text))
##      return N
##    return Backend.fromtext(self, text)

  def sync(self):
    raise NotImplementedError

  def close(self):
    warn("cs.nodedb.sqla.Backend_SQLAlchemy.close() unimplemented")

  def __delitem__(self, nodekey):
    assert not self.nodedb.readonly
    t, name = nodekey
    node_id = self.__IDbyTypeName[t, name]
    self.attrs.delete(self.attrs.c.NODE_ID == node_id).execute()
    self.nodes.delete(self.nodes.c.NODE_ID == node_id).execute()
    self._forgetNode(t, name, node_id)

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

class TestAll(NodeTestAll):

  def setUp(self):
    self.backend=Backend_SQLAlchemy('sqlite:///:memory:')
    self.db=NodeDB(backend=self.backend)

if __name__ == '__main__':
  import sqlalchemy
  print 'SQLAlchemy version =', sqlalchemy.__version__
  unittest.main()
