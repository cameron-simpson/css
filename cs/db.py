#!/usr/bin/python
#
# Assorted database routines and classes.
#	- Cameron Simpson <cs@zip.com.au> 23dec2005
#

import string
import types
import cs.secret
import cs.cache
from cs.misc import debug, ifdebug, warn, isodate, exactlyOne

def today():
  "Today's date in ISO-8601 format (YYYY-MM-DD)."
  return isodate()

def iscurrent(row,when=None,startndx='START_DATE',endndx='END_DATE',inclusive=False):
  """ Test if a row object is ``current''.
      A row is an array of values.
      The optional parameter when defaults to today().
      The optional parameter startndx is the row element index for the inclusive lower bound of currency,
      default 'START_DATE'.
      The optional parameter endndx is the row element index for the exclusive upper bound of currency,
      default 'END_DATE'.
      The optional parameter inclusive, if True, specifies that the upper bound is inclusive
      instead of exclusive (the default).
      Bounds values of None mean no bound.
  """
  if when is None: when=today()
  start=row[startndx]
  if start is not None and start > when:
    return False
  end=row[endndx]
  if end is not None and ((inclusive and end < when) or (not inclusive and end <= when)):
    return False
  return True

def sqlite(filename):
  from pysqlite2 import dbapi2
  return dbapi2.connect(filename)

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

# convert DateTime objects into strings
# trim DateTime strings that are exact days to just the date
# this make naive string comparisons behave well
def datestr(date):
  if date is not None:
    date=str(date)
    if date[-12:] == ' 00:00:00.00':
      date=date[:-12]

  return date

def sqlise(v):
  """ Mark up a value for direct insertion into an SQL statement. """
  if v is None:
    return "NULL"

  t=type(v)
  if t is str:
    # SQL escape quotes
    if v.find("'") >= 0: v=string.join(v.split("'"),"''")
    # double % into %% because the string undergoes python % substitution
    if v.find("%") >= 0: v=string.join(v.split("%"),"%%")
    return "'"+v+"'"

  if t in (types.IntType,types.LongType,types.FloatType):
    return str(v)

  ## FIXME: doesn't work with mysql - no real boolean, and TRUE gets "ERROR 1054: Unknown column 'true' in 'field list'"
  if t is bool:
    if v:
      return 'TRUE'
    return 'FALSE'

  # FIXME: awful hack - throw exception?
  return sqlise(`v`)

# (void) synonym for SQLQuery
def dosql(conn,query,*params):
  SQLQuery(conn,query,*params)
  return None

class SQLQuery:
  """ Iterable SQL query results.
  """
  def __init__(self,conn,query,*params):
    self.__conn=conn
    self.__query=query
    self.__params=params
    self.__cursor=conn.cursor()
    if ifdebug():
      warn('SQLQuery:', query)
      if len(params) > 0: warn("SQLQuery: params =", `params`)
    self.__cursor.execute(query,params)

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

def sqlDatedRecordTest(when=None,startColumn='START_DATE',endColumn='END_DATE'):
  """ Return SQL to test that a dated record overlaps the specified date.
  """
  if when is None: when=today()
  whensql=sqlise(when)
  return '(ISNULL('+startColumn+') OR '+startColumn+' <= '+whensql+')' \
       + ' AND (ISNULL('+endColumn+') OR '+endColumn+' > '+whensql+')'

###############################################################################
# Database Tables
#

__tableCache={}
def getTable(conn,table,keyColumns,allColumns,constraint=None):
  if isinstance(keyColumns,str):
    keyColumns=(keyColumns,)

  cacheKey=(conn,table,keyColumns,allColumns,constraint)

  global __tableCache
  if cacheKey not in __tableCache:
    if len(keyColumns) == 1:
      # present the keys directly
      view=SingleKeyTableView(conn,table,keyColumns[0],allColumns,constraint)
    else:
      # keys in tuples
      view=KeyedTableView(conn,table,keyColumns,allColumns,constraint)

    __tableCache[cacheKey]=view

  return __tableCache[cacheKey]

def getDatedTable(conn,table,keyColumns,allColumns,when=None):
  return getTable(conn,table,keyColumns,allColumns,constraint=sqlDatedRecordTest(when))

##class OldKeyedTableView:
##  """ A view of a table where each key designates a unique row.
##      A key may span multiple table columns.
##      Each row is indexed by a tuple of the key values.
##      If you have a single key column (a common case),
##      use the SingleKeyTableView class,
##      which is a simple subclass of KeyedTableView
##      that presents the keys directly instead of as a single element tuple.
##  """
##
##  def __init__(self,conn,tablename,keyColumns,allColumns,constraint=None,doCache=False):
##    self.conn=conn
##    self.name=tablename
##
##    self.__keyColumns=keyColumns
##
##    self.__allColumns=allColumns
##    self.__sqlColumns=string.join(self.__allColumns,",")	# precompute "col1,col2,..."
##    self.__constraint=constraint
##
##    self.__columnmap={}
##    for i in range(len(allColumns)):
##      self.__columnmap[allColumns[i]]=i
##    self.__keyindices=[self.__columnmap[column] for column in keyColumns]
##
##    self.__preload=None
##
##  def columns(self):
##    return self.__allColumns
##
##  def insert(self,row,sqlised_columns=()):
##    ''' Insert a new row into the table.
##    '''
##    sqlrow={}
##
##    for col in row.keys():
##      v=row[col]
##      if col not in sqlised_columns: v=sqlise(v)
##      sqlrow[col]=v
##
##    self.insertSQLised(sqlrow)
##
##  def insertSQLised(self,row):
##    ''' Insert a new row into the table.
##	The row values are already in SQL syntax.
##    '''
##    columns=row.keys()
##    sql='INSERT INTO '+self.name+'('+string.join(columns,',')+') VALUES ('+string.join([row[k] for k in columns],',')+')'
##    dosql(self.conn,sql)
##
##  def columnMap(self):
##    return self.__columnmap
##
##  def _columnIndex(self,column):
##    return self.__columnmap[column]
##
##  def _selectFields(self):
##    ''' Returns the table view columns, comma separated.
##    '''
##    return self.__sqlColumns
##
##  def rowKey(self,row):
##    ''' Return the key values for the supplied row.
##    '''
##    return tuple([row[i] for i in self.__keyindices])
##
##  def __keyWhereClause(self,key):
##    clause=string.join([self.__keyColumns[i]+" = "+sqlise(key[i]) for i in range(len(key))]," AND ")
##    if self.__constraint:
##      clause=clause+' AND ('+self.__constraint+')'
##    return clause
##
##  def __rowWhereClause(self,row):
##    return self.__keyWhereClause(self.rowKey(row))
##
##  # load up a table
##  def preload(self):
##    self.__preload={}
##    sql='SELECT '+self.__sqlColumns+' FROM '+self.name;
##    if self.__constraint:
##      sql=sql+' WHERE ('+self.__constraint+')'
##    res=SQLQuery(self.conn,sql)
##    for row in res:
##      self.__preload[self.rowKey(row)]=_TableRow(self,self.__rowWhereClause(row),rowdata=row)
##
##  def __preloaded(self,key):
##    if self.__preload is None:
##      return None
##
##    if key not in self.__preload:
##      return None
##
##    return self.__preload[key]
##
##  def findrow(self,where):
##    kfsqlfields=string.join(self.__keyColumns,",")
##    sql='SELECT '+kfsqlfields+' FROM '+self.name+' WHERE '+where
##    rows=SQLQuery(self.conn,sql).allrows()
##    if len(rows) == 0:
##      return None
##    row1=rows[0]
##    id=tuple(row1)
##    if len(rows) > 1:
##      warn("multiple hits in",self.name,"- choosing the first one:",kfsqlfields,'=',`id`)
##    return KeyedTableView.__getitem__(self,id)
##
##  def __getitem__(self,key):
##    row=self.__preloaded(key)
##    if row is None:
##      if self.__preload is None: self.__preload={}
##      row=self.__preload[key]=_TableRow(self,self.__keyWhereClause(key))
##    return row
##
##  def keys(self):
##    if self.__preload is None:
##      self.preload()
##    return self.__preload.keys()
##
##  def __iter__(self):
##    for key in self.keys():
##      yield self[key]
##
##  def __contains__(self,key):
##    if self.__preload is None:
##      self.preload()
##    return key in self.__preload
##  def has_key(self,key):
##    return self.__contains__(key)

class DirectKeyedTableView:
  ''' An uncached view of a table where each key designates a unique row.
      A key may span multiple table columns.
      Each row is indexed by a tuple of the key values.
      If you have a single key column (a common case),
      use the SingleKeyTableView class,
      which is a simple subclass of KeyedTableView
      that presents the keys directly instead of as a single element tuple.
  '''

  def __init__(self,conn,tablename,keyColumns,allColumns,constraint=None):
    self.conn=conn
    self.name=tablename

    self.__keyColumns=tuple(keyColumns)
    self.__sqlKeyColumns=string.join(self.__keyColumns,",")	# precompute "col1,col2,..."
    self.__selectKeys='SELECT '+self.__sqlKeyColumns+' FROM '+self.name

    self.__allColumns=tuple(allColumns)
    self.__constraint=constraint

    self.__sqlColumns=string.join(self.__allColumns,",")	# precompute "col1,col2,..."
    self.__selectRow='SELECT '+self.__sqlColumns+' FROM '+self.name

    self.__columnmap={}
    for i in range(len(allColumns)):
      self.__columnmap[allColumns[i]]=i
    self.__keyindices=tuple([self.__columnmap[column] for column in keyColumns])

  def keys(self):
    sql=self.__selectKeys
    where=self.whereClause()
    if where is not None:
      sql=sql+' WHERE '+where
    return [tuple(row) for row in SQLQuery(self.conn, sql)]

  def __getitem__(self,key):
    return DirectTableRow(self,self.selectRowByKey(key))

  def __setitem__(self,key,value):
    dosql(self.conn,
          'UPDATE '+self.name \
          +' SET '+string.join([ self.__allColumns[i]+' = '+sqlise(value[i])
                                 for i in range(len(value))
                               ], ', ') \
          +' WHERE '+self.whereClause(self.__key2where(key)))

  def __delitem__(self,key):
    self.deleteRows(self.__key2where(key))

  def columns(self):
    return self.__allColumns

  def index2column(self,index):
    ''' Returns a column name from an index (counts from 0).
    '''
    return self.__allColumns[index]

  def columnIndex(self,column):
    return self.__columnmap[column]

  def keyColumns(self):
    return self.__keyColumns

  def rowKey(self,row):
    return tuple([row[self.__columnmap[key]] for key in self.__keyColumns])

  def __key2where(self,key):
    return string.join([self.__allColumns[i]+' = '+sqlise(key[i]) for i in range(len(key))], ' AND ')

  def rowWhere(self,row):
    return self.__key2where(self.rowKey(row))
    
  def whereClause(self,where=None):
    if where is not None:
      if self.__constraint is not None:
        where='('+where+') AND ('+self.__constraint+')'
    elif self.__constraint is not None:
        where=self.__constraint
    return where

  def findrow(self,where):
    rows=self.selectRows(where)
    if len(rows) == 0:
      return None
    if len(rows) > 1:
      warn("multiple hits WHERE", where, "in", self.name, "- choosing the first one:",kfsqlfields,'=',`id`)

    return rows[0]

  def selectRowByKey(self,key):
    return self.selectRows(self.__key2where(key))[0]

  def selectRows(self,where=None):
    where=self.whereClause(where)
    sql=self.__selectRow
    if where is not None:
      sql+=' WHERE '+where

    return [DirectTableRow(self,row) for row in SQLQuery(self.conn,sql)]

  def deleteRows(self,where=None):
    where=self.whereClause(where)
    sql='DELETE FROM '+self.name
    if where is not None:
      sql+=' WHERE '+where
    dosql(self.conn,sql)


  def insert(self,row,sqlised_columns=()):
    """ Insert a new row into the table. """
    sqlrow={}

    for f in row.keys():
      if f in sqlised_columns:
	sqlrow[f]=row[f]
      else:
	sqlrow[f]=sqlise(row[f])

    self.insertSQLised(sqlrow)

  def insertSQLised(self,row):
    """ Insert a new row into the table.
	The row values are already in SQL syntax.
    """
    columns=row.keys()
    sql='INSERT INTO '+self.name+'('+string.join(columns,',')+') VALUES ('+string.join([row[k] for k in columns],',')+')'
    dosql(self.conn,sql)

class DirectTableRow:
  ''' Direct access to a table row.
  '''
  def __init__(self,table,values):
    self.__table=table
    self.key=table.rowKey(values)
    self.__where=table.rowWhere(values)
    self.__values=tuple(values)

  def len(self):
    return len(self.__values)

  def keys(self):
    return self.__table.columns()

  def __repr__(self):
    return '{' \
	 + string.join([ `k`+": "+`self[k]` for k in self.keys() ], ", ") \
	 + '}'

  def __getitem__(self,column):
    if self.__values is None:
      row=self.__table[self.key]
      self.__values=row.__values

    if type(column) is str:
      return self.__values[self.__table.columnIndex(column)]
    return self.__values[column]

  def __setitem__(self,column,value):
    if type(column) is not str:
      column=self.__table.index2column(column)

    dosql(self.__table.conn,'UPDATE '+self.__table.name+' SET '+column+' = '+sqlise(value)+' WHERE '+self.__where)
    self.__values=None

##  def where(self):
##    return self.__table.key2where
##    return self.selectRows(self.__key2where(key))[0]

class KeyedTableView(cs.cache.Cache):
  ''' Caching wrapper for DirectKeyedTableView.
  '''
  def __init__(self,conn,tablename,keyColumns,allColumns,constraint=None):
    self.__direct=DirectKeyedTableView(conn,tablename,keyColumns,allColumns,constraint)
    cs.cache.Cache.__init__(self,self.__direct)
    self.__columnIndices={}

  def preload(self,where=None):
    debug("preload "+self.name)
    for row in self.selectRows(where=where):
      key=self.rowKey(row)
      assert self.name != 'USER_DATES' or key[0] != 315
      self.store(row,key)

  def insert(self,hash):
    self.__direct.insert(hash)
    self.bump()

  def selectRows(self,where=None):
    rows=self.__direct.selectRows(where)
    for row in rows:
      self.store(row, row.key)
    return rows

  def selectRow(self,where):
    return exactlyOne(self.selectRows(where))

  def selectRowsByColumn(self,column,value):
    return self.selectRows(column+" = "+sqlise(value))

  class ByColumn(cs.cache.CrossReference):
    def __init__(self,table,column):
      self.table=table
      self.column=column
      cs.cache.CrossReference.__init__(self)
      self.table.addCrossReference(self)
    def key(self,row):
      return row[self.column]
    def byKey(self,key):
      return self.table.selectRow(self.column+" = "+sqlise(key))

  def addColumnIndex(self,column):
    index=self.__columnIndices[column]=self.ByColumn(self,column)
    self.addCrossReference(index)

  def byColumn(self,column,key):
    return self.__columnIndices[column].find(key)

class SingleKeyTableView(KeyedTableView):
  def __init__(self,conn,tablename,keyColumn,allColumns,constraint=None):
    KeyedTableView.__init__(self,conn,tablename,(keyColumn,),allColumns,constraint)

  def keys(self):
    return [k[0] for k in KeyedTableView.keys(self)]

  def __getitem__(self,key):
    return KeyedTableView.__getitem__(self,(key,))

  def __contains__(self,key):
    return KeyedTableView.__contains__(self,(key,))

  def has_key(self,key):
    return self.__contains__(key)

class DirectRekeyedTableView(cs.cache.Cache):
  def __init__(self,table,keyFields):
    self.table=table
    self.keyFields=tuple(keyFields)

  def __getitem__(self,key):
    where=string.join(" AND ",[self.keyFields[i]+" = "+sqlise(key[i]) for i in range(len(self.keyFields))])
    rows=self.table.selectRows(where)
    if len(rows) == 0:
      raise IndexError, "no entries WHERE "+where
    if len(rows) > 1:
      raise IndexError, "multiple entries WHERE "+where+": "+strlist(rows)
    return rows[0]

class RekeyedTableView(cs.cache.Cache):
  def __init__(self,table,keyFields):
    self.__direct=DirectRekeyedTableView(table,keyFields)
    cs.cache.Cache.__init__(self,self.__direct)

###############################################################################
# Database Table Rows
#

class TableRowWrapper:
  def __init__(self,tableview,key):
    self.TableRow=tableview[key]

  def keys(self):
    return self.TableRow.keys()

  def __getitem__(self,column):
    return self.TableRow[column]

  def __setitem__(self,column,value):
    self.TableRow[column]=value

##class _TableRow:
##  def __init__(self,table,whereclause,rowdata=None):
##    assert false, "OBSOLETE _TableRow"
##    self.table=table
##    self.__whereclause=whereclause
##    self.__rowcache=None
##    if rowdata is not None:
##      self.__setrowcache(rowdata)
##
##  def keys(self):
##    return self.table.columns()
##
##  def __repr__(self):
##    return '[' \
##	 + string.join([ `k`+": "+`self[k]` for k in self.keys() ], ", ") \
##	 + ']'
##
##  def whereClause(self):
##    return self.__whereclause
##
##  def __setrowcache(self,rowdata):
##    # stash copy of the supplied data
##    self.__rowcache=[d for d in rowdata]
##
##  def __loadrowcache(self):
##    self.__setrowcache(SQLQuery(self.table.conn,
##			        'SELECT '+self.table._selectFields()+' FROM '+self.table.name+' WHERE '+self.__whereclause+' LIMIT 1',
##			       ).allrows()[0])
##
##  def __getrowcache(self):
##    if self.__rowcache is None:
##      ## FIXME - caching for dated tables!
##      self.__loadrowcache()
##    return self.__rowcache
##
##  def __keys__(self):
##    return keys(self.__getrowcache())
##
##  def __getitem__(self,column):
##    # fetch from cache, loading it if necessary
##    return self.__getrowcache()[self.table._columnIndex(column)]
##
##  def __setitem__(self,column,value):
##    # update the db
##    dosql(self.table.conn,
##	  'UPDATE '+self.table.name+' SET '+column+' = '+sqlise(value)+' WHERE '+self.__whereclause)
##    # update the cache
##    self.__getrowcache()[self.table._columnIndex(column)]=value
