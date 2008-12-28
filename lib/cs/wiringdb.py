#!/usr/bin/python -tt

from cs.nodedb import NodeDB, Node
from cs.mappings import parseUC_sAttr
import sys

class WiringNode(Node):
  ''' A WiringNode is a subclass of cs.nodedb.Node.
      In addition to the normal pluralisable fields particular node types map
      FOO to FOO_ID. Accessing FOO dereferences the field FOO_ID and returns
      the corresponding nodes.
  '''
  _idFields={ 'HOST': ('NIC','RACK',),
              'SWITCHPORT': ('VLAN','SWITCH','RACK',),
              'VHOST': ('HOST',),
            }
  def idField(self,field):
    t=self.TYPE
    if t in WiringNode._idFields and field in WiringNode._idFields[t]:
      return field+'_ID'
    return None

  def _idf2nodes(self,field):
    return [ self._nodedb.NodeById(int(v)) for v in getattr(self,field+'s') ]
  def __hasattr__(self,attr):
    k, plural = parseUC_sAttr(attr)
    if k is not None:
      kid=self.idField(k)
      if kid is not None:
        return hasattr(self,kid)
    return Node.__getattr__(self,attr)
  def __getattr__(self,attr):
    k, plural = parseUC_sAttr(attr)
    if k is not None:
      if k == 'TYPE':
        return Node.__getattr__(self,'TYPE')
      t=self.TYPE
      kid=self.idField(k)
      if kid is not None:
        return self._idf2nodes(kid)
    return Node.__getattr__(self,attr)
  def __setattr__(self,attr,value):
    k, plural = parseUC_sAttr(attr)
    print >>sys.stderr, "WN.setattr(%s/%s,%s)" % (k,plural,value)
    if k is not None:
      t=self.TYPE
      kid=self.idField(k)
      if kid is not None:
        if not plural:
          value=(value,)
        setattr(self,kid+'s',[ N.ID for N in value ])
        return
    Node.__setattr__(self,attr,value)
  def __delattr__(self,attr):
    k, plural = parseUC_sAttr(attr)
    if k is not None:
      t=self.TYPE
      kid=self.idField(k)
      if kid is not None:
        delattr(self,kid)
        return
    Node.__delattr__(self,attr)

class WiringDB(NodeDB):
  def __init__(self,engine,nodes=None,attrs=None):
    NodeDB.__init__(self,engine=engine,nodes=nodes,attrs=attrs)

  def _newNode(self,_node,nodedb=None):
    if nodedb is None:
      nodedb=self
    return WiringNode(_node,nodedb)

  def __getitem__(self,k):
    if type(k) is int:
      return self.nodeById(k)
    if type(k) is str:
      k=(k, 'HOST')
    return self.nodeByNameType(k[0], k[1])

if __name__ == '__main__':
  import sqlalchemy; print sqlalchemy.__version__
  db=WiringDB('sqlite:///:memory')
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
  N2=db['newname']
  print "N2=%s" % (N2,)
  print "N.ATTRXs=%s" % (N.ATTRXs,)
  print "N.ATTR2=%s" % N.ATTR2
  print "N.ATTR3es=%s" % (N.ATTR3es,)
  sys.stdout.flush()
