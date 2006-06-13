import string
from cs.misc import warn
from cs.db import dosql, SQLQuery, sqlise

def typeTest(*types):
  assert len(types) > 0
  if len(types) == 1:
    return "TYPE = "+sqlise(types[0])
  warn("types =", `types`)
  return "TYPE IN ("+string.join([ sqlise(type) for type in types ], ",")+")"

class DBDiGraph:
  def __init__(self,nodeTable,edgeTable,attrTable):
    self.nodes=nodeTable
    self.edges=edgeTable
    self.attrs=DBDiGraphAttrs(attrTable,self)
    self.__nodeCache={}
    self.__selectIdsBaseQuery='SELECT ID FROM '+self.nodes.name

  def preload(self):
    self.nodes.preload()
    self.edges.preload()
    self.attrs.preload()

  def selectIds(self,where):
    query=self.__selectIdsBaseQuery
    if where is not None: query=query+' WHERE '+where
    warn("query =", query)
    return [ row[0] for row in SQLQuery(self.nodes.conn,query) ]

  def keys(self):
    return self.selectIds()

  def _newNode(self,nodeid):
    return DBDiGraphNode(nodeid,self)

  def __getitem__(self,nodeid):
    if nodeid not in self.__nodeCache:
      self.__nodeCache[nodeid]=self._newNode(nodeid)
    return self.__nodeCache[nodeid]

  def nodeidsByNameAndType(self,name,*types):
    return self.selectIds('NAME = '+sqlise(name)+' AND '+typeTest(*types)) 

  def nodesByType(self,*types):
    return [ self[id] for id in self.selectIds(typeTest(*types)) ]
    
  def nodeByNameAndType(self,name,*types):
    node=None
    for id in self.nodeidsByNameAndType(name,*types):
      if node is None:
        node=self[id]
      else:
        warn("multiple hits for node named", name, "- discarding id", str(id))

    return node

class DBDiGraphNode:
  def __init__(self,id,digraph):
    self.id=id
    self.digraph=digraph

  def _dbrow(self):
    return self.digraph.nodes[self.id]

  def keys(self):
    return self.digraph.attrs.getAttrNames(self.id)

  def attrs(self):
    return self.digraph.attrs.getAttrs(self.id)

  def __getitem__(self,key):
    ''' Return the value of the specific key.
	If no values, return None.
	If one value, return the value.
	If more values, return the list.
    '''
    values=self.digraph.attrs.getAttr(self.id,key)
    if len(values) == 0:
      return None
    if len(values) == 1:
      return values[0]
    return values

  def __setitem__(self,key,value):
    if type(value) is str: value=(value,)
    self.digraph.attrs.setAttr(self.id,key,value)

  def __delitem__(self,key):
    self.digraph.attrs.delAttr(self.id,key)

  def addAttrs(self,key,values):
    self.digraph.attrs.addAttr(self.id,key,values)

class DBDiGraphAttrs:
  def __init__(self,attrTable,digraph):
    self.table=attrTable
    self.digraph=digraph

  def preload(self):
    self.table.preload()

  def getAttr(self,srcid,key):
    return [ row[0]
             for row in SQLQuery(self.table.conn,
	     			 'SELECT VALUE FROM '+self.table.name
				 +' WHERE ID_REF = '+str(srcid)+' AND ATTR = '+sqlise(key))
	   ]

  def delAttr(self,srcid,attr):
    dosql(self.table.conn,
	  'DELETE FROM '+self.table.name
	  +' WHERE ID_REF = '+str(srcid)+' AND ATTR = '+sqlise(attr))

  def addAttr(self,srcid,attr,values):
    for value in values:
      warn("insert(ID_REF="+str(srcid)+",ATTR="+attr+",VALUE="+`value`+")")
      dosql(self.table.conn,
      	    'INSERT DELAYED INTO '+self.table.name
	    +' SET ID_REF=?, ATTR=?, VALUE=?',
	    (srcid,attr,value))

  def setAttr(self,srcid,attr,values):
    self.delAttr(srcid,attr)
    self.addAttr(srcid,attr,values)

  def getAttrNames(self,srcid):
    return [ row[0]
             for row in SQLQuery(self.table.conn,
	     			 'SELECT DISTINCT(ATTR) FROM '+self.table.name
				 +' WHERE ID_REF = '+str(srcid))
	   ]

  def getAttrs(self,srcid):
    attrs={}
    for (attr,value) in SQLQuery(self.table.conn,
	     			 'SELECT ATTR, VALUE FROM '+self.table.name
				 +' WHERE ID_REF = '+str(srcid)):
      if attr not in attrs:
	attrs[attr]=[value]
      else:
	attrs[attr].append(value)

    for attr in attrs:
      if len(attrs[attr]) == 1:
	attrs[attr]=attrs[attr][0]

    return attrs
