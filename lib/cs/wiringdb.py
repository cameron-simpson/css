#!/usr/bin/python -tt

from cs.nodedb import NodeDB, Node
from cs.mappings import parseUC_sAttr
from cs.misc import the
import sys

class WiringNode(Node):
  ''' A WiringNode is a subclass of cs.nodedb.Node.
      Accessing FOO_OF returns the nodes whose FOO_ID value references
      this node's ID.
      Accessing FOO_OF_ID returns the IDs of nodes whose FOO_ID value
      references this node's ID.
      Accessing FOO  returns the nodes specified by the ID values in FOO_ID.
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
    if Node.__hasattr__(self,attr):
      return True
    k, plural = parseUC_sAttr(attr)
    if k is None:
      return False
    if k.endswith('_ID'):
      return hasattr(self._attrs,k)
    kid=k+'_ID'
    if hasattr(self._attrs,kid):
      return True
    return hasattr(self._attrs,k)

  def __getattr__(self,attr):
    if attr == 'TYPE' or attr == 'NAME' or attr == 'ID':
      return Node.__getattr__(self,attr)
    k, plural = parseUC_sAttr(attr)
    if k is None:
      return Node.__getattr__(self,attr)
    values=()
    if k.endswith('_OF'):
      values = list(
                 self._nodedb.nodesByIds(
                              int(id) for id in getattr(self,k+'_IDs')))
    elif k.endswith('_OF_ID'):
      nodedb=self._nodedb
      session=nodedb.session
      values = list( attrObj.NODE_ID
                   for attrObj in session
                                  .query(nodedb._Attr)
                                  .filter_by(ATTR=k[:-6]+'_ID',
                                             VALUE=str(self.ID)))
    elif not k.endswith("_ID"):
      kid=k+"_ID"
      if hasattr(self._attrs,kid):
        values = self._nodedb.nodesByIds(getattr(self,kid+'s'))
    elif self._hasattr(k):
      values = list(getattr(self._attrs,k+'s'))

    if plural:
      return values
    return the(values)

  def __setattr__(self,attr,value):
    k, plural = parseUC_sAttr(attr)
    if k is None:
      Node.__setattr__(self,attr,value)
      return
    if k.endswith('_OF') or k.endswith('_OF_ID'):
      raise AttributeError, "illegal assignment to .%s" % attr
    if not k.endswith('_ID') and hasattr(self._attrs,k+'_ID'):
      setattr(k+'_IDs', [ N.ID for N in value ])
      return
    if not plural:
      value=(value,)
    Node.__setattr__(self,k+'s', value)

  def __delattr__(self,attr):
    k, plural = parseUC_sAttr(attr)
    if k is None:
      Node.__delattr__(self,attr)
      return
    if k.endswith('_OF') or k.endswith('_OF_ID'):
      raise AttributeError, "illegal del of .%s" % attr
    if not attr.endswith('_ID') and self.hasattr(k+'_ID'):
      del self[k+'_ID']
      return
    Node.__delattr__(self,attr)

class WiringDB(NodeDB):
  def _newNode(self,_node,attrs):
    return WiringNode(_node,self,attrs)

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
