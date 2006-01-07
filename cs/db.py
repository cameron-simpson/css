#!/usr/bin/python
#
# Assorted database routines and classes.
#	- Cameron Simpson <cs@zip.com.au> 23dec2005
#

import string
import types
import cs.secret
from cs.misc import warn, isodate

def today():
  "Today's date in ISO-8601 format (YYYY-MM-DD)."
  return isodate()

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
def dbpool(secret,dbname):
  """ Cache for sharing database connections for a specific secret and db name. """
  global _cache_dbpool
  if (secret,dbname) not in _cache_dbpool:
    _cache_dbpool[secret,dbname]=mysql(secret,dbname)

  return _cache_dbpool[secret,dbname]

def sqlise(v):
  """ Mark up a value for direct insertion into an SQL statement. """
  if v is None:
    return "NULL"

  t=type(v)
  if t is types.StringType:
    if v.find("'") >= 0: v=string.join(v.split("'"),"''")
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
    row=self.__cursor.fetchone()
    while row is not None:
      yield row
      row=self.__cursor.fetchone()

class DateRangeRecord:
  def iscurrent(self,when=None):
    return iscurrent(self,when)

def sqlDatedRecordTest(when=today(),startfield='START_DATE',endfield='END_DATE'):
  """ Return SQL to test that a dated record overlaps the specified date. """
  whensql=sqlise(when)
  return '(ISNULL('+startfield+') OR '+startfield+' <= '+whensql+')' \
       + ' AND (ISNULL('+endfield+') OR '+endfield+' > '+whensql+')'

###############################################################################
# Database Tables
#

__tableCache={}
def getTable(conn,table,keyfields,fieldlist,constraint=None):
  if isinstance(keyfields,str):
    keyfields=(keyfields,)

  cacheKey=(conn,table,keyfields,fieldlist,constraint)

  global __tableCache
  if cacheKey not in __tableCache:
    if len(keyfields) == 1:
      # present the keys directly
      view=SingleKeyTableView(conn,table,keyfields[0],fieldlist,constraint)
    else:
      # keys in tuples
      view=KeyedTableView(conn,table,keyfields,fieldlist,constraint)

    __tableCache[cacheKey]=view

  return __tableCache[cacheKey]

def getDatedTable(conn,table,keyfields,fieldlist,when=today()):
  return getTable(conn,table,keyfields,fieldlist,constraint=sqlDatedRecordTest(when))

class KeyedTableView:
  """ A view of a table where each key designated a unique row.
      A key may span multiple table columns.
      Each row is indexed by a tuple of the key values.
      If you have a single key column (a common case),
      use the SingleKeyTableView class,
      which is a simple subclass of KeyedTableView
      that presents the keys directly instead of as a single element tuple.
  """

  def __init__(self,conn,tablename,keyfields,fieldlist,constraint=None):
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
    """ Insert a new row into the table. """
    sqlrow={}

    for f in row.keys():
      if f in sqlised_fields:
	sqlrow[f]=row[f]
      else:
	sqlrow[f]=sqlise(row[f])

    self.insertSQLised(sqlrow)

  def insertSQLised(self,row):
    """ Insert a new row into the table.
	The row values are already in SQL syntax.
    """
    fields=row.keys()
    sql='INSERT INTO '+self.name+'('+string.join(fields,',')+') VALUES ('+string.join([row[k] for k in fields],',')+')'
    dosql(self.conn,sql)

  def fieldMap(self):
    return self.__fieldmap

  def _columnIndex(self,column):
    return self.__fieldmap[column]

  def _selectFields(self):
    """ Return the table view fields, comma separated. """
    return self.__sqlfields

  def __rowkey(self,row):
    """ Return the key values for the supplied row. """
    return tuple([row[i] for i in self.__keyindices])

  def __keyWhereClause(self,key):
    ##warn("key =", `key`)
    clause=string.join([self.__keyfields[i]+" = "+sqlise(key[i]) for i in range(len(key))]," AND ")
    if self.__constraint:
      clause=clause+' AND ('+self.__constraint+')'
    return clause

  def __rowWhereClause(self,row):
    return self.__keyWhereClause(self.__rowkey(row))

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
      self.__preload[self.__rowkey(row)]=_TableRow(self,self.__rowWhereClause(row),rowdata=row)

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
    kfsqlfields=string.join(self.__keyfields,",")
    sql='SELECT '+kfsqlfields+' FROM '+self.name+' WHERE '+where
    rows=SQLQuery(self.conn,sql).allrows()
    if len(rows) == 0:
      return None
    id=tuple(rows[0])
    if len(rows) > 1:
      warn("multiple hits in",self.name,"- selecting first one:",kfsqlfields,'=',`id`)
    return self[id]

  def __getitem__(self,key):
    row=self.__preloaded(key)
    if row is None:
      if self.__preload is None: self.__preload={}
      row=self.__preload[key]=_TableRow(self,self.__keyWhereClause(key))
    return row

  def keys(self):
    if self.__preload is None:
      self.preload()
    return self.__preload.keys()

  def __iter__(self):
    for key in self.keys():
      yield self[key]

  def __contains__(self,key):
    if self.__preload is None:
      self.preload()
    return key in self.__preload
  def has_key(self,key):
    return self.__contains__(key)

class SingleKeyTableView(KeyedTableView):
  def __init__(self,conn,tablename,keyfield,fieldlist,constraint=None):
    KeyedTableView.__init__(self,conn,tablename,(keyfield,),fieldlist,constraint)

  def keys(self):
    return [k[0] for k in KeyedTableView.keys(self)]

  def __getitem__(self,key):
    return KeyedTableView.__getitem__(self,(key,))

  def __contains__(self,key):
    return KeyedTableView.__contains__(self,(key,))

  def has_key(self,key):
    return self.__contains__(key)

###############################################################################
# Database Table Rows
#

class _TableRow:
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
    ##for arg in ('SELECT ', self.table._selectFields(), ' FROM ', self.table.name, ' WHERE ', self.__whereclause, ' LIMIT 1'):
    ##  warn("arg =", `arg`)
    self.__setrowcache([row for row in SQLQuery(self.table.conn,
					     'SELECT '+self.table._selectFields()+' FROM '+self.table.name+' WHERE '+self.__whereclause+' LIMIT 1',
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
    return self.__getrowcache()[self.table._columnIndex(column)]

  def __setitem__(self,column,value):
    # update the db
    dosql(self.table.conn,
	  'UPDATE '+self.table.name+' SET '+column+' = %s WHERE '+self.__whereclause,
	  (value,))
    # update the cache
    self.__getrowcache()[self.table._columnIndex(column)]=value

class TableRowWrapper:
  def __init__(self,tableview,key):
    self.TableRow=tableview[key]

  def __getitem__(self,column):
    return self.TableRow[column]

  def __setitem__(self,column,value):
    self.TableRow[column]=value
