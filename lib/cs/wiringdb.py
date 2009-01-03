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
    return list(self._nodedb.NodeById(int(v)) for v in getattr(self,field+'s'))
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
      if k.endswith('_OF'):
        return list(
                 self._nodedb.nodesByIds(
                                int(id) for id in getattr(self,k+'_IDs')))
      if k.endswith('_OF_ID'):
        nodedb=self._nodedb
        session=nodedb.session
        return list( attrObj.NODE_ID
                     for attrObj in session
                                    .query(nodedb._Attr)
                                    .filter_by(ATTR=k[:-6]+'_ID',
                                               VALUE=str(self.ID)))
      kid=self.idField(k)
      if kid is not None:
        return self._idf2nodes(kid)
    return Node.__getattr__(self,attr)
  def __setattr__(self,attr,value):
    k, plural = parseUC_sAttr(attr)
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

  def _newNode(self,_node,attrs):
    return WiringNode(_node,self,attrs)

  def __getitem__(self,k):
    if type(k) is int:
      return self.nodeById(k)
    if type(k) is str:
      k=(k, 'HOST')
    return self.nodeByNameAndType(k[0], k[1])

if __name__ == '__main__':
  import sqlalchemy; print sqlalchemy.__version__
  db=WiringDB('sqlite:///:memory')
  N=db.createNode('host1','HOST')
  NIC=db.createNode(None,'NIC')
  N.NIC=NIC
  print "N=",str(N)
  print "NIC=",str(NIC)
  Hs=NIC.NIC_OFs
  print "NIC_OF(NIC)s=",[str(H) for H in Hs]
  HIDs=NIC.NIC_OF_IDs
  print "NIC_OF_ID(NIC)s=",HIDs
