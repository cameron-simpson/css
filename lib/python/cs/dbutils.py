#!/usr/bin/python
#
# Classes used for representing relational db things, such as tables and rows.
#

from __future__ import print_function
from collections import namedtuple
from threading import RLock
from cs.py.func import prop
from cs.seq import the
from cs.threads import locked
from cs.logutils import X, debug, info, warning, error, Pfx

class TableSpace(object):

  def __init__(self, table_class=None, lock=None, db_name=None):
    if table_class is None:
      table_class = Table
    if lock is None:
      lock = RLock()
    self.db_name = db_name
    self._tables = {}
    self.table_class = table_class
    self._lock = lock

  def __getattr__(self, attr):
    if not attr.startswith('_'):
      if attr.endswith('s'):
        if '_' not in attr:
          # *s ==> iterable of * (obtained from *_by_id)
          nickname = attr[:-1]
          if nickname in self.table_by_nickname:
            # require the matching table load
            getattr(self, 'load_%ss' % (nickname,))()
            by_id = getattr(self, nickname + '_by_id')
            return lambda: by_id.values()
        # read_*s ==> iterator of rows from table "*"
        if attr.startswith('read_'):
          nickname = attr[5:-1]
          return self.table_by_nickname[nickname].read_rows
      if attr.startswith('load_') and attr.endswith('s'):
        nickname = attr[5:-1]
        if nickname in self.table_by_nickname:
          loaded_attr = '_loaded_table_' + nickname
          loaded = getattr(self, loaded_attr, False)
          if loaded:
            return lambda: None
          else:
            load_funcname = '_load_table_' + nickname + 's'
            ##@locked
            def loadfunc():
              if not getattr(self, loaded_attr, False):
                XP("load %ss (%s)...", nickname, self.table_by_nickname[nickname].qualname)
                getattr(self, load_funcname)()
                setattr(self, loaded_attr, True)
            return loadfunc
      if attr.startswith('select_by_'):
        criterion_words = attr[10:].split('_')
        class_name = 'SelectBy' + '_'.join(word.title() for word in criterion_words)
        return partial(globals()[class_name], self)
      if attr.endswith('_table'):
        # *_table ==> table "*"
        nickname = attr[:-6]
        if nickname in self.table_by_nickname:
          return self.table_by_nickname[nickname]
    raise AttributeError("%s.__getattr__: nothing named %r" % (type(self).__name__, attr,))

  @locked
  def table(self, name):
    ''' Return the Table named `name`.
    '''
    T = self._tables.get(name)
    if T is None:
      T = self._tables[name] = self.table_class(self, name)
    return T

class Table(object):
  ''' Base class for table data.
  '''

  def __init__(self, db, table_name, lock=None, row_class=None, column_names=None, id_column=None):
    if lock is None:
      lock = db._lock
    self.db = db
    self.table_name = table_name
    self.column_names = column_names
    self.id_column = id_column
    self.row_tuple = namedtuple('%s_Row' % (table_name,), column_names)
    self.row_class = row_class
    self._lock = lock

  @prop
  def qual_name(self):
    db_name = self.db.db_name
    return '.'.join( (db_name, self.table_name) )

  @prop
  def conn(self):
    return self.db.conn

  def select(self, where=None, *where_argv):
    ''' Select raw SQL data from the table.
    '''
    sql = 'select %s from %s' % (','.join(self.column_names), self.table_name)
    sqlargs = []
    if where:
      sql += ' where ' + where
      sqlargs.append(where_argv)
    elif where_argv:
      raise ValueError("empty where (%r) but where_argv=%r" % (where, where_argv))
    ##X("SQL: %s %r", sql, sqlargs)
    with Pfx("SQL %r: %r", sql, sqlargs):
      return self.conn.cursor().execute(sql, *sqlargs)

  def read_rows(self, where=None, *where_argv):
    ''' Return row objects.
        This is a generator consuming a SELECT result and must
        therefore be consumed before another query may be performed.
    '''
    row_class = self.row_class
    for row in self.select(where, *where_argv):
      yield row_class(self, row)

  def insert(self, column_names, valueses):
    sql = 'insert into %s(%s) values ' % (self.table_name, ','.join(column_names))
    sqlargs = []
    tuple_param = '(%s)' % ( ','.join( '?' for _ in column_names ), )
    tuple_params = []
    for values in valueses:
      tuple_params.append(tuple_param)
      sqlargs.extend(values)
    sql += ', '.join(tuple_params)
    C = self.conn.cursor()
    X("SQL: %s %r", sql, sqlargs)
    with Pfx("SQL %r: %r", sql, sqlargs):
      C.execute(sql, sqlargs)
    self.conn.commit()
    C.close()

  def update_columns(self, update_columns, update_argv, where, *where_argv):
    sql = 'update %s set %s where %s' \
          % (self.table_name,
             ','.join("%s=?" % (column_name,) for column_name in update_columns),
             where)
    sqlargs = list(update_argv) + list(where_argv)
    C = self.conn.cursor()
    info("SQL: %s %r", sql, sqlargs)
    with Pfx("SQL %r: %r", sql, sqlargs):
      C.execute(sql, sqlargs)
    self.conn.commit()
    C.close()

  def delete(self, where, *where_argv):
    sql = 'delete from %s where %s' % (self.table_name, where)
    sqlargs = where_argv
    C = self.conn.cursor()
    info("SQL: %s %r", sql, sqlargs)
    with Pfx("SQL %r: %r", sql, sqlargs):
      C.execute(sql, sqlargs)
    self.conn.commit()
    C.close()

  def __getitem__(self, id_value):
    ''' Fetch the row or rows indicated by `id_value`.
        If `id_value` is None or a string or is not slicelike or
        is not iterable return the sole matching row or raise
        IndexError.
        Otherwise return an iterable of row values as from read_rows.
    '''
    condition = where_index(self.id_column, in_value)
    rows = self.read_rows(condition.where, *condition.params)
    if condition.is_scalar:
      return the(rows)
    return rows

  def edit_column_by_ids(column_name, ids=None):
    if ids is None:
      where = None
    else:
      where = '%s in (%s)' % (column_name, ','.join("%d" % i for i in ids))
    return self.edit_column(column_name, where)

  def edit_column(column_name, where=None):
    with Pfx("edit_column(%s, %r)", column_name, where):
      id_column = self.id_column
      edit_lines = []
      for row in self.select(where=where):
        edit_line = "%d:%s" % (row[id_column], row[column_name])
        edit_lines.append(edit_line)
      changes = edit_strings(sorted(edit_lines,
                                    key=lambda _: _.split(':', 1)[1]),
                             errors=lambda msg: warning(msg + ', discarded')
                            )
      for old_string, new_string in changes:
        with Pfx("%s => %s", old_string, new_string):
          old_id, old_name = old_string.split(':', 1)
          new_id, new_name = new_string.split(':', 1)
          if old_id != new_id:
            error("id mismatch (%s != %s), discarding change")
          else:
            self[int(new_id)] = new_name
            info("updated")

class Row(object):

  def __init__(self, table, values, lock=None):
    if lock is None:
      lock = table._lock
    self._table = table
    self._row = table.row_tuple(*values)
    self._lock = lock

  @prop
  def db(self):
    return self._table.db

  @prop
  def column_names(self):
    return self._table.column_names

  @prop
  def id_value(self):
    return getattr(self._row, self._table.id_column)

  def __getitem__(self, key):
    if isinstance(key, int):
      # do I really want to support numeric access to columns?
      return self._row[key]
    try:
      return getattr(self._row, key)
    except AttributeError as e:
      raise KeyError("_row has not attribute %r: %s" % (key, e))

  def __getattr__(self, attr):
    if not attr.startswith('_') and attr in self.column_names:
      return getattr(self._row, attr)
    raise AttributeError("%s: no attr %r" % (self.__class__, attr,))

  def __setattr__(self, attr, value):
    if not attr.startswith('_') and attr in self.column_names:
      self._table.update_columns((attr,), (value,), '%s = ?' % (self._table.id_column,), self.id_value)
      self._row = self._row._replace(**{attr: value})
    else:
      self.__dict__[attr] = value

where_index_result = namedtuple(
                        'where_index_result',
                        'is_scalar where params')
def where_index(column, index):
  ''' Return a where clause and any associated parameters for a single column index such as may be accepted by __getitem__.
      Handles integers, strings, slicelike objects and bounded iterables.
      Returns a namedtuple with fields (is_scalar, where, params).
  '''
  try:
    start = index.start
    stop = index.stop
    step = index.step
  except AttributeError:
    # not a slice or slicelike object
    if index is None:
      return where_index_result(True, 'ISNULL(`%s`)' % (column,), ())
    elif isinstance(index, str):
      # strings are scalars
      return where_index_result(True, '`%s` = ?' % (column,), (index,))
    # see if we have an iterable
    try:
      id_values = iter(index)
    except TypeError:
      # not an iterable therefore a scalar
      return where_index_result(True, '`%s` = ?' % (column,), (index,))
  else:
    # a slice
    if stop is None:
      # unbounded slice
      if step is None:
        step = 1
      if step > 0:
        where = '`%s` >= %d' % (column, start)
      else:
        where = '`%s` <= %d' % (column, start)
        step = -step
      if step != 1:
        where += ( ' AND MOD(`%s`, %d) == MOD(%d, %d)'
                 % (column, step, start, step)
                 )
      return where_index_result(False, where, ())
    # convert the slice into a range
    id_values = range(start, stop, step)
  # convert most iterables to a tuple
  if not isinstance(id_values, (list, tuple)):
    id_values = tuple(id_values)
  if len(id_values) == 0:
    where = 'FALSE'
  elif len(id_values) == 1:
    where = '`%s` = ?' % (column,)
  else:
    where = '`%s` IN (%s)' % (column, ', '.join(['?' for _ in id_values]),)
  return where_index_result(False, where, id_values)

def _exercise_where_index():
  for index in ( None, 0, 1,
                 (1,2,3), [1,3,2],
                 slice(0, 8, 2), slice(8, 0, -2),
                 slice(0, None), slice(0, None, 2),
                 slice(8, None, -2)
               ):
    print(repr(index), '=>', where_index('foo', index))
