#!/usr/bin/env python3
#

from __future__ import print_function
from collections import namedtuple
from functools import lru_cache
import sqlite3
from threading import RLock
from cs.dbutils import TableSpace, Table, Row
from cs.deco import cachedmethod
from cs.logutils import setup_logging, warning
from cs.pfx import Pfx
from cs.py.func import prop
from cs.x import X

DEFAULT_TAG_SEP = '.'

class MetadataDB(TableSpace):

  SCHEMAE = {
    # tags exist in a concrete taxonomy hierarchy, with potentially different
    # names in different domains
    'tag': {
        'INDEX': ('normalised_name',),
        'UNIQUE': ('parent_id normalised_name',),
        'name': str,              # short name
        'normalised_name': str,   # short name in canonical form (lc, ws->sp)
        'description': str,       # descriptive text
        'parent_id': int,         # parent tag, NULL if domain root tag
        'root_id': int,           # root tag id, aka domain id
        'caninical_id': int,      # link to tag in canonical domain, if mapped
    },
    # media come from various spaces
    # perhaps associated with storage areas or confidentiality
    'mediaspace': {
        'name': str,
        'description': str,
        'path': str,
    },
    # individual mediaitems
    # the path is unique within the mediaspace
    'media': {
        'name': str,
        'path': str,
        'mediaspace_id': int,
    },
  }

  def __init__(self, dbpath, **kw):
    super().__init__(db_name=dbpath, table_class=MetadataTable, **kw)
    self.dbpath = dbpath
    self.conn = sqlite3.connect(self.dbpath)
    self.param_style = '?'
    self.schemae = schemae = {}
    for table_name, columns in self.SCHEMAE.items():
      column_map = {}
      indices = []
      unique = []
      for column_name, pytype in columns.items():
        if column_name == 'INDEX':
          indices = pytype
        elif column_name == 'UNIQUE':
          for spec in pytype:
            unique.append(spec.split())
        elif column_name[0].islower():
          column_map[column_name] = pytype
        else:
          raise ValueError(
              "invalid column spec %r (pytype=%r)"
              % (column_name, pytype))
      schemae[table_name] = TableSchema(
          table_name, column_map, indices, unique)
    for name, schema in self.schemae.items():
      print(schema.create_statement)

  def create(self):
    ''' Create all the tables specified by the schema.
    '''
    for name, schema in sorted(self.schemae.items()):
      with Pfx("create %r", name):
        self.table(name).create()

  def __getattr__(self, attr):
    if not attr.startswith('_'):
      if attr.endswith('s'):
        table_name = attr[:-1]
        T = self.table(table_name)
        if T is not None:
          return T
    return super().__getattr__(attr)

  def table(self, table_name):
    ''' Obtain the singleton Table instance for `table_name`.

        If there are local classes for the table or its rows, use
        them, otherwise fall back on MetadataTable and MetadataRow.
    '''
    table_class_name = table_name.title() + 'Table'
    table_class = globals().get(table_class_name, MetadataTable)
    row_class_name = table_name.title() + 'Row'
    row_class = globals().get(row_class_name, MetadataRow)
    T = super().table(table_name, table_class=table_class, row_class=row_class)
    self.table_by_nickname[table_name] = T
    return T

  def tag_domain(self, domain_name, do_create=False):
    ''' Return the tag domain named `domain_name`.

        Raises KeyError if the domain does not exist unlesd `do_create`
        is true, in which case the domain will be created.
    '''
    tags = self.tags
    try:
      domain = tags.domain(domain_name)
    except KeyError:
      if do_create:
        domain = tags.new_domain(domain_name)
      else:
        raise
    return domain

_TableSchema = namedtuple('TableSchema', 'name columns indices unique');
class TableSchema(_TableSchema):

  @property
  def column_names(self):
    return ['id'] + list(self.columns.keys())

  def column_type(self, column_name, pytype):
    ''' Table column type specification from name and Python type.
    '''
    if column_name == 'id':
      assert pytype is int
      return 'integer primary key'
    if column_name == 'name':
      assert pytype is str
      return 'text collate nocase'
    if pytype is int:
      return 'integer'
    if pytype is str:
      return 'text'
    raise ValueError(
        "cannot turn (%r,%r) into an SQLite column type"
        % (column_name, pytype))

  @property
  def create_statement(self):
    ''' Table creation statement.
    '''
    return (
        'create table `%s` (%s)'
        % (
            self.name,
            ",\n  ".join(
                [ '`id` integer primary key' ]
                + [
                    '`%s` %s' % (name, self.column_type(name, pytype))
                    for name, pytype in self.columns.items()
                ] + [
                  ('constraint `%s` unique (%s)'
                  % (
                      '_'.join(['unique'] + unique),
                      ', '.join(
                          '`%s`' % (column_name,) for column_name in unique)
                  )) for unique in self.unique
                ]
            )
        )
    )

class MetadataTable(Table):

  def __init__(self, db, table_name, row_class=None, **kw):
    X("INIT %s %r ...", type(self), table_name)
    if row_class is None:
      row_class = MetadataRow
    schema = self.schema = db.schemae[table_name]
    super().__init__(
        db, table_name,
        row_class=row_class,
        column_names=schema.column_names,
        id_column='id',
        name_column='name',
        **kw)

  def create(self):
    ''' Create this table, prefill any required entries.
    '''
    schema = self.schema
    conn = self.db.conn
    conn.execute(schema.create_statement)
    for index in self.schema.indices:
      assert isinstance(index, str)
      conn.execute(
          'CREATE INDEX `%s__%s_idx` on `%s`(`%s`)'
          % (self.table_name, index, self.table_name, index))

  def autoname(self, name, tagdomain=None):
    ''' Return the row named `name`, creating it if necessary.
    '''
    try:
      row = self.named_row(name)
    except KeyError as e:
      warning("%s[%r]: %s", self, name, e)
      if tagdomain is None:
        rows = self.rows_where(name=name)
      else:
        rows = self.rows_by_where(name=name, tagdomain_id=tagdomain.id)
      if rows:
        X("rows=%r", rows)
        row, = rows
      else:
        row_id = self.insert1(name=name)
        row = self[row_id]
    return row

class MetadataRow(Row):

  def __getattr__(self, attr):
    column_names = self.column_names
    if not attr.startswith('_') and attr not in column_names:
      # {base}s => other_rows
      if attr.endswith('s'):
        base = attr[:-1]
        local_id = base + '_id'
        if local_id in column_names:
          # local_id => (other_table[local_id],)
          otable = self.db.table(attr)
          oid = self[local_id]
          return otable[oid],
        # map through link table to other table
        link_tablename = '_'.join(sorted(self.table.name, base))
        link_table = self.db.table(link_tablename)
        my_column_name = self.table.name + '_id'
        other_column_name = base + '_id'
        oids = [
            values[0]
            for values in link_table.select(
                column_names=(other_column_name,),
                where='`%s` = %d' % (my_column_name, self.id))
        ]
        # return the matching rows
        return otable[oids]
    return super().__getattr__(attr)

class TagTable(MetadataTable):

  def create(self):
    ''' Create this table, prefill any required entries.
    '''
    super().create()
    self.insert1(name=None)

  @staticmethod
  def normalise(tagname):
    ''' Return the normalised tag name.

        Strip leading and trailing whitespace, lowercase, replace
        all whitespace with single spaces.
    '''
    return ' '.join(tagname.strip().lower().split())

  @prop
  @cachedmethod
  def default_root(self):
    root, = self.rows_where(name=None, parent_id=None)
    return root

  @prop
  def root(self):
    ''' Return the root TagnodeRow.
    '''
    root_tagnode, = self.rows_where(parent_id=None)

  @lru_cache(maxsize=16)
  def domain(self, domain_name):
    ''' Return the root tag with name `domain_name`.
    '''
    normname = self.normalise(domain_name)
    domains = self.rows_where(normalised_name=normname, parent_id=None)
    if not domains:
      raise KeyError("no domain named %r" % (domain_name,))
    domain, = domains
    return domain

  def new_domain(self, domain_name):
    ''' Create and return a new tag domain.
    '''
    normname = self.normalise(domain_name)
    row_id = self.insert1(name=domain_name, normalised_name=normname, parent_id=None)
    return self[row_id]

class TagRow(MetadataRow):
  ''' A tag node.
  '''

  def normalise(self, name):
    return self._table.normalise(name)

  @prop
  def domain_id(self):
    return self.id if self.parent_id is None else self.root_id

  @prop
  @cachedmethod
  def parent(self):
    ''' Parent TagnodeRow or None.
    '''
    parent_id = self.parent_id
    if parent_id is None:
      return None
    return self._table[parent_id]

  def children(self):
    return self._table.rows_where(parent_id=self.id)

  def child(self, name, do_create):
    ''' Return the child of this tag named `name`.
        Return None if no match, or create the tag if `do_create`.
    '''
    normname = self.normalise(name)
    child = self._table.unique_row_where(
        parent_id=self.id, normalised_name=normname)
    if child:
      return child
    if do_create:
      X("child: call add_child(name=%r)", name)
      return self.add_child(name)
    return None

  def add_child(self, name):
    ''' Add a child tag to this tag, return the new tag.
    '''
    name = name.strip()
    normname = self.normalise(name)
    row_id = self._table.insert1(
        name=name,
        normalised_name=normname,
        parent_id=self.id,
        root_id=self.domain_id)
    return self._table[row_id]

  def path(self):
    ''' Return the to this tag from the root tag as a list of tags.
    '''
    tag = self
    elems = []
    while tag:
      elems.append(tag.name)
      tag = tag.parent
    return reversed(elems)

  def pathname(self, sep=None):
    ''' Return the pathname of this tag, as the names of the tags
        in the path to this tag joined by `sep` (default DEFAULT_TAG_SEP).
    '''
    if sep is None:
      sep = DEFAULT_TAG_SEP
    return sep.join(self.path())

  def by_path(self, path, do_create=False, sep=None):
    ''' Find a descentant by traversing `path`.
    '''
    if sep is None:
      sep = DEFAULT_TAG_SEP
    tag = self
    for name in path.split(sep):
      if name.strip():
        X("by_path: name=%r", name)
        tag = tag.child(name, do_create=do_create)
        if tag is None:
          break
    return tag

if __name__ == '__main__':
  setup_logging(__file__)
  db = MetadataDB('metadata.sqlite')
  for item in db.table('media'):
    print(item)
