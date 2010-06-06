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
from cs.logutils import error
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

    self.__nodesByID = {}
    self.__IDbyTypeName = {}

  def nodeByTypeName(self, t, name):
    ''' Map (type,name) to Node.
    '''
    return self.__nodesByID[self.__IDbyTypeName[t, name]]

  def _noteNode(self, N, nodeid):
    self.__nodesByID[nodeid] = N
    self.__IDbyTypeName[N.type, N.name] = nodeid

  def _forgetNode(self, N, nodeid):
    del self.__nodesByID[nodeid]
    del self.__IDbyTypeName[N.type, N.name]

  def _preload(self):
    ''' Prepopulate the NodeDB from the database.
    '''
    nodes = self.nodes
    attrs = self.attrs
    byID = self.__nodesByID
    # load Nodes
    for nodeid, t, name in select( [ nodes.c.ID,
                                     nodes.c.TYPE,
                                     nodes.c.NAME
                                   ] ).execute():
      N = self.nodedb._makeNode(t, name)
      self._noteNode(N, nodeid)
    # load Node attributes
    # TODO: order by NODE_ID, ATTR and use .extend in batches
    for attrid, nodeid, attr, value in select( [ attrs.c.ID,
                                                 attrs.c.NODE_ID,
                                                 attrs.c.ATTR,
                                                 attrs.c.VALUE,
                                               ] ).execute():
      if nodeid not in byID:
        error("invalid NODE_ID(%s): ignore %s=%s" % (nodeid, attr, value))
        continue
      N = byID[nodeid]
      value = self.deserialise(value)
      N[attr+'s'].append(value, noBackend=True )

  def deserialise(self, value):
    if value.startswith(':#'):
      # deprecated :#node_id serialisation
      return self.__nodesByID[int(value[2:])]
    return Backend.deserialise(self, value)

  def close(self):
    raise NotImplementedError

  def newNode(self, N):
    assert not self.nodedb.readonly
    ins = self.nodes.insert().values(TYPE=N.type, NAME=N.name).execute()
    self._noteNode(N, ins.lastrowid)

  def delNode(self, N):
    assert not self.nodedb.readonly
    nodeid = self.__IDbyTypeName[N.type, N.name]
    self.attrs.delete(self.attrs.c.NODE_ID == nodeid).execute()
    self.nodes.delete(self.nodes.c.NODE_ID == nodeid).execute()
    self._forgetNode(N, nodeid)

  def extendAttr(self, N, attr, values):
    assert not self.nodedb.readonly
    nodeid = self.__IDbyTypeName[N.type, N.name]
    ins_values = [ { 'NODE_ID': nodeid,
                     'ATTR':    attr,
                     'VALUE':   self.serialise(value),
                   } for value in values ]
    self.attrs.insert().execute(ins_values)

  def set1Attr(self, N, attr, value):
    # special case, presumes there's only one VALUE
    assert not self.nodedb.readonly
    nodeid = self.__IDbyTypeName[N.type, N.name]
    self.attrs.update().where(and_(self.attrs.c.NODE_ID == nodeid,
                                   self.attrs.c.ATTR == attr)) \
                       .values(VALUE=value) \
                       .execute()

  def delAttr(self, N, attr):
    assert not self.nodedb.readonly
    nodeid = self.__IDbyTypeName[N.type, N.name]
    self.attrs.delete(and_(self.attrs.c.NODE_ID == nodeid,
                           self.attrs.c.ATTR == attr)).execute()

class TestAll(NodeTestAll):

  def setUp(self):
    self.backend=Backend_SQLAlchemy('sqlite:///:memory:')
    self.db=NodeDB(backend=self.backend)

if __name__ == '__main__':
  import sqlalchemy
  print 'SQLAlchemy version =', sqlalchemy.__version__
  unittest.main()
