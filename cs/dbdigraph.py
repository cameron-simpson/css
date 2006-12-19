import string
import re
from cs.misc import cmderr, debug, warn, die, uniq, exactlyOne, all
from cs.hier import flavour, T_SEQ, T_MAP
from cs.db import dosql, SQLQuery, sqlise, today
from cs.lex import strlist
import cs.cache

NodeCoreAttributes=('NAME','TYPE')

def SQLtestType(*types):
  assert len(types) > 0
  if len(types) == 1:
    return "TYPE = "+sqlise(types[0])
  return "TYPE IN ("+string.join([ sqlise(type) for type in types ], ",")+")"

def testType(t, *types):
  if type(t) is not str: t=t.TYPE
  return t in types

CapWord_re = re.compile(r'^[A-Z][A-Z_]*$')

def testAllCaps(s):
  return CapWord_re.match(s)

def createDB(conn,nodes='NODES',attrs='ATTRS'):
  dosql(conn, 'CREATE TABLE '+nodes+' (ID autoincrement, NAME varchar(32), TYPE varchar(16));')
  dosql(conn, 'CREATE TABLE '+attrs+' (ID autoincrement, ID_REF unsigned, ATTR varchar(64), VALUE varchar(255), IS_IDREF tinyint(1));')

class DBDiGraph:
  def __init__(self,nodeTable,attrTable):
    self.nodes=nodeTable
    self.attrs=AttrTable(self,attrTable)
    self.__nodeCache={}
    self.__selectIdsBaseQuery='SELECT DISTINCT(ID) FROM '+self.nodes.name
    self.__soleUser=False
    self.__lastid=None
    self.__typeMap={}
    self.__preloaded=False

  def preload(self):
    self.nodes.preload()
    self.attrs.preload()
    map=self.__typeMap
    for id in self.keys():
      node=self[id]
      key=(node.NAME,node.TYPE)
      if key in map:
        map[key].append(node)
      else:
        map[key]=[node]
    self.__preloaded=True

  def keys(self):
    return self.nodes.keys()

  def createNode(self,name,type,multipleNamesOk=False):
    ''' Creates a new node with the specified name and type.
        Returns the ID of the new node.
    '''
    if name is not None and not multipleNamesOk and self.nodeByNameAndType(name,type):
      die("createNode(name="+str(name)+", type="+type+"): already exists")

    if not self.__soleUser or self.__lastid is None:
      newId=[row[0] for row in SQLQuery(self.nodes.conn,'SELECT MAX(ID) FROM NODES')][0]
      if newId is None: newId=0
    else:
      newId=self.__lastid

    newId+=1
    self.__lastid=newId

    self.nodes.insert({'ID': newId, 'NAME': name, 'TYPE': type});
    return self[newId]

  def setSoleUser(self,state):
    self.__soleUser=state

  def addAnonNode(self):
    return self.createNode(None,'')

  def _newNode(self,nodeid):
    ''' Constructs a new node instance associated with an id.
        To be overridden by subclasses.
    '''
    return DBDiGraphNode(nodeid,self)

  def __getitem__(self,nodeid):
    if type(nodeid) is int or type(nodeid) is long:
      if nodeid not in self.__nodeCache:
        self.__nodeCache[nodeid]=self._newNode(nodeid)
      return self.__nodeCache[nodeid]

    if type(nodeid) is tuple:
      return exactlyOne(self.nodesByNameAndType(nodeid[0],*nodeid[1:]), `nodeid`)

    raise IndexError, "invalid index "+`nodeid`

  def __delitem__(self,nodeid):
    refids=self[nodeid].referringNodeids()
    warn("refids =", `refids`)
    if len(refids) > 0:
      node=self[nodeid]
      raise IndexError, "node "+str(node)+" has referring nodes: "+strlist([self[id] for id in refids])

    del self.attrs[nodeid]
    del self.nodes[nodeid]

  def need(self,name,type):
    nodes=self.nodesByNameAndType(name,type)
    if len(nodes) > 1:
      raise IndexError, "multiple nodes with index "+`(name,type)`+": "+strlist(nodes)
    if len(nodes) == 0:
      node=self.createNode(name,type)
    else:
      node=nodes[0]
    return node

  def nodesFromNames(self,*names):
    for name in names:
      if name[0] == "@":
        for node in self.nodesByType(name[1:]):
          yield node
      else:
        yield self[name]

  def nodesWhere(self,where):
    return [self[row[0]] for row in self.nodes.selectRows(where)]

  def nodesFromIds(self,nodeids):
    if len(nodeids) == 0:
      return ()
    if len(nodeids) == 1:
      return (self[nodeids[0]],)
    return self.nodesWhere("ID IN ("+strlist(nodeids)+")")

  def nodesByType(self,*types):
    return nodesWhere(SQLtestType(*types))

  def nodesByNameAndType(self,name,*types):
    if len(types) == 1:
      key=(name,types[0])
      if key in self.__typeMap:
        return tuple(self.__typeMap[key])

    return self.nodesWhere('NAME = '+sqlise(name)+' AND '+SQLtestType(*types))

  def _nodeByNameAndType(self,name,*types):
    return exactlyOne(self.nodesByNameAndType(name,*types), `(name,types)`)

  def nodeByNameAndType(self,name,*types):
    try:
      node=self._nodeByNameAndType(name,*types)
    except IndexError:
      return None

    return node

  def nodesLike(self,like,types=(),where=None):
    likewhere="NAME LIKE "+sqlise(like)
    if len(types) > 0:
      likewhere=likewhere+" AND "+SQLtestType(*types)
    if where is not None:
      likewhere=likewhere+" AND ("+where+")"
    return self.nodesWhere(likewhere)

  def nodesByAttr(self,attr,value):
    nodeids=uniq([row['ID_REF'] for row in self.attrs.table.selectRows("ATTR = "+sqlise(attr)+" AND VALUE = "+sqlise(str(value)))])
    return self.nodesFromIds(nodeids)

  def nodesByType(self,*types):
    return self.nodesWhere(SQLtestType(*types))

class DBDiGraphNode:
  def __init__(self,id,digraph):
    self.id=id
    self.digraph=digraph

  def clone(self,pruneFields=()):
    ''' Returns a shallow clone of a node.
    '''
    N={}
    for k in self.keys():
      if k not in pruneFields:
        N[k]=self[k]
    for k in NodeCoreAttributes:
      if k not in pruneFields:
        N[k]=self[k]
    return N

  def keys(self):
    return self.digraph.attrs[self.id].keys()

  def attrs(self):
    return uniq(NodeCoreAttributes+self.digraph.attrs.getAttrs(self.id))

  def __str__(self):
    ''' Returns "NAME(TYPE#id)".
    '''
    label=self.TYPE+"#"+str(self.id)
    if self.NAME is not None:
      label=self.NAME+"("+label+")"

    return label

  def __getattr__(self,attr):
    if attr in NodeCoreAttributes:
      return self.digraph.nodes[self.id][attr]
    if testAllCaps(attr):
      return self.__fieldAccess(attr)
    raise AttributeError, "node "+str(self)+" has no attribute named "+attr

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

  def each(self,key):
    ''' Generator yielding each attribute value.
    '''
    for v in self.getAttr(key):
      yield v

  def all(self,key):
    return all(self.each(key))

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
    del self.digraph.attrs[self.id][key]

  def delete(self):
    del self.digraph[self.id]

  def setAttr(self,attr,values):
    self.digraph.attrs[self.id][attr]=values

  def addAttr(self,attr,values):
    self.digraph.attrs.addAttr(self.id,attr,values)

  def delAttr(self,attr,value=None):
    self.digraph.attrs[self.id].delValue(attr,value)

  def getAttr(self,attr):
    ##warn(str(self)+": getAttr("+`attr`+")")
    if type(attr) is not str:
      raise IndexError, "non-string attr: "+`attr`
    values=self.digraph.attrs[self.id]
    return values[attr]

  def testFlag(self,flagname,flagField='FLAGS'):
    return flagname in self.each(flagField)

  def setFlag(self,flagname,flagField='FLAGS'):
    if not testFlag(self,flagname,flagField=flagField):
      self.addAttr(flagField,(flagname,))

  def clearFlag(self,flagname,flagField='FLAGS'):
    if testFlag(self,flagname,flagField=flagField):
      self.delAttr(flagField,flagname)

  def referringNodeids(self):
    ''' Returns a list of node ids which refer to this node.
    '''
    return [row[0] for row in
            SQLQuery(self.digraph.attrs.table.conn,
                     'SELECT DISTINCT(ID_REF+0) FROM '+self.digraph.attrs.table.name \
                     +' WHERE IS_IDREF AND VALUE = '+sqlise(str(self.id)))]

  def edges(self,upstream=False):
    ''' Returns edges running from this node.
    '''
    if upstream:
      return self.digraph.nodesByAttr('EDGES',self.id)

    return self.all('EDGES')

  def detach(self):
    assert self.TYPE == 'EDGE', "not an EDGE: "+str(self)

    # detach upstream
    dosql(self.digraph.attrs.table.conn,
          "DELETE FROM "+self.digraph.attrs.table.name+" WHERE IS_IDREF AND ATTR = 'EDGES' AND VALUE = "+sqlise(self.id))
    self.digraph.attrs.table.bump()

    if self.NAME is None:
      # anonymous edge - discard the whole thing
      self.delete()
    else:
      # just detach downstream
      del self['EDGES']

  def connectedNodes(self,upstream=False):
    nodes=[]
    for edge in self.edges(upstream=upstream):
      if edge.TYPE == 'EDGE':
        nodes.extend(edge.connectedNodes(upstream=upstream))
      else:
        nodes.append(edge)
    
    return nodes

  def addEdge(self,toNode,edge=None):
    if edge is None:
      # an anonymous edge
      edge=self.digraph.addAnonNode()
      edge.TYPE='EDGE'
    elif type(edge) is str:
      edge=self.digraph.createNode(edge,'EDGE')

    ##warn("addEdge "+str(self)+"->"+str(toNode),"via",str(edge))
    self.addAttr('EDGES',edge)
    edge.addAttr('EDGES',toNode)

  def delEdge(self,edge):
    self.delAttr('EDGES',edge.id)
    if edge.TYPE == 'EDGE':
      edge.detach()

class AttrTable(cs.cache.Cache):
  def __init__(self,digraph,table):
    self.__direct=DirectAttrTable(digraph,table)
    cs.cache.Cache.__init__(self,self.__direct)
    self.__preloaded=False

  def preload(self):
    self.preloadForNodeids()
    self.__preloaded=True

  def preloadForNodeids(self,nodeids=None):
    if self.__preloaded:
      return

    where=None
    if nodeids is not None:
      if len(nodeids) == 0:
        return
      where="ID_REF IN ("+strlist(nodeids)+")"

    NV={}
    for row in self.table.selectRows(where):
      id=row.key
      nodeid=row['ID_REF']
      if nodeid not in NV:
        nv=NV[nodeid]={}
      else:
        nv=NV[nodeid]
      attr=row['ATTR']
      v=(row['VALUE'],row['IS_IDREF'])
      if attr in nv:
        nv[attr].append(v)
      else:
        nv[attr]=[v]

    for nodeid in NV:
      self.store(AttrSet(self.__direct,nodeid,NV[nodeid]),nodeid)

  def attrsFromNodeids(self,nodeids):
    ''' Returns AttrSets for the nodes specified in nodeids.
    '''
    attrs=[]
    missing=[nodeid for nodeid in nodeids if not self.__cache.inCache(nodeid)]
    self.preloadForNodeids(missing)
    return [self[nodeid] for nodeid in nodeids]

  def nodesWhere(self,where):
    return self.digraph.nodesFromIds([ row['ID_REF'] for row in self.table.selectRows(where) ])

class DirectAttrTable:
  def __init__(self,digraph,table):
    self.digraph=digraph
    self.table=table

  def addAttr(self,nodeid,attr,values):
    if flavour(values) != T_SEQ:
      values=(values,)
    AttrSet(self,nodeid).addValues(attr,values)

  def __delitem__(self,nodeid):
    AttrSet(self,nodeid).deleteAll()

  def __getitem__(self,nodeid):
    return AttrSet(self,nodeid)

  def __setitem__(self,nodeid,newattrs):
    A=AttrSet(self,nodeid)
    A.deleteAll()
    for k in newattrs.keys():
      A[k]=newattrs[k]

  def insert(self,row):
    self.table.insert(row)

class AttrSet:
  def __init__(self,attrs,nodeid,values=None):
    self.attrs=attrs
    self.nodeid=nodeid
    self.__values=values

  def values(self):
    if self.__values is None:
      A={}
      for row in self.attrs.table.selectRows('ID_REF = '+sqlise(self.nodeid)):
        key=row['ATTR']
        value=(row['VALUE'],row['IS_IDREF'])
        if key in A:
          A[key].append(value)
        else:
          A[key]=[value]
      self.__values=A

    return self.__values

  def keys(self):
    return self.values().keys()

  def deleteAll(self):
    import inspect
    for frame in inspect.stack():
      warn("stack:", frame[1]+":"+str(frame[2]))
    self.attrs.table.deleteRows('ID_REF = '+sqlise(self.nodeid))
    self.__values={}

  def __getitem__(self,key):
    scalars=[]
    A=self.values()
    if key not in A:
      return ()

    values=A[key]
    for v in values:
      if v[1] == 0:
        scalars.append(v[0])
      else:
        scalars.append(self.attrs.digraph[int(v[0])])

    return tuple(scalars)

  def __setitem__(self,key,values):
    self.attrs.table.deleteRows('ID_REF = '+sqlise(self.nodeid)+' AND ATTR = '+sqlise(key))
    self.values()[key]=[]
    self.addValues(key,values)

  def __delitem__(self,key):
    del self.values()[key]
    self.attrs.table.deleteRows('ID_REF = '+sqlise(self.nodeid)+' AND ATTR = '+sqlise(key))

  def delValue(self,key,value=None):
    if value is None:
      del self[key]
    else:
      self.attrs.table.deleteRows('ID_REF = '+sqlise(self.nodeid)+' AND ATTR = '+sqlise(key)+' AND VALUE = '+sqlise(value))

  def addValues(self,key,values):
    A=self.values()
    if key not in A:
      A[key]=[]

    if flavour(values) != T_SEQ:
      values=(values,)

    for value in values:
      if flavour(value) != T_MAP:
        is_idref=0
      else:
        is_idref=1
        if isinstance(value,DBDiGraphNode) and self.attrs.digraph is value.digraph:
          value=value.id
        else:
          hash=self.attrs.digraph.addAnonNode()
          for k in value.keys():
            hash[k]=value[k]
          value=hash.id

        value=str(value)

      self.attrs.insert({ 'ID_REF': self.nodeid,
                          'ATTR': key,
                          'VALUE': value,
                          'IS_IDREF': is_idref});
      A[key].append((value,is_idref))
