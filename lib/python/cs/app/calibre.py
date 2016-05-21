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
from cs.env import envsub
from cs.lex import get_identifier
from cs.logutils import Pfx, info, warning, error, setup_logging, X, XP
from cs.obj import O
from cs.threads import locked, locked_property

DEFAULT_LIBRARY = '$HOME/Calibre_Library'
METADB_NAME = 'metadata.db'

USAGE = '''Usage: %s [/path/to/iphoto-library-path] op [op-args...]
  ls [books]        List books.
  ls tags           List tags.
  select criteria... List books with all specified criteria.

Criteria:
  [!]/regexp            Regexp found in text fields.
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
          if not argv:
            obclass = 'books'
          else:
            obclass = argv.pop(0)
          with Pfx(obclass):
            if obclass == 'books':
              for B in CL.books:
                print(B.title, B.author_sort)
            elif obclass == 'tags':
              I.load_folders()
              names = I.event_names()
            else:
              warning("unknown class %r", obclass)
              badopts = True
            if argv:
              warning("extra arguments: %r", argv)
              badopts = True
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
    self._books = None
    self._book_table = None
    self._tables = {}
    self._table_meta = \
      {
        'books': (Book, 'id title sort timestamp pubdate series_index author_sort isbn lccn path flags uuid has_cover last_modified'),
      }

  def pathto(self, rpath):
    if rpath.startswith('/'):
      raise ValueError('rpath may not start with a slash: %r' % (rpath,))
    return os.path.join(self.path, rpath)

  @property
  def books(self):
    return self.table_books.instances()

  def __getattr__(self, attr):
    if attr.startswith('table_'):
      table_name = attr[6:]
      T = self._tables.get(table_name)
      if T is None:
        try:
          meta = self._table_meta[table_name]
        except KeyError:
          raise AttributeError('%s: no entry in ._table_meta for %r' % (attr, table_name))
        T = CalibreTable(meta[0], self, self.metadb, table_name, meta[1])
        self._tables[table_name] = T
      return T
    raise AttributeError(attr)

class CalibreTableRowNS(NS):

  def __init__(self, table, rowmap):
    self.table = table
    NS.__init__(self, **rowmap)

  def library(self):
    return self.table.library

class CalibreTable(object):

  def __init__(self, row_class, CL, db, name, columns):
    self.row_class = row_class
    self.library = CL
    self.db = db
    self.name = name
    self.columns = columns.split()
    X("columns = %r", self.columns)
    self._select_all = 'SELECT %s from %s' % (','.join(self.columns), name)
    X("select = %r", self._select_all)
    self.by_id = {}
    self._load()

  def instances(self):
    return self.by_id.values()

  def _load(self):
    for row in self.db.execute(self._select_all):
      o = self.row_class(self, dict(zip(self.columns, row)))
      self.by_id[o.id] = o

class Book(CalibreTableRowNS):
  pass

if __name__ == '__main__':
  sys.exit(main(sys.argv))
