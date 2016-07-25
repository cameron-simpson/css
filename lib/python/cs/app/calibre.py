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
from cs.seq import the
from cs.threads import locked, locked_property

DEFAULT_LIBRARY = '$HOME/Calibre_Library'
METADB_NAME = 'metadata.db'

USAGE = '''Usage: %s [/path/to/iphoto-library-path] op [op-args...]
  ls [books]        List books.
  ls authors        List authors.
  ls tags           List tags.
  select criteria... List books with all specified criteria.

Criteria:
  [!]/regexp            Filename matches regexp.
  [!]kw:[keyword]       Latest version has keyword.
                        Empty keyword means "has a keyword".
  [!]face:[person_name] Latest version has named person.
                        Empty person_name means "has a face".
                        May also be writtens "who:...".
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
              for B in CL.table_books.instances():
                print(B,
                      ' '.join(str(T) for T in B.tags),
                      str(B.rating),
                      str(B.series),
                     )
                print(' ', '+'.join(str(A) for A in B.authors))
                S = B.series
                if S:
                  for B2 in S.books:
                    if B2 is not B:
                      print('  also', B2)
            elif obclass in ('authors', 'tags'):
              for obj in CL.table(obclass).instances():
                print(obj)
                for B in obj.books:
                  print(' ', B)
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

  def pathto(self, rpath):
    if rpath.startswith('/'):
      raise ValueError('rpath may not start with a slash: %r' % (rpath,))
    return os.path.join(self.path, rpath)

  def table(self, table_name):
    T = self._tables.get(table_name)
    if T is None:
      try:
        faces = by_master_uuid[muuid]
      except KeyError as e:
        faces = by_master_uuid[muuid] = set()
      faces.add(face)

  def face(self, face_id):
    return self.face_by_id.get(face_id)

  def _load_table_vfaces(self):
    ''' Load Faces.RKVersionFaceContent into memory and set up mappings.
    '''
    by_id = self.vface_by_id = {}
    by_master_id = self.vfaces_by_master_id
    for vface in self.read_vfaces():
      by_id[vface.modelId] = vface
      master_id = vface.masterId
      try:
        vfaces = by_master_id[master_id]
      except KeyError:
        raise AttributeError('%s: no entry in ._table_meta' % (table_name,))
      T = CalibreTable(meta.klass, self, self.metadb, table_name, meta.columns, meta.name)
      self._tables[table_name] = T
    return T

  @locked_property
  def vfaces_by_master_id(self):
    self.load_vfaces()
    return I.vfaces_by_master_id.get(self.modelId, ())

  def _load_table_folders(self):
    ''' Load Library.RKFolder into memory and set up mappings.
    '''
    by_id = self.folder_by_id = {}
    by_name = self.folders_by_name = {}
    for folder in self.read_folders():
      by_id[folder.modelId] = folder
      name = folder.name
      try:
        folders = by_name[name]
      except KeyError:
        folders = by_name[name] = set()
      folders.add(folder)

class CalibreTable(object):

  def __init__(self, row_class, CL, db, name, columns, name_column):
    self.row_class = row_class
    self.library = CL
    self.db = db
    self.name = name
    self.columns = columns.split()
    self.name_column = name_column
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

  def __getitem__(self, row_id):
    return self.by_id[row_id]

class CalibreTableRowNS(NS):

  def __init__(self, table, rowmap):
    self.table = table
    NS.__init__(self, **rowmap)

  def __str__(self):
    return getattr(self, self.table.name_column)

  def __hash__(self):
    return self.id

  def match_one_person(self, person_name):
    matches = self.match_people(person_name)
    if not matches:
      raise ValueError("unknown person")
    if len(matches) > 1:
      raise ValueError("matches multiple people, rejected: %r" % (matches,))
    return matches.pop()

  def _load_table_masters(self):
    ''' Load Library.RKMaster into memory and set up mappings.
    '''
    by_id = self.master_by_id = {}
    for master in self.read_masters():
      by_id[master.modelId] = master

  def master(self, master_id):
    self.load_masters()
    return self.master_by_id.get(master_id)

  def master_pathnames(self):
    self.load_masters()
    for master in self.master_by_id.values():
      yield master.pathname

  def _load_table_versions(self):
    ''' Load Library.RKVersion into memory and set up mappings.
    '''
    by_id = self.version_by_id = {}
    by_master_id = self.versions_by_master_id = {}
    for version in self.read_versions():
      by_id[version.modelId] = version
      master_id = version.masterId
      try:
        versions = by_master_id[master_id]
      except KeyError:
        versions = by_master_id[master_id] = set()
      versions.add(version)

  def version(self, version_id):
    self.load_versions()
    return self.version_by_id.get(version_id)

  def _load_table_keywords(self):
    ''' Load Library.RKKeyword into memory and set up mappings.
    '''
    by_id = self.keyword_by_id = {}
    by_name = self.keyword_by_name = {}
    for kw in self.read_keywords():
      by_id[kw.modelId] = kw
      by_name[kw.name] = kw

  def keyword(self, keyword_id):
    self.load_keywords()
    return self.keyword_by_id.get(keyword_id)

  @locked_property
  def keywords(self):
    self.load_keywords()
    return self.keyword_by_name.values()

  def keyword_names(self):
    return frozenset(kw.name for kw in self.keywords)

  def match_keyword(self, kwname):
    ''' User convenience: match string against all keywords, return matches.
    '''
    self.load_keywords()
    if kwname in self.keyword_by_name:
      return (kwname,)
    lc_kwname = kwname.lower()
    matches = []
    for name in self.keyword_names():
      if lc_kwname in name.lower():
        matches.append(name)
    return matches

  def match_one_keyword(self, kwname):
    matches = self.match_keyword(kwname)
    if not matches:
      raise ValueError("unknown keyword")
    if len(matches) > 1:
      raise ValueError("matches multiple keywords, rejected: %r" % (matches,))
    return matches[0]

  def versions_by_keyword(self, kwname):
    self.load_keywords()
    return self.keywords_by_name[kwname].versions()

  def masters_by_keyword(self, kwname):
    self.load_keywords()
    return self.keyword_by_name[kwname].masters()

  def _load_table_keywordForVersions(self):
    ''' Load Library.RKKeywordForVersion into memory and set up mappings.
    '''
    by_kwid = self.kw4v_version_ids_by_keyword_id = {}
    by_vid = self.kw4v_keyword_ids_by_version_id = {}
    for kw4v in self.read_keywordForVersions():
      kwid = kw4v.keywordId
      vid = kw4v.versionId
      try:
        version_ids = by_kwid[kwid]
      except KeyError:
        version_ids = by_kwid[kwid] = set()
      version_ids.add(vid)
      try:
        keyword_ids = by_vid[vid]
      except KeyError:
        keyword_ids = by_vid[vid] = set()
      keyword_ids.add(kwid)

  def keywords_by_version(self, version_id):
    ''' Return version
    '''
    self.load_keywordForVersions()
    kwids = self.kw4v_keyword_ids_by_version_id.get(version_id, ())
    return [ self.keyword(kwid) for kwid in kwids ]

  def parse_selector(self, selection):
    with Pfx(selection):
      selection0 = selection
      selector = None
      invert = False
      if selection.startswith('!') or selection.startswith('-'):
        invert = True
        selection = selection[1:]
      if selection.startswith('/'):
        re_text = selection[1:]
        selector = SelectByFilenameRE(self, re_text, invert)
      else:
        sel_type, offset = get_identifier(selection)
        if not sel_type:
          raise ValueError("expected identifier at %r" % (selection,))
        if offset == len(selection):
          raise ValueError("expected delimiter after %r" % (sel_type,))
        selection = selection[offset:]
        if selection.startswith(':'):
          selection = selection[1:]
          if sel_type == 'kw':
            kwname = selection
            if not kwname:
              selector = SelectByFunction(self,
                                          lambda master: len(master.keywords) > 0,
                                          invert)
            else:
              okwname = kwname
              try:
                kwname = self.match_one_keyword(kwname)
              except ValueError as e:
                raise ValueError("invalid keyword: %s" % (e,))
              else:
                if kwname != okwname:
                  info("%r ==> %r", okwname, kwname)
                selector = SelectByKeyword_Name(self, kwname, invert)
          elif sel_type == 'face' or sel_type == 'who':
            person_name = selection
            if not person_name:
              selector = SelectByFunction(self,
                                          lambda master: len(master.vfaces) > 0,
                                          invert)
            else:
              operson_name = person_name
              try:
                person_name = self.match_one_person(person_name)
              except ValueError as e:
                warning("rejected face name: %s", e)
                badopts = True
              else:
                if person_name != operson_name:
                  info("%r ==> %r", operson_name, person_name)
                selector = SelectByPerson_Name(self, person_name, invert)
          else:
            raise ValueError("unknown selector type %r" % (sel_type,))
        elif selection[0] in '<=>':
          cmpop = selection[0]
          selection = selection[1:]
          if selection.startswith('='):
            cmpop += '='
            selection = selection[1:]
          left = sel_type
          right = selection
          selector = SelectByComparison(self, left, cmpop, right, invert)
        else:
          raise ValueError("unrecognised delimiter after %r" % (sel_type,))
      if selector is None:
        raise RuntimeError("parse_selector(%r) did not set selector" % (selection0,))
      return selector

class iPhotoDBs(object):

  def __init__(self, iphoto):
    self.iphoto = iphoto
    self.dbmap = {}
    self.named_tables = {}
    self._lock = iphoto._lock

  def load_all(self):
    for dbname in 'Library', 'Faces':
      self._load_db(dbname)

  @property
  def library(self):
    return self.table.library

  def related_entities(self, link_table_name, our_column_name, other_column_name, other_table_name=None):
    if other_table_name is None:
     other_table_name = other_column_name + 's'
    T = self.library.table(other_table_name)
    return set( T[row[0]] for row
                in T.db.execute( 'SELECT %s as %s_id from %s where %s = %d'
                                 % (other_column_name, other_column_name,
                                    link_table_name,
                                    our_column_name, self.id)) )

class Author(CalibreTableRowNS):

  @property
  def books(self):
    return self.related_entities('books_authors_link', 'author', 'book')

class Book(CalibreTableRowNS):

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

class Rating(CalibreTableRowNS):

  def __str__(self):
    return ('*' * self.rating) if self.rating else '-'

class Series(CalibreTableRowNS):

  @property
  def books(self):
    return self.related_entities('books_series_link', 'series', 'book')

class Tag(CalibreTableRowNS):

  @property
  def books(self):
    return self.related_entities('books_tags_link', 'tag', 'book')

if __name__ == '__main__':
  sys.exit(main(sys.argv))
