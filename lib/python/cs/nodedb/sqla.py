#!/usr/bin/python

import os
import sys
from contextlib import contextmanager
from sqlalchemy import (
    MetaData, Table, Column, Index, Integer, String, select, create_engine
)
from sqlalchemy.pool import QueuePool
from sqlalchemy.sql import and_, asc
from cs.py3 import StringTypes
from cs.logutils import error, debug, trace
from cs.pfx import Pfx
from . import Backend

class Backend_SQLAlchemy(Backend):
  ''' Use an SQL database via sqlalchemy as the backend.
  '''

  def __init__(self, engine, nodes_name=None, attrs_name=None, readonly=False):
    Backend.__init__(self, readonly=readonly)
    if isinstance(engine, StringTypes):
      echo = len(os.environ.get('DEBUG', '')) > 0
    if isinstance(engine, StringTypes):
      engine = create_engine(engine, poolclass=QueuePool, echo=echo)
    if nodes_name is None:
      nodes_name = 'NODES'
    if attrs_name is None:
      attrs_name = 'ATTRS'
    self.engine = engine
    self.nodes_name = nodes_name
    self.attrs_name = attrs_name

    # forward and reverse (type, name) <=> node_id mappings
    self.__nodekeysByID = {}
    self.__IDbyTypeName = {}

  def init_nodedb(self):
    engine = self.engine
    metadata = MetaData()
    metadata.bind = engine
    nodes = self.nodes = self.NODESTable(metadata, name=self.nodes_name)
    Index('nametype', nodes.c.NAME, nodes.c.TYPE)
    attrs = self.attrs = self.ATTRSTable(metadata, name=self.attrs_name)
    Index('attrvalue', attrs.c.ATTR, attrs.c.VALUE)
    metadata.create_all()
    self.attrs.select().execute()

  def close(self):
    self.engine = None

  @staticmethod
  def NODESTable(metadata, name=None):
    ''' Define an SQLAlchemy Table for the nodes.
    '''
    if name is None:
      name = 'NODES'
    return Table(
        name,
        metadata,
        Column('ID', Integer, primary_key=True, nullable=False),
        Column('NAME', String(64)),
        Column('TYPE', String(64), nullable=False),
    )

  @staticmethod
  def ATTRSTable(metadata, name=None):
    ''' Define an SQLAlchemy Table for the attributes.
    '''
    if name is None:
      name = 'ATTRS'
    return Table(
        name,
        metadata,
        Column('ID', Integer, primary_key=True, nullable=False),
        Column('NODE_ID', Integer, nullable=False, index=True),
        Column('ATTR', String(64), nullable=False, index=True),
        # mysql max index key len is 1000, so VALUE only 900
        Column('VALUE', String(900), index=True),
    )

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
        self._forgetNode(t, name, node_id)
    for attr in N.keys():
      self.saveAttrs(N[attr])

  def _node_id(self, t, name):
    ''' Return the db node_id for the supplied type and name.
        Create a new node_id if unknown.
    '''
    node_id = self.__IDbyTypeName.get((t, name))
    if node_id is None:
      if self.nodedb.readonly:
        raise RuntimeError(
            "readonly: can't instantiate new NODE_ID for (%s, %s)" % (t, name)
        )
      ins = self.nodes.insert().values(TYPE=t, NAME=name).execute()
      node_id = ins.lastrowid
      self._noteNodeKey(t, name, node_id)
    return node_id

  def _forgetNode(self, t, name, node_id):
    ''' Forget the mapping between db node_id and (type, name).
    '''
    del self.__nodekeysByID[node_id]
    del self.__IDbyTypeName[t, name]

  @contextmanager
  def lockdata(self):
    ''' Obtain an exclusive lock on the database.
    '''
    debug("no SQLA lockdata()")
    yield None

  def fetch_updates(self):
    debug("no SQLA fetch_updates()")
    return ()

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
    with Pfx("%s.nodedata()...", self):
      nodes = self.nodes
      attrs = self.attrs
      byID = self.__nodekeysByID
      # load node data
      for node_id, t, name in select([nodes.c.ID, nodes.c.TYPE,
                                      nodes.c.NAME]).execute():
        self._noteNodeKey(t, name, node_id)
      # load Node attributes
      # TODO: order by NODE_ID, ATTR and use .extend in batches
      onode_id = None
      attrmap = {}
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

  def items(self):
    ''' Yield `((t,name),attrmap)` for the node data.
    '''
    for t, name, attrmap in self.nodedata():
      yield (t, name), attrmap

  def keys(self):
    ''' Yield `t,name` for the node data.
    '''
    nodes = self.nodes
    # load node keys
    for _, t, name in select([
        nodes.c.TYPE,
        nodes.c.NAME,
    ]).execute():
      yield t, name

  def _update(self, update):
    ''' Apply a single `Update` to the database.
    '''
    self.push_updates(update.to_csv())

  def push_updates(self, csvrows):
    ''' Apply the update rows from the iterable `csvrows` to the database.
    '''
    trace("push_updates: write our own updates")
    totext = self.nodedb.totext
    for thisrow in csvrows:
      t, name, attr, value = thisrow
      node_id = self._node_id(t, name)
      if attr.startswith('-'):
        attr = attr[1:]
        if value != "":
          raise ValueError(
              "ATTR = \"%s\" but non-empty VALUE: %r" % (attr, value)
          )
        self.attrs.delete(
            and_(self.attrs.c.NODE_ID == node_id, self.attrs.c.ATTR == attr)
        ).execute()
      else:
        # add attribute
        if name.startswith('='):
          # discard node and start anew
          name = name[1:]
          self.attrs.delete(self.attrs.c.NODE_ID == node_id).execute()
        if attr.startswith('='):
          # reset attribute completely before appending value
          attr = attr[1:]
          self.attrs.delete(
              and_(self.attrs.c.NODE_ID == node_id, self.attrs.c.ATTR == attr)
          ).execute()
        self.attrs.insert().execute(
            [{
                'NODE_ID': node_id,
                'ATTR': attr,
                'VALUE': totext(value),
            }]
        )

if __name__ == '__main__':
  import cs.nodedb.sqla_tests
  cs.nodedb.sqla_tests.selftest(sys.argv)
