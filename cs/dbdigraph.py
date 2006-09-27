import string
import re
from cs.misc import cmderr, debug, warn, die, uniq
from cs.hier import flavour
from cs.db import dosql, SQLQuery, sqlise, today

NodeCoreAttributes=('NAME','TYPE')

def SQLtypeTest(*types):
  assert len(types) > 0
  if len(types) == 1:
    return "TYPE = "+sqlise(types[0])
  return "TYPE IN ("+string.join([ sqlise(type) for type in types ], ",")+")"

CapWord_re = re.compile(r'^[A-Z][A-Z_]*[A-Z]$')

def testAllCaps(s):
  return CapWord_re.match(s)

class DBDiGraph:
  def __init__(self,nodeTable,attrTable):
    self.nodes=nodeTable
    self.attrs=attrTable
    self.__nodeCache={}
    self.__selectIdsBaseQuery='SELECT ID FROM '+self.nodes.name

  def keys(self):
    return self.nodeidsWhere()

  def createNode(self,name,type,multipleNamesOk=False):
    ''' Creates a new node with the specified name and type.
        Returns the ID of the new node.
    '''
    if not multipleNamesOk and self.nodeByNameAndType(name,type):
      die("createNode(name="+str(name)+", type="+type+"): already exists")

    self.nodes.insert({'NAME': name, 'TYPE': type});
    return self.nodeByNameAndType(name,type)

  def addAnonNode(self):
    maxNodeId=[row[0] for row in SQLQuery(self.nodes.conn,'SELECT MAX(ID) FROM NODES')][0]
    return self.createNode(str(maxNodeId+1),'_NODE')

  def _newNode(self,nodeid):
    ''' Constructs a new node instance associated with an id.
        To be overridden by subclasses.
    '''
    return DBDiGraphNode(nodeid,self)

  def __getitem__(self,nodeid):
    if nodeid not in self.__nodeCache:
      self.__nodeCache[nodeid]=self._newNode(nodeid)
    return self.__nodeCache[nodeid]

  def nodeByNameAndType(self,name,*types):
    node=None
    for id in self.nodeidsByNameAndType(name,*types):
      if node is None:
        node=self[id]
      else:
        cmderr("multiple hits for node named", name, "- ignoring id", str(id))

    return node

  def need(self,name,type):
    node=self.nodeByNameAndType(name,type)
    if node is None:
      node=self.createNode(name,type)
    return node

  def nodeidsWhere(self,where):
    query=self.__selectIdsBaseQuery
    if where is not None: query=query+' WHERE '+where
    debug("query =", query)
    return [ row[0] for row in SQLQuery(self.nodes.conn,query) ]

  def nodeidsByNameAndType(self,name,*types):
    return self.nodeidsWhere('NAME = '+sqlise(name)+' AND '+SQLtypeTest(*types)) 

  def nodeidsByAttr(self,attr,value):
    return [ row[0] for row in SQLQuery(self.attrs.conn,
                                        'SELECT DISTINCT(ID) FROM '+self.attrs.name
                                        +' WHERE ATTR = '+sqlise(attr)+' AND VALUE = '+sqlise(value)) ]

  def nodeidsByAttrs(self,attrs):
    return [ row[0] for row in SQLQuery(self.attrs.conn,
                                        'SELECT DISTINCT(ID) FROM '+self.attrs.name
                                        +' WHERE ATTR = '+sqlise(attr)+' AND VALUE = '+sqlise(value)) ]

  def nodeidsByType(self,*types):
    return self.nodeidsWhere(SQLtypeTest(*types))

class DBDiGraphNode:
  def __init__(self,id,digraph):
    self.id=id
    self.digraph=digraph

  def clone(self):
    N={}
    for k in self.keys():
      N[k]=self[k]
    for k in NodeCoreAttributes:
      N[k]=self[k]
    return N

  def keys(self):
    return [ row[0]
             for row in SQLQuery(self.digraph.attrs.conn,
	     			 'SELECT DISTINCT(ATTR) FROM '+self.digraph.attrs.name
				 +' WHERE ID_REF = '+str(self.id))
	   ]

  def attrs(self):
    return uniq(NodeCoreAttributes+self.digraph.attrs.getAttrs(self.id))

  def __getattr__(self,attr):
    if attr in self.__dict__:
      raise AttributeError, "WTF?"
    if attr == "id":
      raise AttributeError, "WTF2? "+`self.__dict__`
    if testAllCaps(attr):
      return self.__fieldAccess(attr)
    raise AttributeError, "node id="+str(self.id)+" has no attribute named "+attr

  def __setattr__(self,attr,value):
    if attr in self.__dict__ or attr in ('id', 'digraph'):
      self.__dict__[attr]=value
      return

    if testAllCaps(attr):
      self[attr]=value
      return

    raise AttributeError, "node id="+str(self.id)+" has no attribute named "+attr

  def __fieldAccess(self,field,value=None):
    if value is None:
      return self[field]
    self[field]=value

  def __getitem__(self,key):
    ''' Return the value of the specific key.
	If no values, return None.
	If one value, return the value.
	If more values, return the list.
    '''
    if key in NodeCoreAttributes:
      return self.digraph.nodes[self.id][key]

    values=self.getAttr(key)
    if len(values) == 0:
      return None
    if len(values) == 1:
      return values[0]
    return values

  def __setitem__(self,key,value):
    ''' Set a new attribute.
        If the value is an array, add all the elements.
    '''
    if key in NodeCoreAttributes:
      self.digraph.nodes[self.id][key]=value
      return

    self.setAttr(key,value)

  def __delitem__(self,key):
    if key in NodeCoreAttributes:
      raise IndexError, "can't delete NodeCoreAttribute "+key

    # TODO: recursively delete any linked hashes?
    dosql(self.digraph.attrs.conn,
	  'DELETE FROM '+self.digraph.attrs.name
	  +' WHERE ID_REF = '+str(self.id)+' AND ATTR = '+sqlise(key))

  def setAttr(self,attr,values):
    del self[attr]
    self.addAttr(attr,values)

  def addAttr(self,attr,values,is_idref=0):
    if flavour(values) != "ARRAY":
      values=(values,)

    for value in values:
      assert is_idref == 0 or int(value) > 0, "is_idref="+`is_idref`+" and value="+`value`
      if flavour(value) == "HASH":
        hash=self.digraph.addAnonNode()
        self.addAttr(attr,str(hash.id),1)
        for k in value.keys():
          hash[k]=value[k]
      else:
        debug("insert(ID_REF="+str(self.id)+",ATTR="+attr+",VALUE="+`value`+")")
        self.digraph.attrs.insert({'ID_REF': self.id,
                           'ATTR': attr,
                           'VALUE': value,
                           'IS_IDREF': is_idref});

  def getAttr(self,attr):
    # suck in all the rows
    rows=[row for row
          in SQLQuery(self.digraph.attrs.conn,
                      'SELECT VALUE, IS_IDREF FROM '+self.digraph.attrs.name
                      +' WHERE ID_REF = '+str(self.id)+' AND ATTR = '+sqlise(attr))]

    # stash the scalar values
    values=[row[0] for row in rows if not row[1]]

    # stash the nested values
    for hashid in [int(row[0]) for row in rows if row[1]]:
      values.append(self.digraph[hashid])

    return values

  def edges(self,idField='FROM_ID'):
    ''' Returns edges running from this node.
        Optional parameter idField defaults to ID_FROM.
    '''
    nodetbl=self.digraph.nodes.name
    nodeid=nodetbl+'.ID'
    attrtbl=self.digraph.attrs.name
    attridref=attrtbl+'.ID_REF'
    attrattr =attrtbl+'.ATTR'
    attrvalue=attrtbl+'.VALUE'
    query='SELECT DISTINCT('+attridref+') FROM '+nodetbl+' INNER JOIN '+attrtbl+' ON '+attrattr+' = '+sqlise(idField)+' AND '+attrvalue+' = '+str(self.id)
    return [ self.digraph[row[0]] for row in SQLQuery(self.digraph.nodes.conn,query) ]

  def addEdge(self,toNode,name=None):
    if name is None:
      # an anonymous edge
      edge=self.digraph.addAnonNode()
      edge.TYPE='EDGE'
    else:
      edge=self.digraph.createNode(name,'EDGE')

    edge.FROM_ID=self.id
    edge.TO_ID=toNode.id
    edge.START_DATE=today()
