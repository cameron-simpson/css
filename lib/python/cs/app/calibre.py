#!/usr/bin/python
#
# Access Calibre ebook library data.
#       - Cameron Simpson <cs@zip.com.au> 13feb2016
#

from __future__ import print_function
import sys
import os
import os.path
from collections import namedtuple
from functools import partial
import re
import sqlite3
from threading import RLock
from types import SimpleNamespace as NS
from PIL import Image
Image.warnings.simplefilter('error', Image.DecompressionBombWarning)
from cs.edit import edit_strings
from cs.env import envsub
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
    if argv and argv[0].startswith('/'):
      library_path = argv.pop(0)
    else:
      library_path = None
    CL = Calibre_Library(library_path)
    xit = 0
    if not argv:
      warning("missing op")
      badopts = True
    else:
      op = argv.pop(0)
      with Pfx(op):
        if op == 'ls':
          xit, badopts = CL.cmd_ls(argv)
        elif op == 'rename':
          xit, badopts = CL.cmd_rename(argv)
        elif op == 'tag':
          xit, badopts = CL.cmd_tag(argv)
        else:
          warning("unrecognised op")
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
    self.metadbpath = self.pathto(METADB_NAME)
    self.metadb = sqlite3.connect(self.metadbpath)
    self._tables = {}
    self._table_meta = \
      {
        'authors': NS(klass=Author,
                      columns='id name sort link',
                      name='name'),
        'books': NS(klass=Book,
                    columns='id title sort timestamp pubdate series_index author_sort isbn lccn path flags uuid has_cover last_modified',
                    name='title'),
        'ratings': NS(klass=Rating,
                      columns='id rating',
                      name='rating'),
        'series': NS(klass=Series,
                     columns='id name sort',
                     name='name'),
        'tags': NS(klass=Tag,
                   columns='id name',
                   name='name'),
      }

  def dosql_ro(self, sql, *params):
    return self.metadb.execute(sql, params)

  def dosql_rw(self, sql, *params):
    c = self.metadb.cursor()
    results = c.execute(sql, params)
    self.metadb.commit()
    return results

  def pathto(self, rpath):
    if rpath.startswith('/'):
      raise ValueError('rpath may not start with a slash: %r' % (rpath,))
    return os.path.join(self.path, rpath)

  def table(self, table_name):
    T = self._tables.get(table_name)
    if T is None:
      try:
        meta = self._table_meta[table_name]
      except KeyError:
        raise AttributeError('%s: no entry in ._table_meta' % (table_name,))
      T = CalibreTable(meta.klass, self, self.metadb, table_name, meta.columns, meta.name)
      self._tables[table_name] = T
    return T

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
    raise AttributeError(attr)

  def cmd_rename(self, argv):
    xit = 0
    badopts = False
    if not argv:
      warning("missing 'tags'")
      badopts = True
    else:
      entity = argv.pop(0)
      if entity == "tags":
        table = self.table_tags
      else:
        warning("unsupported entity type: %r", entity)
        badopts = True
      if argv:
        warning("extra arguments after %s: %s", entity, ' '.join(argv))
        badopts = True
    if not badopts:
      names = [ obj.name for obj in table.instances() ]
      if not names:
        warning
      for name, newname in edit_strings(names):
        if newname != name:
          table[name].rename(newname)
    return xit, badopts

  def cmd_ls(self, argv):
    xit = 0
    badopts = False
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
        warning("unknown class %r", obclass)
        badopts = True
      if argv:
        warning("extra arguments: %r", argv)
        badopts = True
    return xit, badopts

  def cmd_tag(self, argv):
    xit = 0
    badopts = False
    if not argv:
      warning('missing book-title')
      badopts = True
    else:
      book_title = argv.pop(0)
    if not badopts:
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
              warning('unsupported tag op %r', tag_op)
              badopts = True
    return xit, badopts

class CalibreTable(object):

  def __init__(self, row_class, CL, db, name, columns, name_column):
    self.row_class = row_class
    self.library = CL
    self.db = db
    self.dosql_ro = CL.dosql_ro
    self.dosql_rw = CL.dosql_rw
    self.name = name
    self.columns = columns.split()
    self.name_column = name_column
    self._select_all = 'SELECT %s from %s' % (','.join(self.columns), name)

  def _select_rows(self, where=None):
    sql = self._select_all
    if where:
      sql += ' WHERE ' + where
    for row in self.dosql_ro(sql):
      yield self.row_class(self, dict(zip(self.columns, row)))

  def instances(self):
    ''' Return rows sorted by name.
    '''
    return sorted(self._select_rows(), key=lambda obj: obj.name)

  def __getitem__(self, row_id):
    ''' Retrieve row by id or name.
    '''
    if isinstance(row_id, int):
      rows = self._select_rows('id = %d' % (row_id,))
    elif isinstance(row_id, str):
      rows = filter(lambda r: r.name == row_id, self._select_rows())
    else:
      raise TypeError("invalid type, expected int or str, got: %s" % (type(row_id),))
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

class CalibreTableRow(O):
  ''' A snapshot of a row from a table, with column values as attributes.
      Not intended to represent significant state, actions take
      place against the database and generally also update the row's
      attribute values to match.
  '''

  def __init__(self, table, rowmap):
    self.table = table
    self.ns = NS(**rowmap)

  @property
  def dosql_ro(self):
    return self.table.dosql_ro

  @property
  def dosql_rw(self):
    return self.table.dosql_rw

  @property
  def name(self):
    name_column = self.table.name_column
    return getattr(self.ns, name_column)

  def __str__(self):
    return self.name

  def __hash__(self):
    return self.id

  def __lt__(self, other):
    return self.name < other.name

  def __getattr__(self, attr):
    return getattr(self.ns, attr)

  def __setattr__(self, attr, value):
    ''' Set attribute of this row, excluding `id`.
    '''
    if 'ns' in self.__dict__:
      ns = self.ns
      if hasattr(ns, attr):
        if attr == 'id':
          raise AttributeError('may not set %r' % (attr,))
        self.table.update(attr, value, "id=?", self.id)
        return setattr(ns, attr, value)
    return super().__setattr__(attr, value)

  @property
  def library(self):
    return self.table.library

  @property
  def db(self):
    return self.library.metadb

  def related_entities(self, link_table_name, our_column_name, other_column_name, other_table_name=None):
    if other_table_name is None:
     other_table_name = other_column_name + 's'
    T = self.library.table(other_table_name)
    return set( T[row[0]] for row
                in T.dosql_ro( 'SELECT %s as %s_id from %s where %s = %d'
                               % (other_column_name, other_column_name,
                                  link_table_name,
                                  our_column_name, self.id)) )

class Author(CalibreTableRow):

  @property
  def books(self):
    return self.related_entities('books_authors_link', 'author', 'book')

class Book(CalibreTableRow):

  @property
  def authors(self):
    return self.related_entities('books_authors_link', 'book', 'author')

  @property
  def rating(self):
    Rs = self.related_entities('books_ratings_link', 'book', 'rating')
    if Rs:
      return the(Rs).rating
    return None

  @property
  def series(self):
    Ss = self.related_entities('books_series_link', 'book', 'series', 'series')
    if Ss:
      return the(Ss)
    return None

  @property
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

  @property
  def books(self):
    return self.related_entities('books_series_link', 'series', 'book')

class Tag(CalibreTableRow):

  @property
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

if __name__ == '__main__':
  sys.exit(main(sys.argv))
