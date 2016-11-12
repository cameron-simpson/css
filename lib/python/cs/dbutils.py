#!/usr/bin/python
#
# Classes used for representing relational db things, such as tables and rows.
#

from collections import namedtuple
from threading import RLock
from cs.threads import locked
from cs.logutils import X

class TableSpace(object):

  def __init__(self, table_class=None, lock=None, db_name=None):
    if table_class is None:
      table_class = Table
    if lock is None:
      lock = RLock()
    self.db_name = db_name
    self._tables = {}
    self._table_class = table_class
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
      T = self._tables[name] = self._table_class(self, name)
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

  @property
  def qual_name(self):
    return '.'.join( (self.db.db_name, self.table_name) )

  def __getitem__(self, id_value):
    return self.row_class(the(self.select_by_column(self.id_column, id_value)))

class Row(object):

  def __init__(self, table, values, lock=None):
    if lock is None:
      lock = table._lock
    self._table = table
    self._row = table.row_tuple(*values)
    self._lock = lock

  @property
  def db(self):
    return self._table.db

  @property
  def column_names(self):
    return self._table.column_names

  @property
  def id_value(self):
    return getattr(self._row, self.id_column)

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
      self.update_by_column(self, attr, value, self.id_column, self.id_value, sel_op='=')
      self._row = self._row._replace(**{attr: value})
    else:
      self.__dict__[attr] = value
