#!/usr/bin/python

import cs.misc
import os
import string

def cursorColumns(c):
  names=[]
  for meta in c.description:
    names.append(meta[0])
  return names

def fieldTestTplt(f):
  return f+" = %s"

def doSql(cursor,sql,params=()):
  print "SQL=", sql,
  print "PARAMS=", `params`
  cursor.execute(sql,params)

def selectFields(fields):
  return "SELECT "+string.join(fields,", ")

def mysqlConn(db,systemid=os.environ['SYSTEMID']):
  import cs.dbi.mysql
  return cs.dbi.mysql.conn(db,systemid)

""" tuple (or scalar if just one value) that may be used as a dictionary key
"""
def valueKey(values):
  if len(values) == 1:
    return values[0]
  return eval( "("+string.join([`v` for v in values], ", ")+")" )

""" an iterator for the results of a selection
"""
class Selection(cs.misc.HasNameIndex):
  def __init__(self,dbconn,sel,params=()):
    if string.upper(sel[0:7]) != "SELECT ": sel="SELECT "+sel
    self.__cursor=dbconn.newCursor()
    doSql(self.__cursor,sel,params)
    self.initNameIndex(cursorColumns(self.__cursor))

  def getCursor(self):
    return self.__cursor

  def __iter__(self):
    rowvalues=self.__cursor.fetchone()
    while rowvalues != None:
      yield rowvalues
      rowvalues=self.__cursor.fetchone()

class View(cs.misc.HasNameIndex):
  def __init__(self,dbconn,selection,where="",wparams=()):
    self.dbconn=dbconn
    self.selection="SELECT "+selection
    self.where=where
    self.wparams=wparams

  def newCursor(self):
    return self.dbconn.newCursor()

  # returns a Selection
  def selectWhere(self,extra_where="",wparams=()):
    sel=self.withWhere(self.selection,extra_where)
    selobj=Selection(self.dbconn,sel,self.wparams+wparams)
    self.initNameIndex(selobj.getNames())
    return selobj

  def selectEqual(self,fields,values):
    if not hasattr(values,'__getitem__'): values=(values,)
    return self.selectWhere(string.join([ f+"=%s" for f in fields ], " AND "),values)

  def withWhere(self,sql,extra_where=""):
    # compose condition
    wh=self.where
    if len(extra_where) > 0:
      if len(wh) > 0: wh="("+wh+") AND ("+extra_where+")"
      else:           wh=extra_where
    if len(wh) > 0:
      sql=sql+" WHERE "+wh
    return sql

  # method to return new Row object for subclassing
  def newRow(self,rowvalues):
    return Row(self,rowvalues)

  """ Given a complete row of values and a list of desired columns
      return a list of the chosen column values
  """
  def chooseRowValues(self,rowvalues,fields):
    return [ rowvalues[self.lookupNameIndex(f)] for f in fields ]
      
  # return a sequence of Rows specified by the where clause
  def fetch(self,extra_where="",*args):
    return [ self.newRow(rowvalues) for rowvalues in self.selectWhere(extra_where,*args) ]

class Row(cs.misc.IndexedSeqWrapper):
  def __init__(self,view,values):
    cs.misc.IndexedSeqWrapper.__init__(self,values,view.getNames())
    self.setView(view)
  def setView(self,view):
    self.__view=view
  def getView(self):
    return self.__view
  def __setitem__(self,key,value):
    raise TypeError, "change to anonymous row can't affect database, rejected"
  def __delitem__(self,key):
    raise TypeError, "deletion from anonymous row can't affect database, rejected"

""" A View indexable by identifying fields, and thus updateable.
"""
class UpdateableTableView(View):
  def __init__(self,dbconn,table,idfields,otherfields=None,where="",wparams=()):
    self.__keys=None
    self.__liveRows={}
    self.setTable(table)
    self.setIdFields(idfields)

    if otherfields is None:	selfields="*"
    else:			selfields=string.join(idfields+otherfields,", ")
    View.__init__(self,dbconn,selfields+" FROM "+table, where,wparams)

  def setTable(self,table):
    self.__table=table
  def getTable(self):
    return self.__table

  def getFieldNum(self,fieldname):
    return self.lookupNameIndex(fieldname)

  def setIdFields(self,idfields):
    self.__idfields=idfields
  def getIdFields(self):
    return self.__idfields

  def isIdField(self,field):
    return field in self.getIdFields()
  def isIdFieldNum(self,fieldnum):
    isidfield = self.getNames()[fieldnum] in self.getIdFields()
    print "isIdField(", fieldnum, ")=", `isidfield`
    return isidfield

  """ tuple of id field values for this row
      or simple scalar if just one id field
  """
  def __rowIdentity(self,rowvalues):
    print "rowvalues=", `rowvalues`
    print "len = ", len(rowvalues)
    return valueKey(self.chooseRowValues(rowvalues,self.getIdFields()))

  def __iter__(self):
    selobj=Selection(self.dbconn,
    		     "SELECT "+string.join(self.getIdFields(),", ")
		    +" FROM "+self.getTable())
    for selrow in selobj:
      vk=valueKey(selrow)
      yield vk

  def keys(self):
    if self.__keys is None:
      self.__keys=[ key for key in self.__iter__() ]
    return self.__keys

  def __cachedItem(self,key):
    # cached, return it
    if key in self.__liveRows:
      return self.__liveRows[key]
    return None

  def __cacheItem(self,key,row):
    self.__liveRows[key]=row

  # method to return new Row object for subclassing
  def newRow(self,rowvalues):
    return IdentifiableRow(self,rowvalues)

  def __getitem__(self,key):
    row=self.__cachedItem(key)
    if row is not None: return row

    # obtain the record from the db
    selobj=self.selectEqual(self.getIdFields(),key)
    rowvv=[ rowvalues for rowvalues in selobj ]
    rowvalues=rowvv[0]

    row=self.newRow(rowvalues)
    self.__cacheItem(key,row)
    return row

  def __setitem__(self,key,value):
    print "SETITEM"
    row=self.__getitem(key)
    # not a dictionary?
    # must be complete row - don't screw up!
    if not hasattr(value,'keys'):
      list
    row._updateAllValues(value)

class IdentifiableRow(Row):
  def __init__(self,view,values):
    Row.__init__(self,view,[ v for v in values ])

  def getTable(self):
    return self.getView().getTable()

  """ return WHERE condition and values to match
  """
  def getIdentity(self):
    return \
      [ string.join( [f+" = %s" for f in self.getView().getIdFields()],
		     " AND ")
      ] \
      + [self[f] for f in self.getView().getIdFields()]

  def __setitem__(self,key,value):
    self.update({key: value})

  def update(self,dict):
    fields=self.getNameIndex().keys()
    for key in dict.keys():
      if key not in fields:
	raise TypeError, "unknown column named \""+key+"\""
      if self.getView().isIdField(key):
	raise TypeError, "can't set id field \""+key+"\""

    sql="UPDATE "+self.getTable()+" SET "+string.join([ field+"=%s" for field in dict.keys() ],", ")
    sqlparams=[ dict[k] for k in dict.keys() ]

    idsql=self.getIdentity()
    sql=sql+" WHERE "+idsql[0]
    sqlparams+=idsql[1:]

    ##print "SQL: ", sql
    ##print "     ", `sqlparams`
    cursor=self.getView().newCursor()
    doSql(cursor,sql,sqlparams)
    for k in dict.keys():
      cs.misc.IndexedSeqWrapper.__setitem__(self,k,dict[k])
