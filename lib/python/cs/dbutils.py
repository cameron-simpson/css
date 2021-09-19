#!/usr/bin/python
#
# Classes used for representing relational db things, such as tables and rows.
#

''' Facilities for related SQL database tables.
'''

from __future__ import print_function
from collections import namedtuple, defaultdict
from functools import partial
from threading import RLock
from cs.py.func import prop
from cs.seq import the
from cs.threads import locked
from cs.logutils import info, warning, error
from cs.pfx import Pfx, XP
from cs.x import X

class Params(object):
  ''' A manager for query parameters.
  '''

  def __init__(self, style):
    ''' Initialise the parameter manager.

        Parameters:
        * `style`: the TableSpace parameter style:
          * '?': use '?' as the placeholder
          * '%s': use '%s' as the placeholder

        TODO:
        * '$n': PostgreSQL style numbered parameters.
        * ':name_n': MySQL style :name_n numbered and named parameters.
    '''
    self.style = style
    self.counts = defaultdict(int)
    self.params = []
    self.values = []

  def __len__(self):
    ''' The length of a Params is the number of parameters.
    '''
    return len(self.params)

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
        Only useful for named parameter styles eg ":foo9".
    '''
    return dict(zip(self.params, self.values))

class TableSpace(object):
  ''' A table space, containing various named Tables.
  '''

  def __init__(self, table_class=None, lock=None, db_name=None):
    if table_class is None:
      table_class = Table
    if lock is None:
      lock = RLock()
    self.db_name = db_name
    self._tables = {}
    self.table_by_nickname = {}
    self.default_table_class = table_class
    self._lock = lock

  def new_params(self):
    ''' Return a new Params for a query.
    '''
    return Params(self.param_style)

  def __getattr__(self, attr):
    ''' Fetch a virtual attribute.
    '''
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
        class_name = 'SelectBy' + '_'.join(
            word.title() for word in criterion_words)
        return partial(globals()[class_name], self)
      if attr.endswith('_table'):
        # *_table ==> table "*"
        nickname = attr[:-6]
        if nickname in self.table_by_nickname:
          return self.table_by_nickname[nickname]
    raise AttributeError(
        "%s.__getattr__: nothing named %r"
        % (type(self).__name__, attr,))

  @locked
  def table(self, name, table_class=None, row_class=None):
    ''' Return the Table named `name`.
    '''
    if table_class is None:
      table_class = self.default_table_class
    T = self._tables.get(name)
    if T is None:
      T = self._tables[name] = table_class(self, name, row_class=row_class)
    return T

class Table(object):
  ''' Base class for table data.
  '''

  def __init__(
      self,
      db, table_name,
      lock=None, row_class=None,
      column_names=None, id_column=None, name_column=None,
  ):
    ''' Initialise a new Table:
        * `db`: the database (TableSpace) containing this Table
        * `table_name`: the Table's name
        * `lock`: optional Lock; if omitted or None use `db._lock`
        * `row_class`: factory to construct a Row from a query result
        * `column_names`: Table column names
        * `id_column`: the Table primary key column
        * `name_column`: optional Table name column, contains an
          identifying string
    '''
    if lock is None:
      lock = db._lock
    if row_class is None:
      row_class = Row
    self.db = db
    self.table_name = table_name
    self.column_names = tuple(column_names)
    self.id_column = id_column
    self.id_index = column_names.index(id_column) if id_column else None
    self.name_column = name_column
    self.row_tuple_class = namedtuple(table_name.title() + 'RowTuple', column_names)
    self.row_class = row_class
    self._row_cache = {}            # id => row
    self._row_cache_unique = {}     # ((k1,v1),(k2,v2),...) => row
    self.relations = {}
    self._lock = lock

  def __str__(self):
    return "%s:name=%s" % (self.__class__.__name__, self.table_name)

  def __repr__(self):
    return "%s[%s]" % (self, ','.join(self.column_names))

  def new_params(self):
    ''' Return a new Params for a query.
    '''
    return self.db.new_params()

  def __iter__(self):
    ''' Return an iterator of all the rows as row_class instances.
    '''
    return iter(self.rows())

  def _cached_row(self, raw_row):
    id_index = self.id_index
    row_class = self.row_class
    if id_index is None:
      row = row_class(self, raw_row)
    else:
      id_value = raw_row[id_index]
      cached = self._row_cache.get(id_value)
      if cached:
        row = cached
      else:
        row = self._row_cache[id_value] = row_class(self, raw_row)
    return row

  def rows(self, where=None, *where_argv):
    ''' Return a list of row_class instances.
    '''
    row_class = self.row_class
    cache_row = self._cached_row
    return list(
        cache_row(row)
        for row in self.select(*where_argv, where=where))

  def rows_by_value(self, column_names, *values):
    ''' Return rows which have the specified column values.
    '''
    if isinstance(column_names, str):
      column_names = (column_names,)
    if len(column_names) != len(values):
      raise ValueError("%d column_names vs %d values"
                       % (len(column_names), len(values)))
    P = self.new_params()
    conditions = []
    for column_name, value in zip(column_names, values):
      if value is None:
        conditions.append(
            '`%s` IS NULL' % (column_name,))
      elif isinstance(value, (list, tuple, set)):
        conditions.append(
            '`%s` in (%s)'
            % (column_name, ','.join(P.vadd(column_name, value))))
      else:
        conditions.append(
            '`%s` = %s' % (column_name, P.add(column_name, value)))
    if len(P) < 900:
      # arbitrary limit based on SQLite3 default limit of 999
      where_clause = ' AND '.join(conditions)
      return self.rows(where_clause, *P.values)
    # perform a series of SQL queries and set intersections
    id_set = None
    # partition the conditions into scalars and vectors
    scalar_criteria = []
    vector_criteria = []
    for column_name, value in zip(column_names, values):
      if isinstance(value, (list, tuple, set)):
        vector_criteria.append( (column_name, value) )
      else:
        scalar_criteria.append( (column_name, value) )
    # run the scalar criteria first
    if scalar_criteria:
      P = self.new_params()
      conditions = []
      for column_name, value in scalar_criteria:
        if value is None:
          conditions.append(
              '`%s` IS NULL' % (column_name,))
        else:
          conditions.append(
              '`%s` = %s' % (column_name, P.add(column_name, value)))
      where_clause = ' AND '.join(conditions)
      row_ids = [
          row[0]
          for row in self.select(
              *P.values,
              column_names=(self.id_column,),
              where=where_clause)
      ]
      if not row_ids:
        # no matches
        return ()
      id_set = set(row_ids)
    else:
      id_set = None
    for column_name, value in vector_criteria:
      if isinstance(value, set):
        value = list(value)
      all_row_ids = []
      for offset in range(0, len(value), 900):
        values = value[offset:offset+900]
        P = self.new_params()
        P.vadd(column_name, values)
        all_row_ids.extend(
            row[0]
            for row in self.select(
                *P.values,
                column_names=(self.id_column,),
                where='`%s` in (%s)' % (column_name, ','.join(P.params)))
        )
      if not all_row_ids:
        # no matches
        return ()
      all_row_ids = set(all_row_ids)
      if id_set is None:
        id_set = all_row_ids
      else:
        id_set &= all_row_ids
        if not id_set:
          return ()
    row_ids = sorted(id_set)
    rows = []
    for offset in range(0, len(row_ids), 900):
      values = value[offset:offset+900]
      P = self.new_params()
      P.vadd(column_name, values)
      where_clause = '`%s` in (%s)' % (self.id_column, ','.join(P.params))
      rows.extend(self.rows(where_clause, *P.values))
    return rows

  def unique_row_where(self, **kw):
    ''' Fetch a row uniquely identified by the keyword parameters
        using a cache.

        This makes things like repeated graph traversal more efficient
        as the edge is defined as starting at one row's id and have
        some additional criteria.

        This method should only be used when the parameters identify
        a unique row, or no row.
    '''
    key = tuple(sorted(kw.items()))
    cache = self._row_cache_unique
    row = cache.get(key)
    if row:
      # return cache value
      return row
    rows = self.rows_where(**kw)
    if rows:
      # should be a single row - cache and return
      row, = rows
      cache[key] = row
      return row
    # no match
    return None

  def rows_where(self, **kw):
    ''' Keyword based form of `rows_by_value`.
    '''
    column_names = []
    values = []
    for column_name, value in kw.items():
      column_names.append(column_name)
      values.append(value)
    return self.rows_by_value(column_names, *values)

  @prop
  def qual_name(self):
    ''' The qualified name of this Table.
    '''
    db_name = self.db.db_name
    return '.'.join( (db_name, self.table_name) )

  @prop
  def conn(self):
    ''' The current db connection.
    '''
    return self.db.conn

  def select(self, *where_argv, column_names=None, where=None):
    ''' Select raw SQL data from the table.

        It is generally better to use .rows instead, which returns typed rows.
    '''
    if column_names is None:
      column_names = self.column_names
    elif isinstance(column_names, str):
      column_names = (column_names,)
    sql = 'select %s from %s' % (','.join(self.column_names), self.table_name)
    sqlargs = []
    if where:
      sql += ' where ' + where
      sqlargs.extend(where_argv)
    elif where_argv:
      raise ValueError(
          "empty where (%r) but where_argv=%r"
          % (where, where_argv))
    ##info("SQL = %r", sql)
    ##if sqlargs: info("  args = %r", sqlargs)
    with Pfx("SQL %r: %r", sql, sqlargs):
      return self.conn.cursor().execute(sql, sqlargs)

  def new_row(self, **kw):
    ''' Insert a new row and then fetch it for return.
    '''
    with Pfx("new_row(%r)", kw):
      row_id = self.insert1(**kw)
      return self[row_id]

  def insert1(self, **kw):
    ''' Insert a single row, return the new row id.
    '''
    column_names = []
    values = []
    for column_name, value in kw.items():
      column_names.append(column_name)
      values.append(value)
    X("insert1: column_names=%r, values=%r", column_names, values)
    return self.insert(column_names, (values,))

  def insert(self, column_names, valueses, ignore=False):
    ''' Insert new rows. Return the last row id value.
    '''
    ins_cmd = 'insert or ignore' if ignore else 'insert'
    sql = ins_cmd + (
        ' into `%s`(%s) values '
        % (
            self.table_name,
            ','.join(
                '`%s`' % (column_name,)
                for column_name in column_names))
    )
    sqlargs = []
    tuple_param = '(%s)' % ( ','.join( '?' for _ in column_names ), )
    tuple_params = []
    for values in valueses:
      tuple_params.append(tuple_param)
      sqlargs.extend(values)
    sql += ', '.join(tuple_params)
    C = self.conn.cursor()
    ##info("SQL = %r", sql)
    ##if sqlargs: info("  args = %r", sqlargs)
    with Pfx("SQL %r: %r", sql, sqlargs):
      C.execute(sql, sqlargs)
      last_id = C.lastrowid
    self.conn.commit()
    C.close()
    return last_id

  def update_columns(self, update_columns, update_argv, where, *where_argv):
    ''' Update specific row columns.
    '''
    sql = (
        'update %s set %s where %s'
        % (
            self.table_name,
            ','.join("%s=?" % (column_name,) for column_name in update_columns),
            where
        )
    )
    sqlargs = list(update_argv) + list(where_argv)
    C = self.conn.cursor()
    info("SQL: %s %r", sql, sqlargs)
    with Pfx("SQL %r: %r", sql, sqlargs):
      C.execute(sql, sqlargs)
    self.conn.commit()
    C.close()

  def delete(self, where, *where_argv):
    ''' Delete rows.
    '''
    sql = 'delete from %s where %s' % (self.table_name, where)
    sqlargs = where_argv
    C = self.conn.cursor()
    info("SQL: %s %r", sql, sqlargs)
    with Pfx("SQL %r: %r", sql, sqlargs):
      C.execute(sql, sqlargs)
    self.conn.commit()
    C.close()

  def named_row(self, name, fuzzy=False):
    ''' Return the unique Row with the specified `name`.

        The name look up is done by direct value, then on increasingly
        vague terms. A unique match will be returned, or a KeyError
        if no unique match is found.
    '''
    if self.name_column is None:
      raise RuntimeError("%s: no name_column" % (self,))
    rows = self.rows_by_value(self.name_column, name)
    if len(rows) == 1:
      return rows[0]
    if fuzzy:
      name_stripped = name.strip()
      P = self.new_params()
      sql = 'trim(`%s`) = %s' % (self.name_column,
                                 P.add('name_stripped', name_stripped))
      rows = self.rows(sql, *P.values)
      if len(rows) == 1:
        return rows[0]
      name_lc = name_stripped.lower()
      P = self.new_params()
      sql = 'lower(trim(`%s`)) = %s' % (self.name_column,
                                        P.add('name_lc', name_lc))
      rows = self.rows(sql, *P.values)
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
    cache = self._row_cache
    if id_value is None or isinstance(id_value, int):
      # direct lookup, return single row
      row = cache.get(id_value)
      if row is not None:
        return row
      row, = self.rows_where(**{self.id_column: id_value})
      return row
    if isinstance(id_value, str):
      return self.named_row(id_value, fuzzy=True)
    if isinstance(id_value, (list, set, tuple)):
      cached = {k: cache.get(k) for k in id_value}
      rows = list(filter(None, cached.values()))
      id_value = [ row for k, row in cached.items() if not row ]
      # id_value now a unique list of row keys
      if id_value:
        if len(id_value) >= 1024:
          id_value = sorted(id_value)
          offset = 0
          while offset < len(id_value):
            rows.extend(id_value[offset:offset+1024])
            offset += 1024
        else:
          condition = where_index(self.id_column, id_value)
          rows.extend(self.rows(condition.where, *condition.params))
      return rows
    condition = where_index(self.id_column, id_value)
    rows = self.rows(condition.where, *condition.params)
    if condition.is_scalar:
      return the(rows)
    return rows

  def get(self, id_value, default=None):
    ''' Fetch the row with id `id_value`, or a default.
    '''
    try:
      return self[id_value]
    except (IndexError, KeyError) as e:
      ##X("%s.get(%r): %s", self, id_value, e)
      return default

  def __setitem__(self, id_value, new_name):
    if self.name_column is None:
      raise RuntimeError("%s: no name_colum" % (self,))
    row = self[id_value]
    row.name = new_name

  def edit_column_by_ids(self, column_name, ids=None):
    ''' Open an editor on the values of a column for specific `ids`.
    '''
    if ids is None:
      where = None
    else:
      where = '`%s` in (%s)' % (column_name, ','.join("%d" % i for i in ids))
    return self.edit_column(column_name, where)

  def edit_column(self, column_name, where=None):
    ''' Open an interactive editor on the values of a column.
    '''
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

  def link_to(self, other, local_column=None, other_column=None, rel_name=None):
    ''' Associate this table with another via a column indexing `other`.

        Parameters:
        * `other`: the other table
        * `local_column`: the column in this table with the other
          table's column value; default `self.id_column`
        * `other_column`: the column in the other table with the
          matching value; default `other.id_column`
        * `rel_name`: name for this relation; default `other.name`
    '''
    if rel_name is None:
      rel_name = other.table_name
    rels = self.relations
    if rel_name in rels:
      raise KeyError("relation %r already defined" % (rel_name,))
    if other_column is None:
      other_column = other.id_column
    rels[rel_name] = lambda local_key: other.rows_by_value(other_column, local_key)

  def link_via(self,
               via, via_left_column, via_right_column,
               other, left_column=None, right_column=None,
               rel_name=None):
    ''' Associate this table with another via a mapping table.

        Parameters:
        * `via`: the mapping table
        * `via_left_column`: the column in `via` with this table's value
        * `via_right_column`: the column in `via` with the other table's value
        * `other`: the other table
        * `left_column`: value in this Table; default `self.id_column`
        * `right_column`: value in other Table; default `other.id_column`
        * `rel_name`: name for this relation; default from `other.name`
    '''
    if rel_name is None:
      rel_name = other.table_name
    if left_column is None:
      left_column = self.id_column
    if right_column is None:
      right_column = other.id_column
    rels = self.relations
    if rel_name in rels:
      raise KeyError("relation %r already defined" % (rel_name,))
    rel = RelationVia(via, via_left_column, self, via_right_column, other,
                      left_column=left_column, right_column=right_column)
    rels[rel_name] = lambda left_key: rel.left_to_right(left_key)

class Row(object):
  ''' A row of column values.
  '''

  def __init__(self, table, values, lock=None):
    if lock is None:
      lock = table._lock
    self._table = table
    self._row = table.row_tuple_class(*values)
    self._lock = lock

  def __str__(self):
    return "<%s>%s:%s" % (self.__class__.__name__, self._table.table_name, self._row)
  __repr__ = __str__

  def new_params(self):
    ''' Return a new Params for a query.
    '''
    return self._table.new_params()

  def __iter__(self):
    return iter(self._row)

  def __len__(self):
    return len(self._row)

  def __hash__(self):
    return self[self._table.id_column]

  def __eq__(self, other):
    table = self._table
    cmp_column = table.name_column
    if cmp_column is None:
      cmp_column = table.id_column
    return self[cmp_column] == other[cmp_column]

  def __lt__(self, other):
    table = self._table
    cmp_column = table.name_column
    if cmp_column is None:
      cmp_column = table.id_column
    return self[cmp_column] < other[cmp_column]

  def keys(self):
    ''' This row's column names.
    '''
    return self.column_names

  def values(self):
    ''' The values of this row in column name order.
    '''
    return self._row

  def items(self):
    ''' An iterable of `(column_name,column_value)` for this row.
    '''
    return zip(self.column_names, self.values())

  @prop
  def db(self):
    ''' This row's table's db.
    '''
    return self._table.db

  @prop
  def column_names(self):
    ''' The column names of this row's table.
    '''
    return self._table.column_names

  @prop
  def id_value(self):
    ''' The row's id value.
    '''
    return getattr(self._row, self._table.id_column)

  @prop
  def name(self):
    ''' A row's name.
    '''
    name_column = self._table.name_column
    if name_column is None:
      raise RuntimeError("%s: no name_column" % (self,))
    return self[name_column]

  @name.setter
  def name(self, new_name):
    ''' Set the row's name.
    '''
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
      raise KeyError("_row %r has no attribute %r: %s" % (self._row, key, e))

  def __setitem__(self, key, value):
    ''' Direct access to row values by column name or index.
    '''
    if isinstance(key, int):
      # do I really want to support numeric access to columns?
      key = self._table.column_names[key]
    self._table.update_columns(
        (key,), (value,),
        '`%s` = ?' % (self._table.id_column,),
        self.id_value)
    self._row = self._row._replace(**{key: value})

  def __getattr__(self, attr):
    ''' Implement the following attributes:
          .column_name  => column value
          .to_{relnam}s => related rows from another table
    '''
    T = self._table
    if not attr.startswith('_') and attr in T.column_names:
      # .column_name => column value
      return getattr(self._row, attr)
    # .to_{relname}s => related rows in other table
    if attr.startswith('to_') and attr.endswith('s'):
      rel_name = attr[3:-1]
      if rel_name in T.relations:
        return T.relations[rel_name](self.id_value)
      warning("Row %s.to_%ss: NO SUCH RELATION %r", self, rel_name, rel_name)
    raise AttributeError("%s: no attr %r" % (self.__class__, attr,))

  def __setattr__(self, attr, value):
    if not attr.startswith('_') and attr in self.column_names:
      self[attr] = value
    else:
      # TODO: use super().__setattr__ ?
      self.__dict__[attr] = value

_RelationTo = namedtuple(
    'RelationTo', 'left via_left_column right via_right_column')
class RelationTo(_RelationTo):
  ''' Manage a relationship between 2 Tables.
  '''

  def left_to_right(self, left_values):
    ''' Return the rows from the right table associated with keys
        from the left table.
    '''
    return self.right.rows_by_value(self.via_right_column, left_values)

def RelationVia(
    via,
    via_left_column, left,
    via_right_column, right,
    left_column=None, right_column=None
):
    ''' Manage a relationship between 2 `Tables based via a third mapping Table.

        Initialised with:
        * `via`: the relation Table
        * `via_left_column`: via Table column containing the value
          for the left Table
        * `left`: the left Table
        * `via_right_column`: via Table column containing the value
          for the right Table
        * `right`: the right Table
        * `left_column`: left Table column containing the value,
          default `left.id_column`
        * `right_column`: right Table column containing the value,
          default `right.id_column`
    '''
    if left_column is None:
      left_column = left.id_column
    if right_column is None:
      right_column = right.id_column
    return _RelationVia(
        via,
        via_left_column, left,
        via_right_column, right,
        left_column, right_column)

_RelationViaTuple = namedtuple('RelationVia',
                               '''via via_left_column left via_right_column right
                                  left_column right_column''')
class _RelationVia(_RelationViaTuple):
  ''' Manage a relationship between 2 `Table`s based on their id_columns.

      Initialised with:
      * `via`: the relation Table
      * `via_left_column`: via Table column containing the value for the left Table
      * `left`: the left Table
      * `via_right_column`: via Table column containing the value for the right Table
      * `right`: the right Table
      * `left_column`: left Table column containing the value,
        default `left.id_column`
      * `right_column`: right Table column containing the value,
        default `right.id_column`
  '''

  def right_keys(self, left_values):
    ''' Fetch the right hand keys associated with the supplied `left_keys`.
        Returns a set.
    '''
    condition = where_index(self.via_left_column, left_values)
    rel_rows = self.via.rows(condition.where, *condition.params)
    return set( [ row[self.via_right_column] for row in rel_rows ] )

  def left_to_right(self, left_values):
    ''' Fetch right hand rows given a pythonic index into left.
    '''
    if isinstance(left_values, (int, float, str)):
      ''' A single key gets a proxy for the result, allowing modification.
      '''
      return RelatedRows(left_values, self)
    right_values = self.right_keys(left_values)
    if not right_values:
      return []
    return self.right.rows_by_value(self.right_column, right_values)

  # default indirection is from left to right
  __call__ = left_to_right

  def add(self, left_value, right_value):
    ''' Add the pair (`left_value`, `right_value`) to the mapping.
    '''
    self.via.insert(
        (self.via_left_column, self.via_right_column),
        [ (left_value, right_value) ],
        ignore=True
    )

  def __iadd__(self, lr):
    ''' Insert the `(left_value,right_value)` pair `lr` into the mapping.
    '''
    left_value, right_value = lr
    self.add(left_value, right_value)

  def remove(self, left_value, right_value):
    ''' Remove the association between `left_value` and `right_value`.
    '''
    # TODO: build the query with a Params
    self.via.delete(
        '%s = ? and %s = ?'
        % (self.via_left_column, self.via_right_column),
        left_value, right_value)

  def __isub__(self, lr):
    ''' Remove the (left_value, right_value) pair `lr` from the mapping.
    '''
    left_value, right_value = lr
    self.remove(left_value, right_value)

  def remove_left(self, left_values):
    ''' Remove all relation rows with the specified via_left_column values.
    '''
    condition = where_index(self.via_left_column, left_values)
    return self.via.delete(condition.where, *condition.params)

  def remove_right(self, right_values):
    ''' Remove all relation rows with the specified via_right_column values.
    '''
    condition = where_index(self.via_right_column, right_values)
    return self.via.delete(condition.where, *condition.params)

class RelatedRows(object):
  ''' A proxy for rows related to a table via an intermediate table.
  '''

  def __init__(self, left_key, via):
    ''' A proxy for rows related to `left_key` throught the relation `via`.

        Parameters:
        * `left_key`: the key of the left side of `via`
        * `via`: the RelationVia defining the relationship
    '''
    self.via = via
    self.key = left_key
    self._rows = None
    self._row_set = None

  def rows(self):
    ''' Return the related rows from the right table.
    '''
    if self._rows is None:
      self._rows = self.via.left_to_right([self.key])
    return self._rows

  def row_keys(self):
    ''' Return the related row keys from the right table.
    '''
    return self.via.right_keys(self.key)

  def rowset(self):
    ''' Return the related rows from the right table as a set.
    '''
    if self._row_set is None:
      self._row_set = set(self.rows())
    return self._row_set

  def __len__(self):
    return len(self.row_keys())

  def __nonzero__(self):
    return len(self) > 0

  __bool__ = __nonzero__

  def __iter__(self):
    ''' Return an iterator of the rows.
    '''
    return iter(self.rows())

  def __contains__(self, right_key):
    ''' Test if `right_key` is in the relation.
    '''
    if isinstance(right_key, (int, float, str)):
      return right_key in self.row_keys()
    return right_key in self.rowset()

  def __iadd__(self, right_key):
    ''' Add (`self.key`, `right_key`) to the mapping.
    '''
    self.via += (self.key, right_key)

  def __isub__(self, right_key):
    ''' Remove (`self.key`, `right_key`) from the mapping.
    '''
    self.via -= (self.key, right_key)

where_index_result = namedtuple(
    'where_index_result',
    'is_scalar where params')
def where_index(column, index):
  ''' Return a where clause and any associated parameters for a
      single column index such as may be accepted by __getitem__.

      This handles integers, strings, slicelike objects and bounded iterables.

      Returns a namedtuple with fields (is_scalar, where, params).
  '''
  try:
    start = index.start
    stop = index.stop
    step = index.step
  except AttributeError:
    # not a slice or slicelike object
    if index is None:
      return where_index_result(True, '`%s` IS NULL' % (column,), ())
    if isinstance(index, str):
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
        where += (
            ' AND MOD(`%s`, %d) == MOD(%d, %d)'
            % (column, step, start, step)
        )
      return where_index_result(False, where, ())
    # convert the slice into a range
    id_values = range(start, stop, step)
  # convert most iterables to a tuple
  if not isinstance(id_values, (list, tuple)):
    id_values = tuple(id_values)
  if not id_values:
    where = '1=2'
  elif len(id_values) == 1:
    where = '`%s` = ?' % (column,)
  else:
    where = '`%s` IN (%s)' % (column, ', '.join(['?' for _ in id_values]),)
  return where_index_result(False, where, id_values)

def _exercise_where_index():
  for index in (
      None, 0, 1,
      (1, 2, 3), [1, 3, 2],
      slice(0, 8, 2), slice(8, 0, -2),
      slice(0, None), slice(0, None, 2),
      slice(8, None, -2)
  ):
    print(repr(index), '=>', where_index('foo', index))
