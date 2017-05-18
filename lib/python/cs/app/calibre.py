#!/usr/bin/python
#
# Access Calibre ebook library data.
#       - Cameron Simpson <cs@zip.com.au> 13feb2016
#

from __future__ import print_function
from collections import namedtuple
from functools import partial
from getopt import GetoptError
import os
import os.path
import re
import sqlite3
import sys
from threading import RLock
from types import SimpleNamespace as NS
from PIL import Image
Image.warnings.simplefilter('error', Image.DecompressionBombWarning)
from cs.dbutils import TableSpace, Table, Row
from cs.edit import edit_strings
from cs.env import envsub
from cs.py.func import prop
from cs.lex import get_identifier
from cs.logutils import Pfx, info, warning, error, setup_logging, X, XP
from cs.obj import O
from cs.seq import the
from cs.threads import locked, locked_property

DEFAULT_LIBRARY = '$HOME/Calibre_Library'
METADB_NAME = 'metadata.db'

USAGE = '''Usage: %s [/path/to/iphoto-library-path] op [op-args...]
  edit tags         Edit tag names.
  ls [books]        List books.
  ls authors        List authors.
  ls tags           List tags.
  select criteria... List books with all specified criteria.
  tag book-title +tag...

Criteria:
  [!]/regexp          Regexp found in text fields.
  [!]tag:keyword      Book has keyword.
  Because "!" is often used for shell history expansion, a dash "-"
  is also accepted to invert the selector.
'''

def main(argv=None):
  ''' Main program associated with the cs.app.calibre module.
  '''
  if argv is None:
    argv = [ 'cs.app.calibre' ]
  cmd = os.path.basename(argv.pop(0))
  usage = USAGE % (cmd,)
  setup_logging(cmd)
  with Pfx(cmd):
    badopts = False
    try:
      if argv and argv[0].startswith('/'):
        library_path = argv.pop(0)
      else:
        library_path = None
      CL = Calibre_Library(library_path)
      xit = 0
      if not argv:
        raise GetoptError("missing op")
        badopts = True
      else:
        op = argv.pop(0)
        with Pfx(op):
          if op == 'ls':
            return CL.cmd_ls(argv)
          if op == 'rename':
            return CL.cmd_rename(argv)
          if op == 'tag':
            return CL.cmd_tag(argv)
          raise GetoptError("unrecognised op")
    except GetoptError as e:
      warning("%s", e)
      badopts = True
    if badopts:
      print(usage, file=sys.stderr)
      return 2
    return xit

class Calibre_Library(O):

  def __init__(self, libpath=None):
    ''' Open the Calibre library stored at `libpath`.
        If `libpath` is not supplied, use $CALIBRE_LIBRARY_PATH or DEFAULT_LIBRARY.
    '''
    if libpath is None:
      libpath = os.environ.get('CALIBRE_LIBRARY_PATH', envsub(DEFAULT_LIBRARY))
    if not os.path.isdir(libpath):
      raise ValueError("not a directory: %r" % (libpath,))
    self.path = libpath
    self._lock = RLock()
    self.metadbpath = self.pathto(METADB_NAME)
    self.metadb = CalibreMetaDB(self, self.metadbpath)
    self.table = self.metadb.table

  def __getattr__(self, attr):
    if attr.startswith('table_'):
      return self.table(attr[6:])
    raise AttributeError(attr)

  def pathto(self, rpath):
    if rpath.startswith('/'):
      raise ValueError('rpath may not start with a slash: %r' % (rpath,))
    return os.path.join(self.path, rpath)

  def table(self, table_name):
    # will get beefed up if we open more DBs
    return self.metadb.table(table_name)

  def cmd_rename(self, argv):
    xit = 0
    if not argv:
      raise GetoptError("missing 'tags'")
    entity = argv.pop(0)
    if entity == "tags":
      table = self.table('tags')
    else:
      raise GetoptError("unsupported entity type: %r" % (entity,))
    if argv:
      raise GetoptError("extra arguments after %s: %s" % (entity, ' '.join(argv)))
    if not badopts:
      names = [ obj.name for obj in table.instances() ]
      if not names:
        warning
      for name, newname in edit_strings(names):
        if newname != name:
          table[name].rename(newname)
    return xit

  def cmd_ls(self, argv):
    xit = 0
    if not argv:
      obclass = 'books'
    else:
      obclass = argv.pop(0)
    with Pfx(obclass):
      if obclass == 'books':
        for B in self.table_books.instances():
          print(B,
                ' '.join(str(T) for T in B.tags),
                'rating:'+str(B.rating),
                'series:'+str(B.series),
               )
          print(' ', '+'.join(str(A) for A in B.authors))
          S = B.series
          if S:
            for B2 in sorted(S.books):
              if B2.id != B.id:
                print('  also', B2)
      elif obclass in ('authors', 'tags'):
        for obj in self.table(obclass).instances():
          print(obj)
          for B in obj.books:
            print(' ', B)
      else:
        raise GetoptError("unknown class %r" % (obclass,))
      if argv:
        raise GetoptError("extra arguments: %r" % (argv,))
    return xit

  def cmd_tag(self, argv):
    xit = 0
    if not argv:
      raise GetoptError('missing book-title')
    book_title = argv.pop(0)
    with Pfx(book_title):
      for B in self.books_by_title(book_title):
        for tag_op in argv:
          if tag_op.startswith('+'):
            tag_name = tag_op[1:]
            B.add_tag(tag_name)
          elif tag_op.startswith('-'):
            tag_name = tag_op[1:]
            B.remove_tag(tag_name)
          else:
            raise GetoptError('unsupported tag op %r' % (tag_op,))
    return xit

class CalibreMetaDB(TableSpace):

  def __init__(self, CL, dbpath):
    TableSpace.__init__(self, CalibreTable, db_name=dbpath, lock=CL._lock)
    self.library = CL
    self.conn = sqlite3.connect(CL.metadbpath)

  def dosql_ro(self, sql, *params):
    return self.conn.execute(sql, params)

  def dosql_rw(self, sql, *params):
    c = self.conn.cursor()
    results = c.execute(sql, params)
    self.conn.commit()
    return results

  def books_by_title(self, book_title):
    return [ B for B in self.books if B.title == book_title ]

  def book_by_title(self, book_title):
    return the(self.books_by_title(book_title))

  def tag_by_name(self, tag_name):
    return the( T for T in self.tags if T.name == tag_name )

  def __getattr__(self, attr):
    if attr.startswith('table_'):
      return self.table(attr[6:])
    if attr in ('books', 'authors', 'tags'):
      return self.table(attr).instances()
    return TableSpace.__getattr__(self, attr)

class CalibreTable(Table):

  def __init__(self, db, table_name):
    meta = self.META_DATA[table_name]
    Table.__init__(self, db, table_name,
                   column_names=meta.columns.split(),
                   row_class=meta.klass,
                   id_column='id')
    CL = db.library
    self.library = CL
    self.name_column = getattr(meta, 'name_column', None)

  def instances(self):
    ''' Return rows sorted by name.
    '''
    return sorted(self.read_rows(), key=lambda row: row.name)

  def __getitem__(self, row_id):
    ''' Retrieve row by id or name.
    '''
    if isinstance(row_id, int):
      where = 'id = %d' % (row_id,)
      where_argv = ()
    elif isinstance(row_id, str):
      where = '%s = ?' % (self.name_column,)
      where_argv = (row_id,)
    else:
      raise TypeError("invalid type, expected int or str, got: %s" % (type(row_id),))
    rows = self.rows(where, *where_argv)
    try:
      row = the(rows)
    except IndexError as e:
      raise KeyError(row_id)
    return row

  def make(self, name):
    try:
      R = self[name]
    except KeyError:
      R = new(name)
    return R

  def new(self, name, **row_map):
    ''' Create a new row with the supplied name and optional column value map.
        Return a CalibreTableRow for the new row.
    '''
    if self.name_column in row_map:
      raise ValueError('row_map contains column %r' % (self.name_column,))
    new_id = row_map.pop('id', None)
    if new_id is None:
      all_ids = list(obj.id for obj in self.instances())
      if all_ids:
        new_id = max(all_ids) + 1
      else:
        new_id = 1
    row_map['id'] = new_id
    row_map[self.name_column] = name
    self.insert_row(row_map)
    return self[new_id]

  def insert_row(self, row_map):
    columns = []
    values = []
    for k, v in row_map.items():
      columns.append(k)
      values.append(v)
    return self.dosql_rw('insert into %s(%s) values (%s)'
                         % (self.name,
                            ','.join(columns),
                            ','.join('?' for v in values)),
                         *values)

  def delete(self, where, *where_params):
    return self.dosql_rw('delete from %s where %s' % (self.name, where), *where_params)

  def update(self, attr, value, where=None, *where_params):
    ''' Update an attribute in selected table rows.
    '''
    sql = "update %s set %s=?" % (self.name, attr)
    params = [value]
    if where:
      sql += ' WHERE ' + where
      if where_params:
        params.extend(where_params)
    return self.dosql_rw(sql, *params)

class CalibreTableRow(Row):
  ''' A snapshot of a row from a table, with column values as attributes.
      Not intended to represent significant state, actions take
      place against the database and generally also update the row's
      attribute values to match.
  '''

  def __init__(self, table, values, lock=None):
    Row.__init__(self, table, values)

  @prop
  def name(self):
    name_column = self._table.name_column
    return getattr(self._row, name_column)

  def __str__(self):
    return self.name

  def __hash__(self):
    return self.id

  def __lt__(self, other):
    return self.name < other.name

  @prop
  def library(self):
    return self.db.library

  def related_entities(self, link_table_name, our_column_name, related_column_name, related_table_name=None):
    ''' Look up related entities via a link table.
        Return l
    '''
    if related_table_name is None:
     related_table_name = related_column_name + 's'
    LT = self.db.table(link_table_name)
    entity_ids = set( row[related_column_name]
                      for row in LT.read_rows('%s = %d'
                                              % (our_column_name, self.id))
                    )
    RT = self.db.table(related_table_name)
    return RT.read_rows('%s in (%s)' \
             % (RT.id_column,
                ','.join( str(eid) for eid in sorted(entity_ids) )
               )
          )

class Author(CalibreTableRow):

  @prop
  def books(self):
    return self.related_entities('books_authors_link', 'author', 'book')

class Book(CalibreTableRow):

  @prop
  def authors(self):
    return self.related_entities('books_authors_link', 'book', 'author')

  @prop
  def rating(self):
    Rs = list(self.related_entities('books_ratings_link', 'book', 'rating'))
    if Rs:
      return the(Rs).rating
    return None

  @prop
  def series(self):
    Ss = list(self.related_entities('books_series_link', 'book', 'series', 'series'))
    if Ss:
      return the(Ss)
    return None

  @prop
  def tags(self):
    return self.related_entities('books_tags_link', 'book', 'tag')

  def add_tag(self, tag_name):
    if tag_name not in [ str(T) for T in self.tags ]:
      T = self.library.table_tags.make(tag_name)
      sql = 'INSERT INTO books_tags_link(book, tag) VALUES (%d, %d)' \
            % (self.id, T.id)
      self.dosql_rw(sql)

  def remove_tag(self, tag_name):
    CL = self.library
    if tag_name in [ str(T) for T in self.tags ]:
      T = CL.tag_by_name(tag_name)
      sql = 'DELETE FROM books_tags_link WHERE book = %d and tag = %d' \
            % (self.id, T.id)
      CL.dosql_rw(sql)

class Rating(CalibreTableRow):

  def __str__(self):
    return ('*' * self.rating) if self.rating else '-'

class Series(CalibreTableRow):

  @prop
  def books(self):
    return self.related_entities('books_series_link', 'series', 'book')

class Tag(CalibreTableRow):

  @prop
  def books(self):
    return self.related_entities('books_tags_link', 'tag', 'book')

  def rename(self, new_name):
    if self.name == new_name:
      warning("rename tag %r: no change", self.name)
      return
    T = self.table
    try:
      otag = T[new_name]
    except KeyError:
      T.update('name', new_name, 'id = %d' % (self.id,))
    else:
      # update related objects (books?)
      # to point at the other tag
      for B in self.books:
        B.add_tag(new_name)
        B.remove_tag(self.name)
      # delete our tag, become the other tag
      T.delete('id = ?', self.id)
      self.ns.id = otag.id
    self.name = new_name

CalibreTable.META_DATA = {
  'authors': NS(klass=Author,
                columns='id name sort link',
                name_column='name'),
  'books': NS(klass=Book,
              columns='id title sort timestamp pubdate series_index author_sort isbn lccn path flags uuid has_cover last_modified',
              name_column='title'),
  'books_authors_link': NS(klass=CalibreTableRow,
                        columns='id book author'),
  'books_ratings_link': NS(klass=CalibreTableRow,
                        columns='id book rating'),
  'books_series_link': NS(klass=CalibreTableRow,
                        columns='id book series'),
  'books_tags_link': NS(klass=CalibreTableRow,
                        columns='id book tag'),
  'ratings': NS(klass=Rating,
                columns='id rating',
                name_column='rating'),
  'series': NS(klass=Series,
               columns='id name sort',
               name_column='name'),
  'tags': NS(klass=Tag,
             columns='id name',
             name_column='name'),
}

if __name__ == '__main__':
  sys.exit(main(sys.argv))
