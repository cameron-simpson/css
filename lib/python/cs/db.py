#!/usr/bin/python
#
# Assorted database routines and classes.
#       - Cameron Simpson <cs@zip.com.au> 23dec2005
#
# OBSOLETE: I pretty much use SQLalchemy for all this these days.
#

from __future__ import with_statement
import sys
import string
import types
import datetime
if sys.hexversion < 0x02060000: from sets import Set as set
import cs.secret
import cs.cache
from cs.logutils import error, warning
from cs.misc import isodate, the, WithUC_Attrs
from threading import Lock

def today():
  return datetime.date.today()

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

class ConnWrapper:
  def __init__(self,getConn,*args):
    self.getConn=getConn
    self.getConnArgs=args
    self.lock=Lock()
    with self.lock:
      self.conn=getConn(*args)
  def attachConn(self):
    self.conn=self.getConn(*self.getConnArgs)
    return self.conn
  def __getattr__(self,attr):
    return getattr(self.conn,attr)

_warned_MYSQL_NO_UTF8=False

# attach to a MySQL database, return normal python db handle
def mysql(secret,db=None):
  """ Attach to a MySQL database, return normal python db handle.
      Secret is either a dict after the style of cs.secret, or a secret name.
  """
  if secret is None:
    raise IndexError
    import MySQLdb
    global MySQLServer
    conn=MySQLdb.connect(host=MySQLServer,db=db,user=None,passwd=None)
  else:
    conn=cs.secret.mysql(secret,db=db)

  if hasattr(conn,"set_character_set"):
    conn.set_character_set('utf8')
  else:
    global _warned_MYSQL_NO_UTF8
    if not _warned_MYSQL_NO_UTF8:
      warning("mysql: no UTF8 support")
      _warned_MYSQL_NO_UTF8=True

  return conn

def mssql(secret,db=None):
  return cs.secret.mssql(secret,db=db)

_cache_dbpool={}
def dbpool(secret,dbname,doConn=None):
  """ Cache for sharing database connections for a specific secret and db name.
      Returns a ConnWrapper, whose .conn field is a db connection.
      ConnWrapper.attachConn() can be called to try to reattach the connection.
  """
  if doConn is None:
    doConn=mysql
  global _cache_dbpool
  if (secret,dbname) not in _cache_dbpool:
    _cache_dbpool[secret,dbname]=ConnWrapper(doConn,secret,dbname)

  return _cache_dbpool[secret,dbname]

# allow an environment variable of the form [db].[table] to override a db,table pair
def dbtablenames(envvar,dbname,tablename):
  import os
  if envvar in os.environ:
    dbt=os.environ[envvar]
    cpos=dbt.find('.')
    if cpos >= 0:
      ed, et = dbt.split(':',1)
      if len(ed): dbname=ed
      if len(et): tablename=et
    else:
      dbname=dbt
  return (dbname,tablename)

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

  # turn datetime.date into str
  t=type(v)
  if t is datetime.date:
    v=str(v)
    t=type(v)
  elif t is set:
    v=" ".join(v)
    t=type(v)

  if t is str or t is unicode:
    # SQL escape quotes
    if v.find("'") >= 0: v="''".join(v.split("'"))
    # double % into %% because the string undergoes python % substitution
    if v.find("%") >= 0: v="%%".join(v.split("%"))
    return "'"+v+"'"

  if t in (types.IntType,types.LongType,types.FloatType):
    return str(v)

  ## FIXME: doesn't work with mysql - no real boolean, and TRUE gets "ERROR 1054: Unknown column 'true' in 'field list'"
  if t is bool:
    if v:
      return 'TRUE'
    return 'FALSE'

  # FIXME: awful hack - throw exception?
  return sqlise(repr(v))

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
    debug('SQLQuery:', query)
    if len(params) > 0:
      debug("SQLQuery: params =", repr(params))

    with conn.lock:
      self.__cursor=conn.conn.cursor()
      try:
        self.__cursor.execute(query,params)
      except:
        error("SQL failure for: %s (params=%s)", query, params)
        warning("\tRetry with new db connection...")
        conn.attachConn()
        self.__cursor=conn.conn.cursor()
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

def mergeDatedRecords(table,keyFields,idField=None,constraint=None,cropOverlaps=False,doit=False):
  for sql in mergeDatedRecordsSQL(table,keyFields,idField=idField,constraint=constraint,cropOverlaps=cropOverlaps):
    if doit:
      table.dosql(sql)
    else:
      print sql

def mergeDatedRecordsSQL(table,keyFields,idField=None,constraint=None,cropOverlaps=False):
  ''' mergeDatedRecordsSQL() is a generator that yields SQL statements to
      tidy up cruft in DatedRecord tables. It is passed a TableView
      reference, a list of fields that consitute a comparison key, and
      idField that should unqiuely identify a row (default 'ID'),
      an optional SQL constraint and an operation mode (cropOverlaps, default
      False).

      When cropOverlaps is False, overlapping identical records, as
      determined from the keyFields tuple, are merged.

      When cropOverlaps is True, if two records overlap then the earlier
      record has its END_DATE cropped so as not to overlap the later
      record.

      A DatedRecord table has a date range expressed by the fields START_DATE
      and END_DATE and one or more value fields that apply during the date
      range. The first usage mode (cropOverlaps == False) is used to merge
      identical records; keyFields should include START_DATE, END_DATE and
      the complete set of value fields. The second usage mode (cropOverlaps
      == True) is used to tidy up overlapping values; keyFields should
      inclide START_DATE, END_DATE and value fields that identify the scope
      of the record (such as a reference to an entity to which the other
      values apply) but _not_ value fields that are "just values".
  '''
  if type(keyFields) is str:
    keyFields=(keyFields,)
  if idField is None:
    idField='ID'

  oldRows={}
  for row in table.selectRows(where=constraint, modifiers='ORDER BY START_DATE, END_DATE, '+idField):
    start, end = row.START_DATE, row.END_DATE
    if start is not None and end is not None and start >= end:
      print repr(row)
      print "HUH - DELETE RECORD WITH EMPTY DATE RANGE"
      yield 'DELETE FROM %s WHERE %s = %s' % (table.name, idField, sqlise(row[idField]))
      continue

    key=tuple(row[f] for f in keyFields)
    if key not in oldRows:
      # new row key - note for later
      oldRows[key]=row
      continue

    oldrow=oldRows[key]
    if start is not None \
    and oldrow.END_DATE is not None \
    and oldrow.END_DATE <= start:
      # no overlap; update "old" to be latest row
      oldRows[key]=row
      continue

    # identical overlap
    if cropOverlaps:
      if start is None \
      or oldrow.END_DATE is None \
      or oldrow.END_DATE > start:
        # crop earlier record to start of later record
        if start is None:
          warning("warning: START_DATE is NULL, can't fix overlap\n\tOLD: %s\n\tNEW: %s", oldrow, row)
        else:
          yield 'UPDATE %s SET END_DATE = %s WHERE %s = %s' \
                 % (table.name, sqlise(start), idField, sqlise(oldrow[idField]))
    else:
      # advance END_DATE in old, toss new
      if oldrow.END_DATE is not None \
      and (end is None or end > oldrow['END_DATE']):
        yield 'UPDATE %s SET END_DATE = %s WHERE %s = %s' \
               % (table.name, sqlise(end), idField, sqlise(oldrow[idField]))
      yield 'DELETE FROM %s WHERE %s = %s' \
            % (table.name, idField, sqlise(row[idField]))

###############################################################################
# Database Tables
#

__tableCache={}
def getTable(conn,table,keyColumns,allColumns,constraint=None):
  ''' Return a SingleKeyTableView or KeyedTableView from
      a cache of open tables, opening a new one as needed.
  '''
  if isinstance(keyColumns,str):
    keyColumns=(keyColumns,)
  elif not isinstance(keyColumns,tuple):
    keyColumns=tuple(keyColumns)

  if not isinstance(allColumns,tuple):
    allColumns=tuple(allColumns)

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
    self.__sqlKeyColumns=','.join(self.__keyColumns)    # precompute "col1,col2,..."
    self.__selectKeys='SELECT '+self.__sqlKeyColumns+' FROM '+self.name

    self.__allColumns=tuple(allColumns)
    self.__constraint=constraint

    self.__sqlColumns=','.join(self.__allColumns)       # precompute "col1,col2,..."
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

  def __iter__(self):
    for k in self.keys():
      yield self[k]

  def __getitem__(self,key):
    return DirectTableRow(self,self.selectRowByKey(key))

  def getitems(self,keylist):
    ''' SELECT multiple table rows matching an arbitrary list of single-value keys.
    '''
    assert len(self.__keyColumns) == 1, \
      "getitems("+repr(keylist)+") on multikey table "+self.name+"["+",".join(self.__keyColumns)+"]"
    return self.selectRows("%s IN (%s)" \
                           % (self.__keyColumns[0],
                              ",".join(sqlise(k) for k in keylist)))

  def __setitem__(self,key,value):
    self.dosql(
          "UPDATE %s SET %s WHERE %s"
          % (", ".join(self.__allColumns[i]+' = '+sqlise(value[i]) for i in range(len(value))),
             self.whereClause(self.__key2where(key))))

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

  def constraint(self):
    return self.__constraint

  def rowKey(self,row):
    return tuple([row[self.__columnmap[key]] for key in self.__keyColumns])

  def __key2where(self,key):
    if type(key) is not tuple:
      key=(key,)
    if type(key[0]) is tuple:
      raise IndexError, "key is tuple of tuple: %r" % (key,)
    return " AND ".join(self.__allColumns[i]+' = '+sqlise(key[i]) for i in range(len(key)))

  def rowWhere(self,row):
    return self.__key2where(self.rowKey(row))

  def whereClause(self,where=None):
    if where is not None:
      if self.__constraint is not None:
        where="(%s) AND (%s)" % (where, self.__constraint)
    elif self.__constraint is not None:
        where=self.__constraint
    return where

  def findrowByKey(self,key):
    return self.findrow(self.__key2where(key))

  def findrow(self,where):
    rows=self.selectRows(where)
    if len(rows) == 0:
      return None
    if len(rows) > 1:
      warning("multiple hits WHERE", where, "in", self.name, "- choosing the first one:",repr(rows[0]))

    return rows[0]

  def selectRowByKey(self,key):
    return self.selectRows(self.__key2where(key))[0]

  def dosql(self,sql):
    return SQLQuery(self.conn,sql)

  def selectRows(self,where=None,modifiers=None):
    where=self.whereClause(where=where)
    sql=self.__selectRow
    if where is not None:
      sql+=' WHERE '+where
    if modifiers is not None:
      sql+=' '+modifiers

    return [DirectTableRow(self,row) for row in self.dosql(sql)]

  def deleteRows(self,where=None):
    where=self.whereClause(where)
    sql='DELETE FROM '+self.name
    if where is not None:
      sql+=' WHERE '+where
    self.dosql(sql)


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
    sql='INSERT INTO %s(%s) VALUES (%s)' \
        % (self.name,
           ",".join(columns),
           ",".join([row[k] for k in columns]))
    dosql(self.conn,sql)

class DirectTableRow(WithUC_Attrs):
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

  def flush(self):
    self.__values=None

  def __repr__(self):
    return '{' \
         + ', '.join([ repr(k)+": "+repr(self[k]) for k in self.keys() ]) \
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

class KeyedTableView(cs.cache.Cache):
  ''' Caching wrapper for DirectKeyedTableView.
  '''
  def __init__(self,conn,tablename,keyColumns,allColumns,constraint=None):
    self.__direct=DirectKeyedTableView(conn,tablename,keyColumns,allColumns,constraint)
    cs.cache.Cache.__init__(self,self.__direct)
    self.__columnIndices={}

  def __getattr__(self,attr):
    return getattr(self.__direct,attr)

  def _rawTable(self):
    return self.__direct

  def bump(self):
    cs.cache.Cache.bump(self)
    for colname in self.__columnIndices.keys():
      self.__columnIndices[colname].flush()

  def preload(self,where=None):
    debug("preload "+self.name)
    for row in self.selectRows(where=where):
      key=self.rowKey(row)
      self.store(row,key)
    self.preloaded()

  def insert(self,hash):
    self.__direct.insert(hash)
    self.bump()

  def selectRows(self,where=None,modifiers=None):
    rows=self.__direct.selectRows(where=where,modifiers=modifiers)
    for row in rows:
      self.store(row, row.key)
    return rows

  def selectRow(self,where):
    return the(self.selectRows(where))

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

  def findrowByKey(self,key):
    return cs.cache.Cache.findrowByKey(self,key)

class SingleKeyTableView(KeyedTableView):
  def __init__(self,conn,tablename,keyColumn,allColumns,constraint=None):
    KeyedTableView.__init__(self,conn,tablename,(keyColumn,),allColumns,constraint)

  def key(self):
    return self.keyColumns()[0]

  def keys(self):
    return [k[0] for k in KeyedTableView.keys(self)]

  def __iter__(self):
    for k in self.keys():
      yield self[k]

  def __getitem__(self,key):
    return KeyedTableView.__getitem__(self,(key,))

  def __contains__(self,key):
    return KeyedTableView.__contains__(self,(key,))

  def has_key(self,key):
    return self.__contains__(key)

  def findrowByKey(self,key):
    return KeyedTableView.findrowByKey(self,(key,))

class DirectRekeyedTableView(cs.cache.Cache):
  def __init__(self,table,keyFields):
    self.table=table
    self.keyFields=tuple(keyFields)

  def __getitem__(self,key):
    where=' AND '.join([self.keyFields[i]+" = "+sqlise(key[i]) for i in range(len(self.keyFields))])
    rows=self.table.selectRows(where)
    if len(rows) == 0:
      raise IndexError, "no entries WHERE "+where
    if len(rows) > 1:
      raise IndexError, "multiple entries WHERE "+where+": "+",".join(rows)
    return rows[0]

class KeyedTableSubView(KeyedTableView):
  def __init__(self,superTable,constraint):
    raw=superTable._rawTable()
    if rawConstraint:
      constraint="(%) AND (%)" % (rawConstraint, constraint)
    KeyedTableView.__init__(self,raw.conn,raw.name,raw.keyColumns(),raw.columns(),constraint)

class RekeyedTableView(cs.cache.Cache):
  def __init__(self,table,keyFields):
    self.__direct=DirectRekeyedTableView(table,keyFields)
    cs.cache.Cache.__init__(self,self.__direct)

###############################################################################
# Database Table Rows
#

class NoSuchRowError(IndexError):
  ''' Thrown if the row cannot be found.
  '''

class TableRowWrapper(WithUC_Attrs):
  def __init__(self,tableview,key):
    self.TableView=tableview
    try:
      self.TableRow=tableview[key]
    except IndexError, e:
      raise NoSuchRowError("no row with id "+str(id)+": "+repr(e))

  def table(self):
    return self.TableView

  def keys(self):
    return self.TableRow.keys()

  def flush(self):
    self.TableRow.flush()

  def __getattr__(self,attr):
    return getattr(self.TableRow,attr)

  def __getitem__(self,column):
    return self.TableRow[column]

  def __setitem__(self,column,value):
    self.TableRow[column]=value
