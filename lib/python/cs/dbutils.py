#!/usr/bin/python
#
# Classes used for representing relational db things, such as tables and rows.
#

from __future__ import print_function
from collections import namedtuple, defaultdict
from functools import partial
from threading import RLock
from cs.py.func import prop
from cs.seq import the
from cs.threads import locked
from cs.logutils import X, debug, info, warning, error
from cs.pfx import Pfx, XP

class Params(object):
  ''' A manager for query parameters.
  '''

  def __init__(self, style):
    ''' Initialise the parameter manager.
        `style`: the TableSpace paramater style:
          '?': use '?' as the placeholder
          '%s': use '%s' as the placeholder
          TODO:
          '$n': PostgreSQL style numbered parameters.
          ':name_n': MySQL style :name_n numbered and named parameters.
    '''
    self.style = style
    self.counts = defaultdict(int)
    self.params = []
    self.values = []

  def add(self, name, value):
    ''' Add a value with a name basis; return the parameter placeholder.
    '''
    if self.style in ('?', '%s'):
      self.params.append('?')
      self.values.append(value)
    else:
      raise RuntimeError("style %r not implemented" % (self.style,))
    return self.params[-1]

  def vadd(self, name, values):
    ''' Add multiple values with a common name basis; return the parameter placeholders.
    '''
    params = []
    for value in values:
      params.append(self.add(name, value))
    return params

  def map(self):
    ''' Return a mapping of parameter string to value.
        Only useful for named paramater styles eg ":foo9".
    '''
    return dict(zip(self.params, self.values))

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
        # *_rows ==> list of rows from table
        if attr.endswith('_rows'):
          nickname = attr[:-5]
          T = self.table_by_nickname[nickname]
          return T.rows()
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

  def __init__(self, db, table_name, lock=None, row_class=None, column_names=None, id_column=None, name_column=None):
    ''' Initialise a new Table.
        `db`: the database (TableSpace) containing this Table
        `table_name`: the Table's name
        `lock`: optional Lock; if omitted or None use `db._lock`
        `row_class`: factory to construct a Row from a query result
        `column_names`: Table column names
        `id_column`: the Table primary key column
        `name_column`: optional Table name column, contains an identifying string
    '''
    if lock is None:
      lock = db._lock
    self.db = db
    self.table_name = table_name
    self.column_names = column_names
    self.id_column = id_column
    self.name_column = name_column
    self.row_tuple = namedtuple('%s_Row' % (table_name,), column_names)
    self.row_class = row_class
    self._lock = lock

  def __str__(self):
    return "%s:name=%s" % (self.__class__.__name__, self.table_name)

  def __repr__(self):
    return "%s[%s]" % (self, ','.join(self._column_names))

  def __iter__(self):
    ''' Return an iterator of all the rows as row_class instances.
    '''
    return iter(self.rows())

  def rows(self, where=None, *where_argv):
    ''' Return a list of row_class instances.
    '''
    row_class = self.row_class
    return list(row_class(self, row) for row in self.select(where, *where_argv))

  def rows_by_value(self, column_names, *values):
    if isinstance(column_names, str):
      column_names = (column_names,)
    if len(column_names) != len(values):
      raise ValueError("%d column_names vs %d values"
                       % (len(column_names), len(values)))
    P = Params(self.db.param_style)
    conditions = []
    for column_name, value in zip(column_names, values):
      if isinstance(value, (list, tuple)):
        conditions.append('`%s` in (%s)'
                          % (column_name, ','.join(P.vadd(column_name, value))))
      else:
        conditions.append('%s = %s' % (column_name, P.add(column_name, value)))
    where_clause = ' AND '.join(conditions)
    return self.rows(where_clause, *P.values)

  @prop
  def qual_name(self):
    db_name = self.db.db_name
    return '.'.join( (db_name, self.table_name) )

  @prop
  def conn(self):
    return self.db.conn

  def select(self, where=None, *where_argv):
    ''' Select raw SQL data from the table.
        It is generally better to use .rows instead, which returns typed rows.
    '''
    sql = 'select %s from %s' % (','.join(self.column_names), self.table_name)
    sqlargs = []
    if where:
      sql += ' where ' + where
      sqlargs.append(where_argv)
    elif where_argv:
      raise ValueError("empty where (%r) but where_argv=%r" % (where, where_argv))
    with Pfx("SQL %r: %r", sql, sqlargs):
      return self.conn.cursor().execute(sql, *sqlargs)

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

  def named_row(self, name, fuzzy=False):
    if self.name_column is None:
      raise RuntimeError("%s: no name_column" % (self,))
    rows = self.rows('`%s` = %%s' % (self.name_column,), name)
    if len(rows) == 1:
      return rows[0]
    if fuzzy:
      name_stripped = name.strip()
      rows = self.rows('trim(`%s`) = %%s' % (self.name_column,), name_stripped)
      if len(rows) == 1:
        return rows[0]
      name_lc = name_stripped.lower()
      rows = self.rows('lower(trim(`%s`)) = %%s' % (self.name_column,), name_lc)
      if len(rows) == 1:
        return rows[0]
    raise KeyError("%s: no row named %r" % (self, name))

  def __getitem__(self, id_value):
    ''' Fetch the row or rows indicated by `id_value`.
        If `id_value` is None or a string or is not slicelike or
        is not iterable return the sole matching row or raise
        IndexError.
        Otherwise return a list of row_class instances.
    '''
    if isinstance(id_value, str):
      return self.named_row(id_value, fuzzy=True)
    condition = where_index(self.id_column, id_value)
    rows = self.rows(condition.where, *condition.params)
    if condition.is_scalar:
      return the(rows)
    return rows

  def __setitem__(self, id_value, new_name):
    if self.name_column is None:
      raise RuntimeError("%s: no name_colum" % (self,))
    row = self[id_value]
    row.name = new_name

  def edit_column_by_ids(self, column_name, ids=None):
    if ids is None:
      where = None
    else:
      where = '%s in (%s)' % (column_name, ','.join("%d" % i for i in ids))
    return self.edit_column(column_name, where)

  def edit_column(self, column_name, where=None):
    with Pfx("edit_column(%s, %r)", column_name, where):
      id_column = self.id_column
      edit_lines = []
      for row in self.select(where=where):
        edit_line = "%d:%s" % (row[id_column], row[column_name])
        edit_lines.append(edit_line)
      changes = self.edit_strings(sorted(edit_lines,
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

  def __str__(self):
    return "<%s>%s:%s" % (self.__class__.__name__, self._table.table_name, self._row)
  __repr__ = __str__

  def __iter__(self):
    return iter(self._row)

  def __len__(self):
    return len(self._row)

  def __lt__(self, other):
    table = self._table
    cmp_column = table.name_column
    if cmp_column is None:
      cmp_column = table.id_column
    return self[cmp_column] < other[cmp_column]

  def keys(self):
    return self.column_names

  def values(self):
    return self._row

  def items(self):
    return zip(self.column_names, self.values())

  @prop
  def db(self):
    return self._table.db

  @prop
  def column_names(self):
    return self._table.column_names

  @prop
  def id_value(self):
    return getattr(self._row, self._table.id_column)

  @prop
  def name(self):
    name_column = self._table.name_column
    if name_column is None:
      raise RuntimeError("%s: no name_column" % (self,))
    return self[name_column]

  @name.setter
  def name(self, new_name):
    name_column = self._table.name_column
    if name_column is None:
      raise RuntimeError("%s: no name_column" % (self,))
    self[name_column] = new_name

  def __getitem__(self, key):
    ''' Direct access to row values by column name or index.
    '''
    if isinstance(key, int):
      # do I really want to support numeric access to columns?
      return self._row[key]
    try:
      return getattr(self._row, key)
    except AttributeError as e:
      raise KeyError("_row has no attribute %r: %s" % (key, e))

  def __setitem__(self, key, value):
    ''' Direct access to row values by column name or index.
    '''
    if isinstance(key, int):
      # do I really want to support numeric access to columns?
      key = self._table.column_names[key]
    self._table.update_columns((key,),
                               (value,),
                               '`%s` = ?' % (self._table.id_column,),
                               self.id_value)
    self._row = self._row._replace(**{key: value})

  def __getattr__(self, attr):
    if not attr.startswith('_') and attr in self.column_names:
      return getattr(self._row, attr)
    raise AttributeError("%s: no attr %r" % (self.__class__, attr,))

  def __setattr__(self, attr, value):
    if not attr.startswith('_') and attr in self.column_names:
      self[attr] = value
    else:
      self.__dict__[attr] = value

_IdRelation = namedtuple('IdRelation',
                         'id_column relation left_column left right_column right')

class IdRelation(_IdRelation):
  ''' Manage a relationship between 2 Tables based on their id_columns.
      Initialised with:
      `id_column`: id column for the relation Table
      `relation`: the relation Table
      `left_column`: relation Table column containing the id for the left Table
      `left`: the left Table
      `right_column`: relation Table column containing the id for the right Table
      `right`: the right Table
  '''

  def left_to_right(self, left_ids):
    ''' Fetch right rows given a pythonic index into left.
    '''
    condition = where_index(self.left_column, left_ids)
    rel_rows = self.relation.rows(condition.where, *condition.params)
    right_ids = set( [ rel[self.right_column] for rel in rel_rows ] )
    return self.right[right_ids]

  def right_to_left(self, right_ids):
    ''' Fetch left rows given a pythonic index into right.
    '''
    condition = where_index(self.right_column, right_ids)
    rel_rows = self.relation.rows(condition.where, *condition.params)
    left_ids = set( [ rel[self.left_column] for rel in rel_rows ] )
    return self.left[left_ids]

  def add(self, left_id, right_id):
    self.relation.insert( (self.left_column, self.right_column),
                          [ (left_id, right_id) ] )

  def remove(self, left_id, right_id):
    self.relation.delete(
      '%s = ? and %s = ?' % (self.left_column, self.right_column),
      left_id, right_id)

  def remove_left(self, left_ids):
    ''' Remove all relation rows with the specified left_column values.
    '''
    condition = where_index(self.left_column, left_ids)
    return self.left.delete(condition.where, *condition.params)

  def remove_right(self, right_ids):
    ''' Remove all relation rows with the specified right_column values.
    '''
    condition = where_index(self.right_column, right_ids)
    return self.right.delete(condition.where, *condition.params)

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
    where = '1=2'
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
