#!/usr/bin/python
#
# Assorted database routines and classes.
#	- Cameron Simpson <cs@zip.com.au> 23dec2005
#

import string
import types
import cs.misc
warn=cs.misc.warn

def today():
  "Today's date in ISO-8601 format (YYYY-MM-DD)."
  return time.strftime("%Y-%m-%d",time.localtime())

def iscurrent(row,when=None,startndx='START_DATE',endndx='END_DATE'):
  """ Test if a row object is ``current''.
      A row is an array of values.
      The optional parameter when defaults to today().
      The optional startndx is the row element index for the inclusive lower bound of currency,
      default 'START_DATE'.
      The optional endndx is the row element index for the exclusive upper bound of currency,
      default 'END_DATE'.
      Bounds values of None mean no bound.
  """
  if when is None: when=today()
  ##warn "ROW =", `row`, "when =", when
  start=row[startndx]
  if start is not None and start > when:
    return False
  end=row[endndx]
  if end is not None and end <= when:
    return False
  return True

# attach to a MySQL database, return normal python db handle
def mysql(secret,db=None):
  """ Attach to a MySQL database, return normal python db handle.
      Secret is either a dict after the style of cs.secret, or a secret name.
  """
  import MySQLdb

  if secret is None:
    global MySQLServer
    return MySQLdb.connect(host=host,db=db,user=None,passwd=None)

  if type(secret) is types.StringType or not(hasattr(secret,'__keys__') or hasattr(secret,'keys')):
    # transmute secret name into structure
    secret=cs.secret.get(secret)

  return MySQLdb.connect(host=secret['HOST'],
			 db=db,
			 user=secret['LOGIN'],
			 passwd=secret['PASSWORD'])

_cache_dbpool={}
def dbpool(secret,db):
  """ Cache for sharing database connections for a specific secret and db name. """
  global _cache_dbpool
  if secret not in _cache_dbpool:
    _cache_dbpool[secret]={}

  if db not in _cache_dbpool[secret]:
    _cache_dbpool[secret][db]=conn=mysql(secret,db)
  else:
    conn=_cache_dbpool[secret][db]

  return conn

def sqlise(v):
  """ Mark up a value for direct insertion into an SQL statement. """
  if v is None:
    return "NULL"

  t=type(v)
  if t is types.StringType:
    return "'"+v+"'"

  if t in (types.IntType,types.LongType,types.FloatType):
    return str(v)

  # FIXME: awful hack - throw exception?
  return sqlise(`v`)

# (void) synonym for SQLQuery
def dosql(conn,query,*args):
  SQLQuery(conn,query,*args)
  return None

class SQLQuery:
  """ Iterable SQL query results.
  """
  def __init__(self,conn,query,*args):
    self.__conn=conn
    self.__query=query
    self.__args=args
    ##warn("conn =", `conn`)
    self.__cursor=conn.cursor()
    ##warn('SQLQuery:', query)
    ##warn("SQLQuery: args =", `args`)
    self.__cursor.execute(query,*args)
    ##warn("executed")

  def allrows(self):
    return [row for row in self]

  def __iter__(self):
    return self

  def next(self):
    row=self.__cursor.fetchone()
    if row is None: raise StopIteration
    return row

class DateRangeRecord:
  def iscurrent(self,when=None):
    return iscurrent(self,when)

def sqlDatedRecordTest(whensql='CURDATE()',startfield='START_DATE',endfield='END_DATE'):
  return '(ISNULL('+startfield+') OR '+startfield+' <= '+whensql+')' \
       + ' AND (ISNULL('+endfield+') OR '+endfield+' > '+whensql+')'

###############################################################################
# Database Tables
#

__tableCache={}
def getTable(conn,table,keyfields,fieldlist,constraint=None):
  if isinstance(keyfields,str):
    keyfields=(keyfields,)

  global __tableCache
  if conn not in __tableCache:
    __tableCache[conn]={}

  views=__tableCache[conn]
  viewname=table+'['+string.join(keyfields,',')+']('+string.join(fieldlist,',')+')'
  if constraint is not None:
    viewname=viewname+' WHERE '+constraint

  if viewname not in views:
    views[viewname]=KeyedTableView(conn,table,keyfields,fieldlist,constraint)

  return views[viewname]

def getDatedTable(conn,table,keyfields,fieldlist,whensql='CURDATE()'):
  return getTable(conn,table,keyfields,fieldlist,constraint=sqlDatedRecordTest(whensql))

class KeyedTableView:

  def __init__(self,conn,tablename,keyfields,fieldlist,constraint=None):
    if isinstance(keyfields,str):
      keyfields=(keyfields,)

    self.conn=conn
    self.name=tablename

    self.__keyfields=keyfields
    self.__fieldlist=fieldlist
    self.__sqlfields=string.join(self.__fieldlist,",")	# precompute
    self.__constraint=constraint

    self.__fieldmap={}
    for i in range(len(fieldlist)):
      self.__fieldmap[fieldlist[i]]=i
    self.__keyindices=[self.__fieldmap[field] for field in keyfields]

    self.__preload=None

  def insert(self,row,sqlised_fields=()):
    sqlrow={}

    for f in row.keys():
      if f in sqlised_fields:
	sqlrow[f]=row[f]
      else:
	sqlrow[f]=sqlise(row[f])

    self.insertSQLised(sqlrow)

  def insertSQLised(self,row):
    fields=row.keys()
    sql='INSERT INTO '+self.name+'('+string.join(fields,',')+') VALUES ('+string.join([row[k] for k in fields],',')+')'
    warn("dosql(self.conn,"+sql+")")
    dosql(self.conn,sql)

  def columnIndex(self,column):
    return self.__fieldmap[column]

  def selectFields(self):
    return self.__sqlfields

  def fieldMap(self):
    return self.__fieldmap

  def rowkey(self,row):
    return tuple([row[i] for i in self.__keyindices])

  def keyWhereClause(self,key):
    ##warn("key =", `key`)
    clause=string.join([self.__keyfields[i]+" = "+sqlise(key[i]) for i in range(len(key))]," AND ")
    if self.__constraint:
      clause=clause+' AND ('+self.__constraint+')'
    return clause

  def rowWhereClause(self,row):
    return self.keyWhereClause(self.rowkey(row))

  # load up a table
  def preload(self):
    self.__preload={}
    sql='SELECT '+self.__sqlfields+' FROM '+self.name;
    if self.__constraint:
      sql=sql+' WHERE ('+self.__constraint+')'
    warn("SQL ",sql)
    res=SQLQuery(self.conn,sql)
    warn("result =", `res`)
    for row in res:
      self.__preload[self.rowkey(row)]=TableRow(self,self.rowWhereClause(row),rowdata=row)

  def __preloaded(self,key):
    if self.__preload is None:
      ##warn("table."+self.name+": not preloaded")
      return None

    if key not in self.__preload:
      ##warn("table."+self.name+": key", key, "not in preload")
      return None

    ##warn("__preload[", `key`, "] =", `self.__preload[key]`)
    return self.__preload[key]

  def findrow(self,where):
    sql='SELECT '+self.__keyfield+' FROM '+self.name+' WHERE '+where
    rows=dosql(self.conn,sql).allrows()
    if len(rows) == 0:
      return None
    id=rows[0][0]
    if len(rows) > 1:
      warn("multiple hits in",self.name,"- selecting first one:",self.__keyfield,'=',`id`)
    return self[id]

  def __getitem__(self,key):
    ##t=type(key); warn("type =", `t`)
    if isinstance(key,str) or isinstance(key,int) or isinstance(key,long) or isinstance(key,float):
      key=(key,)

    row=self.__preloaded(key)
    if row is None:
      if self.__preload is None: self.__preload={}
      row=self.__preload[key]=TableRow(self,self.keyWhereClause(key))
    return row

  def keys(self):
    if self.__preload is None:
      self.preload()

    return self.__preload.keys()

  def __contains__(self,key):
    if self.__preload is None:
	  self.preload()

    return key in self.__preload

  def has_key(self,key):
    return self.__contains__(key)

  class __SingleKeyTableViewIter:
    def __init__(self,tv):
      self.tv=tv
      self.keys=tv.keys()
      self.n=0
    def __iter__(self):
      return self
    def next(self):
      n=self.n
      keys=self.keys
      if n >= len(keys): raise StopIteration
      row=self.tv[keys[n]]
      self.n=n+1
      return row

  def __iter__(self):
    return self.__SingleKeyTableViewIter(self)

###############################################################################
# Database Table Rows
#

class TableRow:
  def __init__(self,table,whereclause,rowdata=None):
    self.table=table

    self.__whereclause=whereclause
    ##wc=`whereclause`
    ##warn("where(", table.name, ") =", wc)
    ##if wc[0] == '<': raise StopIteration

    self.__rowcache=None
    if rowdata is not None:
      self.__setrowcache(rowdata)

  def whereClause(self):
    return self.__whereclause

  def __setrowcache(self,rowdata):
    # stash copy of the supplied data
    self.__rowcache=[d for d in rowdata]

  def __loadrowcache(self):
    ##for arg in ('SELECT ', self.table.selectFields(), ' FROM ', self.table.name, ' WHERE ', self.__whereclause, ' LIMIT 1'):
    ##  warn("arg =", `arg`)
    self.__setrowcache([row for row in SQLQuery(self.table.conn,
					     'SELECT '+self.table.selectFields()+' FROM '+self.table.name+' WHERE '+self.__whereclause+' LIMIT 1',
					    ).allrows()[0]])

  def __getrowcache(self):
    if self.__rowcache is None:
      ## FIXME - caching for dated tables!
      ##warn("no __rowcache for WHERE", self.__whereclause)
      self.__loadrowcache()
    return self.__rowcache

  def __keys__(self):
    return keys(self.__getrowcache())

  def __getitem__(self,column):
    # fetch from cache, loading it if necessary
    return self.__getrowcache()[self.table.columnIndex(column)]

  def __setitem__(self,column,value):
    # update the db
    dosql(self.table.conn,
	  'UPDATE '+self.table.name+' SET '+column+' = %s WHERE '+self.__whereclause,
	  (value,))
    # update the cache
    self.__getrowcache()[self.table.columnIndex(column)]=value

class TableRowWrapper:
  def __init__(self,tv,key):
    self.TableRow=tv[key]

  def __getitem__(self,column):
    return self.TableRow[column]

  def __setitem__(self,column,value):
    self.TableRow[column]=value
