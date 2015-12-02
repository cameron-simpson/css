#!/usr/bin/python
#
# Access iPhoto data.
#       - Cameron Simpson <cs@zip.com.au> 04apr2013
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
from PIL import Image
Image.warnings.simplefilter('error', Image.DecompressionBombWarning)
from cs.env import envsub
from cs.lex import get_identifier
from cs.logutils import Pfx, info, warning, error, setup_logging, X, XP
from cs.obj import O
from cs.threads import locked, locked_property

DEFAULT_LIBRARY = '$HOME/Pictures/iPhoto Library.photolibrary'

USAGE = '''Usage: %s [/path/to/iphoto-library-path] op [op-args...]
  info masters      List info about masters.
  kw keywords...    List masters with all specified keywords.
  people names...   List masters with the specified people.
  ls                List apdb names.
  ls [0-5]          List master pathnames with specific rating.
  ls albums         List album names.
  ls events         List events names.
  ls folders        List folder names (includes events).
  ls keywords       List keywords.
  ls masters        List master pathnames.
  ls people         List person names.
  select criteria... List masters with all specified criteria.

Criteria:
  [!]/regexp            Filename matches regexp.
  [!]kw:keyword         Latest version has keyword.
                        Empty keyword means "has a keyword".
  [!]face:person_name   Latest version has named person.
                        Empty person_name means "has a face".
'''

def main(argv=None):
  ''' Main program associated with the cs.app.osx.iphoto module.
  '''
  if argv is None:
    argv = [ 'cs.app.osx.iphoto' ]
  cmd = os.path.basename(argv.pop(0))
  usage = USAGE % (cmd,)
  setup_logging(cmd)
  with Pfx(cmd):
    badopts = False
    if argv and argv[0].startswith('/'):
      library_path = argv.pop(0)
    else:
      library_path = os.environ.get('IPHOTO_LIBRARY_PATH', envsub(DEFAULT_LIBRARY))
    I = iPhoto(library_path)
    xit = 0
    if not argv:
      warning("missing op")
      badopts = True
    else:
      op = argv.pop(0)
      with Pfx(op):
        if op == 'info':
          if not argv:
            warning("missing masters")
            badopts = True
          else:
            obclass = argv.pop(0)
            with Pfx(obclass):
              if obclass == 'masters':
                for master in sorted(I.masters(), key=lambda m: m.pathname):
                  with Pfx(master.pathname):
                    iminfo = master.image_info
                    if iminfo is None:
                      error("no info")
                      xit = 1
                    else:
                      print(master.pathname, iminfo.dx, iminfo.dy, iminfo.format,
                            *[ 'kw:'+kwname for kwname in master.keyword_names ])
              else:
                warning("unknown class %r", obclass)
                badopts = True
        elif op == 'kw':
          if not argv:
            warning("missing keywords")
            badopts = True
          else:
            # resolve keyword names
            kwnames = []
            for kwname in argv:
              with Pfx(kwname):
                okwname = kwname
                try:
                  kwname = I.match_one_keyword(kwname)
                except ValueError as e:
                  warning("rejected keyword: %s", e)
                  badopts = True
                else:
                  if kwname != okwname:
                    info("%r ==> %r", okwname, kwname)
                  kwnames.append(kwname)
            if not badopts:
              # select by keywords
              masters = None
              for kwname in kwnames:
                masters = I.select_by_keyword_name(kwname).select(masters)
              for master in masters:
                print(master.pathname)
        elif op == 'ls':
          if not argv:
            for dbname in sorted(I.dbnames()):
              print(dbname)
          else:
            obclass = argv.pop(0)
            with Pfx(obclass):
              if obclass.isdigit():
                rating = int(obclass)
                I.load_versions()
                names = []
                for version in I.versions():
                  if version.mainRating == rating:
                    pathname = version.master.pathname
                    if pathname is not None:
                      names.append(pathname)
              elif obclass == 'albums':
                I.load_albums()
                names = I.album_names()
              elif obclass == 'events':
                I.load_folders()
                names = I.event_names()
              elif obclass == 'folders':
                I.load_folders()
                names = I.folder_names()
              elif obclass == 'keywords':
                I.load_keywords()
                names = I.keyword_names()
              elif obclass == 'masters':
                I.load_masters()
                names = I.master_pathnames()
              elif obclass == 'people':
                I.load_persons()
                names = I.person_names()
              else:
                warning("unknown class %r", obclass)
                badopts = True
              if argv:
                warning("extra arguments: %r", argv)
                badopts = True
              if not badopts:
                for name in sorted(names):
                  print(name)
        elif op == 'people':
          if not argv:
            warning("missing person_names")
            badopts = True
          else:
            person_names = []
            for person_name in argv:
              matches = I.match_people(person_name)
              if not matches:
                warning("%s: unknown person_name", person_name)
                badopts = True
              elif len(matches) > 1:
                warning("%s: matches multiple people, rejected: %r", person_name, matches)
                badopts = True
              else:
                operson_name = person_name
                person_name = matches.pop()
                if person_name != operson_name:
                  info("%s ==> %s", operson_name, person_name)
                person_names.append(person_name)
            if not badopts:
              masters = None
              for person_name in person_names:
                masters = I.select_by_person_name(person_name).select(masters)
              for master in masters:
                print(master.pathname)
        elif op == 'select':
          if not argv:
            warning("missing selectors")
            badopts = True
          else:
            selectors = []
            for selection in argv:
              try:
                selector = I.parse_selector(selection)
              except ValueError as e:
                warning("invalid selector: %s", e)
                badopts = True
              else:
                selectors.append(selector)
          if not badopts:
            masters = None
            for selector in selectors:
              masters = selector.select(masters)
            for master in masters:
              print(master.pathname)
        elif op == "test":
          test(argv, I)
        else:
          warning("unrecognised op")
          badopts = True
    if badopts:
      print(usage, file=sys.stderr)
      return 2
    return xit

def test(argv, I):
  ##for folder in I.read_folders():
  ##  print('uuid =', folder.uuid, 'folderType =', folder.folderType, 'name =', folder.name)
  ##for keyword in I.read_keywords():
  ##  print('uuid =', keyword.uuid, 'name =', keyword.name)
  ##for master in I.read_masters():
  ##  print('uuid =', master.uuid, 'name =', master.name, 'originalFileName =', master.originalFileName, 'imagePath =', master.imagePath, 'pathname =', master.pathname)
  ##for v in I.read_versions():
  ##  print('uuid =', v.uuid, 'masterUuid =', v.masterUuid, 'versionNumber =', v.versionNumber)
  ##for kwv in I.keywords_to_versions():
  ##  print('keywordId =', kwv.keywordId, 'versionId =', kwv.versionId)
  ##for album in I.album_table.read_rows():
  ##  print('uuid =', album.uuid, 'albumType =', album.albumType, 'name =', album.name)
  ##for kwname in sorted(I.keyword_names()):
  ##  kw = I.keyword_by_name.get(kwname)
  ##  print(kw.name, [ master.name for master in kw.masters() ])
  for vface in I.vfaces():
    print(vface)
    P = vface.person()
    print("Person", P)
    master = vface.master
    print("Master", master)
    print(" ", master.pathname)
    for vface2 in master.vfaces:
      print("  VFace", vface)
    FI = vface.Image()
    filebase, ext = os.path.splitext(os.path.basename(master.pathname))
    filename = '%s-FACE%s' % (filebase, ext)
    print("save to %r" % (filename,))
    FI.save(filename)
    FI.close()
    break

Image_Info = namedtuple('Image_Info', 'dx dy format')

class iPhoto(O):

  def __init__(self, libpath=None):
    ''' Open the iPhoto library stored at `libpath`.
        If `libpath` is not supplied, use DEFAULT_LIBRARY.
    '''
    if libpath is None:
      libpath = envsub(DEFAULT_LIBRARY)
    if not os.path.isdir(libpath):
      raise ValueError("not a directory: %r" % (libpath,))
    self.path = libpath
    self.table_by_nickname = {}
    self._lock = RLock()
    self.dbs = iPhotoDBs(self)
    self.dbs.load_all()

  def pathto(self, rpath):
    if rpath.startswith('/'):
      raise ValueError('rpath may not start with a slash: %r' % (rpath,))
    return os.path.join(self.path, rpath)

  def dbnames(self):
    return self.dbs.dbnames()

  def dbpath(self, dbname):
    return self.dbs.pathto(dbname)

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
    raise AttributeError("iPhoto.__getattr__: nothing named %r" % (attr,))

  def _load_table_albums(self):
    ''' Load Library.RKMaster into memory and set up mappings.
    '''
    by_id = self.album_by_id = {}
    ##by_uuid = self.album_by_uuid = {}
    by_name = self.albums_by_name = {}
    for album in self.read_albums():
      by_id[album.modelId] = album
      ##by_uuid[album.uuid] = album
      name = album.name
      if name is None:
        warning("album has no name: %s", album.uuid)
      else:
        try:
          albums = by_name[name]
        except KeyError:
          albums = by_name[name] = set()
        albums.add(album)

  def album(self, album_id):
    self.load_albums()
    return self.album_by_id.get(album_id)

  def album_names(self):
    self.load_albums()
    return self.albums_by_name.keys()

  def _load_table_faces(self):
    ''' Load Faces.RKDetectedFace into memory and set up mappings.
    '''
    by_id = self.face_by_id = {}
    by_master_uuid = self.faces_by_master_uuid = {}
    for face in self.read_faces():
      by_id[face.modelId] = face
      muuid = face.masterUuid
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
    by_master_id = self.vfaces_by_master_id = {}
    for vface in self.read_vfaces():
      by_id[vface.modelId] = vface
      master_id = vface.masterId
      try:
        vfaces = by_master_id[master_id]
      except KeyError:
        vfaces = by_master_id[master_id] = set()
      vfaces.add(vface)

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

  def folder(self, folder_id):
    self.load_folders()
    return self.folder_by_id.get(folder_id)

  def folder_names(self):
    self.load_folders()
    return self.folders_by_name.keys()

  def folders_simple(self):
    return [ folder for folder in self.folders()
             if folder.sortKeyPath == 'custom.default'
           ]

  def events(self):
    return [ folder for folder in self.folders()
             if folder.sortKeyPath == 'custom.kind'
           ]

  def event_names(self):
    return [ event.name for event in self.events() ]

  def _load_table_persons(self):
    ''' Load Faces.RKFaceName into memory and set up mappings.
    '''
    by_id = self.person_by_id = {}
    by_name = self.person_by_name = {}
    by_faceKey = self.person_by_faceKey = {}
    for person in self.read_persons():
      by_id[person.modelId] = person
      by_name[person.name] = person
      by_faceKey[person.faceKey] = person
      # skip fullName; seems to be to associated with Contacts or something

  def person(self, faceKey):
    self.load_persons()
    return self.person_by_faceKey.get(faceKey)

  def person_names(self):
    self.load_persons()
    return self.person_by_name.keys()

  def match_people(self, person_name):
    ''' User convenience: match string against all person names, return matches.
    '''
    lc_person_name = person_name.lower()
    all_names = list(self.person_names())
    matches = set()
    # try exact match, ignoring case
    for name in all_names:
      if lc_person_name == name.lower():
        matches.add(name)
    if not matches:
      # try by word
      lc_person_words = lc_person_name.split()
      for name in all_names:
        lc_words = name.lower().split()
        match_count = 0
        for lc_person_word in lc_person_words:
          if lc_person_word in lc_words:
            match_count += 1
        if match_count == len(lc_person_words):
          matches.add(name)
    if not matches:
      # try substrings
      for name in all_names:
        if lc_person_name in name.lower():
          matches.add(name)
    return matches

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

  def parse_selector(self, selection):
    with Pfx(selection):
      selection0 = selection
      selector = None
      invert = False
      if selection.startswith('!'):
        invert = True
        selection = selection[1:]
      if selection.startswith('/'):
        re_text = selection[1:]
        selector = SelectByFilenameRE(self, re_text, invert)
      else:
        sel_type, offset = get_identifier(selection)
        if ( not sel_type
          or offset >= len(selection)
          or selection[offset] != ':' ):
          raise ValueError('invalid selector, not "/regexp" or "type:"')
        offset += 1
        if sel_type == 'kw':
          kwname = selection[offset:]
          if not kwname:
            raise ValueError("missing keyword")
          okwname = kwname
          try:
            kwname = self.match_one_keyword(kwname)
          except ValueError as e:
            raise ValueError("invalid keyword: %s", e)
          else:
            if kwname != okwname:
              info("%r ==> %r", okwname, kwname)
            selector = SelectByKeyword_Name(self, kwname, invert)
        else:
          raise ValueError("unknown selector type %r" % (sel_type,))
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
  def dbdirpath(self):
   return self.iphoto.pathto('Database/apdb')

  def dbnames(self):
    for basename in os.listdir(self.dbdirpath):
      if basename.endswith('.apdb'):
        yield basename[:-5]

  def pathto(self, dbname):
    ''' Compute pathname of named database file.
    '''
    if dbname == 'Faces':
      return os.path.join(self.dbdirpath, dbname+'.db')
    return os.path.join(self.dbdirpath, dbname+'.apdb')

  def _opendb(self, dbpath):
    ''' Open an SQLite3 connection to the named database.
    '''
    conn = sqlite3.connect(dbpath)
    XP("connect(%r): isolation_level=%s", dbpath, conn.isolation_level)
    return conn

  def _load_db(self, dbname):
    db = iPhotoDB(self.iphoto, dbname)
    self.dbmap[dbname] = db
    return db

  @locked
  def __getattr__(self, dbname):
    dbmap = self.dbmap
    if dbname in dbmap:
      return dbmap[dbname]
    dbpath = self.pathto(dbname)
    if os.path.exists(dbpath):
      return self._load_db(dbname)
    raise AttributeError(dbname)

class iPhotoDB(object):

  def __init__(self, iphoto, dbname):
    global SCHEMAE
    self.iphoto = iphoto
    self.name = dbname
    self.dbpath = iphoto.dbpath(dbname)
    self.conn = sqlite3.connect(self.dbpath)
    self.schema = SCHEMAE[dbname]
    self.table_row_classes = {}
    for nickname, schema in self.schema.items():
      self.iphoto.table_by_nickname[nickname] = iPhotoTable(self, nickname, schema)

class iPhotoTable(object):

  def __init__(self, db, nickname, schema):
    self.nickname = nickname
    self.db = db
    self.schema = schema
    table_name = schema['table_name']
    self.name = table_name
    self.qualname = '.'.join( (self.db.name, table_name) )
    klass = namedtuple('%s_Row' % (table_name,), ['I'] + list(schema['columns']))
    mixin = schema.get('mixin')
    lock = self.iphoto._lock
    if mixin is not None:
      class Mixed(klass, mixin):
        pass
      def klass(*a, **kw):
        o = Mixed(*a, **kw)
        o._lock = lock
        return o
    self.row_class = klass

  @property
  def iphoto(self):
    return self.db.iphoto

  @property
  def conn(self):
    return self.db.conn

  @property
  def table_name(self):
    return self.schema['table_name']

  def read_rows(self):
    I = self.iphoto
    row_class = self.row_class
    for row in self.conn.cursor().execute('select * from %s' % (self.table_name,)):
      yield row_class(*([I] + list(row)))

class Master_Mixin(object):

  @property
  def pathname(self):
    return os.path.join(self.I.pathto('Masters'), self.imagePath)

  @locked_property
  def versions(self):
    I = self.I
    I.load_versions()
    return I.versions_by_master_id.get(self.modelId, ())

  def latest_version(self):
    vs = self.versions
    if not vs:
      raise RuntimeError("no versions for master %d: %r", self.modelId, self.pathname)
      ##return None
    return max(vs, key=lambda v: v.versionNumber)

  @locked_property
  def faces(self):
    I = self.I
    I.load_faces()
    return I.faces_by_master_id.get(self.modelId, ())

  @locked_property
  def vfaces(self):
    I = self.I
    I.load_vfaces()
    return I.vfaces_by_master_id.get(self.modelId, ())

  @locked_property
  def people(self):
    them = set()
    for vface in self.vfaces:
      who = vface.person()
      if who is not None:
        ##X("master %d + %s", self.modelId, who.name)
        them.add(who)
    return them

  @property
  def keywords(self):
    ''' Return the keywords for the latest version of this master.
    '''
    return self.latest_version().keywords

  @property
  def keyword_names(self):
    return [ kw.name for kw in self.keywords ]

  def Image(self):
    ''' Obtain an open Image of this master.
        Caller must close.
    '''
    return Image.open(self.pathname)

  @locked_property
  def image_info(self):
    pathname = self.pathname
    with Pfx("Image.open(%r)", pathname):
      try:
        image = self.Image()
      except OSError as e:
        error("cannot load image: %s", e)
        return None
      dx, dy = image.size
      image_info = Image_Info(dx, dy, image.format)
      image.close()
    return image_info

  @property
  def dx(self):
    return self.image_info.dx

  @property
  def dy(self):
    return self.image_info.dy

  @property
  def format(self):
    return self.image_info.format

class Version_Mixin(object):

  @property
  def master(self):
    master = self.I.master(self.masterId)
    if master is None:
      raise ValueError("version %d masterId %d matches no master"
                       % (self.modelId, self.masterId))
    return master

  @locked_property
  def keywords(self):
    ''' Return the keywords for this version.
    '''
    I = self.I
    I.load_keywordForVersions()
    return frozenset(I.keywords_by_versionId[self.modelId])

  @property
  def keyword_names(self):
    return [ kw.name for kw in self.keywords ]

class Keyword_Mixin(object):

  def versions(self):
    ''' Return the versions with this keyword.
    '''
    I = self.I
    I.load_keywordForVersions()
    for vid in I.kw4v_version_ids_by_keyword_id.get(self.modelId, ()):
      yield I.version(vid)

  def masters(self):
    ''' Return the masters with this keyword.
    '''
    ms = set()
    for version in self.versions():
      ms.add(version.master)
    return ms

  def latest_versions(self):
    ''' Return the latest version of all masters with this keyword.
    '''
    return set(master.latest_version for master in self.masters())

class Person_Mixin(object):

  @locked_property
  def vfaces(self):
    return set()

class VFace_Mixin(object):

  @property
  def master(self):
    return self.I.master(self.masterId)

  def person(self):
    if not self.isNamed:
      return None
    return self.I.person(self.faceKey)

  def Image(self, padfactor=1.0):
    ''' Return an Image of this face.
    '''
    MI = self.master.Image()
    mdx, mdy = MI.size
    # convert face box into centre and radii 
    rx = self.faceRectWidth / 2 * padfactor
    ry = self.faceRectHeight / 2 * padfactor
    cx = self.faceRectLeft + rx
    cy = self.faceRectTop + ry
    X("RX = %s, RY = %s, CX = %s, CY = %s", rx, ry, cx, cy)
    # Image y-ordinates are inverse of iPhoto coordinates
    ##cx = mdx - cx
    cy = 1.0 - cy
    face_box = (
                 int(mdx * (cx - rx)), int(mdy * (cy - ry)),
                 int(mdx * (cx + rx)), int(mdy * (cy + ry)),
               )
    XP("MI.size = %r, face_box = %r", MI.size, face_box)
    face_Image = MI.crop(face_box)
    face_Image.load()
    MI.close()
    return face_Image

class _SelectMasters(object):
  ''' Select masters base class.
  '''

  def select(self, masters=None):
    if masters is None:
      return self.select_from_all()
    else:
      return self.select_masters(masters)

  def select_from_all(self):
    return self.select_masters(self.iphoto.masters())

class SelectByPerson_Name(_SelectMasters):
  ''' Select masters by person name.
  '''

  def __init__(self, iphoto, person_name, invert=False):
    self.iphoto = iphoto
    self.person_name = person_name
    self.person = iphoto.person_by_name[person_name]
    self.invert = invert

  def select_masters(self, masters):
    person = self.person
    if self.invert:
      for master in masters:
        if person not in master.people:
          yield master
    else:
      for master in masters:
        if person in master.people:
          yield master

class SelectByFilenameRE(_SelectMasters):
  ''' Select masters by regular expression.
  '''

  def __init__(self, iphoto, re_text, invert=False):
    self.iphoto = iphoto
    self.re_text = re_text
    self.invert = invert
    self.re = re.compile(re_text)

  def select_masters(self, masters):
    re = self.re
    if self.invert:
      for master in masters:
        if not re.search(master.latest_version().fileName):
          yield master
    else:
      for master in masters:
        if re.search(master.latest_version().fileName):
          yield master

class SelectByKeyword_Name(_SelectMasters):
  ''' Select masters by keyword name.
  '''

  def __init__(self, iphoto, kwname, invert=False):
    self.iphoto = iphoto
    self.kwname = kwname
    self.invert = invert

  def select_from_all(self):
    if self.invert:
      return self.select_masters(self.iphoto.masters())
    else:
      return self.iphoto.masters_by_keyword(self.kwname)

  def select_masters(self, masters):
    kwname = self.kwname
    if self.invert:
      for master in masters:
        if kwname not in master.keyword_names:
          yield master
    else:
      for master in masters:
        if kwname in master.keyword_names:
          yield master

SCHEMAE = {'Faces':
            { 'person':
                { 'table_name': 'RKFaceName',
                  'mixin': Person_Mixin,
                  'columns':
                    ( 'modelId', 'uuid', 'faceKey', 'keyVersionUuid',
                      'name', 'fullName', 'email', 'similarFacesCached',
                      'similarFacesOpen', 'manualOrder', 'lastUsedDate',
                      'attrs',
                    ),
                },
              'face':
                { 'table_name': 'RKDetectedFace',
                  'columns':
                    ( 'modelId', 'uuid', 'masterUuid', 'altMasterUuid',
                      'faceKey', 'correlatedFaceKey', 'ownerServiceKey',
                      'faceIndex', 'width', 'height', 'topLeftX',
                      'topLeftY', 'topRightX', 'topRightY', 'bottomLeftX',
                      'bottomLeftY', 'bottomRightX', 'bottomRightY',
                      'confidence', 'sharpness', 'exposureValue',
                      'rejected', 'faceCount', 'ignore', 'faceFlags',
                      'hasFaceTile', 'tileFacePosition', 'faceAngle',
                      'faceDirectionAngle', 'faceSkinScore',
                      'skippedInUnnamedFaces',
                    ),
                },
            },
           'Library':
            { 'master':
                { 'table_name': 'RKMaster',
                  'mixin': Master_Mixin,
                  'columns':
                    ( 'modelId', 'uuid', 'name', 'projectUuid', 'importGroupUuid',
                      'fileVolumeUuid', 'alternateMasterUuid', 'originalVersionUuid',
                      'originalVersionName', 'fileName', 'type', 'subtype',
                      'fileIsReference', 'isExternallyEditable', 'isTrulyRaw',
                      'isMissing', 'hasAttachments', 'hasNotes', 'hasFocusPoints',
                      'imagePath', 'fileSize', 'pixelFormat',
                      'duration', 'imageDate', 'fileCreationDate', 'fileModificationDate',
                      'imageHash', 'originalFileName', 'originalFileSize', 'imageFormat',
                      'importedBy', 'createDate', 'isInTrash', 'faceDetectionState',
                      'colorSpaceName', 'colorSpaceDefinition', 'fileAliasData',
                      'streamAssetId', 'streamSourceUuid', 'burstUuid',
                    ),
                },
              'vface':
                { 'table_name': 'RKVersionFaceContent',
                  'mixin': VFace_Mixin,
                  'columns':
                    ( 'modelId', 'versionId', 'masterId', 'isNamed', 'faceKey', 'faceIndex', 'faceRectLeft', 'faceRectTop', 'faceRectWidth', 'faceRectHeight',
                    ),
                },
              'folder':
                { 'table_name': 'RKFolder',
                  'columns':
                    ( 'modelId', 'uuid', 'folderType', 'name', 'parentFolderUuid',
                      'implicitAlbumUuid', 'posterVersionUuid',
                      'automaticallyGenerateFullSizePreviews', 'versionCount',
                      'minImageTimeZoneName', 'maxImageTimeZoneName', 'minImageDate',
                      'maxImageDate', 'folderPath', 'createDate', 'isExpanded',
                      'isHidden', 'isHiddenWhenEmpty', 'isFavorite', 'isInTrash',
                      'isMagic', 'colorLabelIndex', 'sortAscending', 'sortKeyPath',
                    ),
                },
              'keyword':
                { 'table_name': 'RKKeyword',
                  'mixin': Keyword_Mixin,
                  'columns':
                    ( 'modelId', 'uuid', 'name', 'searchName', 'parentId',
                      'hasChildren', 'shortcut',
                    ),
                },
              'album':
                { 'table_name': 'RKAlbum',
                  'columns':
                    ( 'modelId', 'uuid', 'albumType', 'albumSubclass', 'serviceName',
                      'serviceAccountName', 'serviceFullName', 'name', 'folderUuid',
                      'queryFolderUuid', 'posterVersionUuid', 'selectedTrackPathUuid',
                      'sortKeyPath', 'sortAscending', 'customSortAvailable',
                      'versionCount', 'createDate', 'isFavorite', 'isInTrash', 'isHidden',
                      'isMagic', 'publishSyncNeeded', 'colorLabelIndex',
                      'faceSortKeyPath', 'recentUserChangeDate', 'filterData',
                      'queryData', 'viewData', 'selectedVersionIds',
                    ),
                },
              'version':
                { 'table_name': 'RKVersion',
                  'mixin': Version_Mixin,
                  'columns':
                    ( 'modelId', 'uuid', 'name', 'fileName',
                      'versionNumber', 'stackUuid', 'masterUuid',
                      'masterId', 'rawMasterUuid', 'nonRawMasterUuid',
                      'projectUuid', 'imageTimeZoneName', 'imageDate',
                      'mainRating', 'isHidden', 'isFlagged', 'isOriginal',
                      'isEditable', 'colorLabelIndex', 'masterHeight',
                      'masterWidth', 'processedHeight', 'processedWidth',
                      'rotation', 'hasAdjustments', 'hasEnabledAdjustments',
                      'hasNotes', 'createDate', 'exportImageChangeDate',
                      'exportMetadataChangeDate', 'isInTrash',
                      'thumbnailGroup', 'overridePlaceId', 'exifLatitude',
                      'exifLongitude', 'renderVersion', 'adjSeqNum',
                      'supportedStatus', 'videoInPoint', 'videoOutPoint',
                      'videoPosterFramePoint', 'showInLibrary',
                      'editState', 'contentVersion', 'propertiesVersion',
                      'rawVersion', 'faceDetectionIsFromPreview',
                      'faceDetectionRotationFromMaster', 'editListData',
                      'hasKeywords',
                    ),
                },
              'keywordForVersion':
                { 'table_name': 'RKKeywordForVersion',
                  'columns':
                    ( 'modelId', 'versionId', 'keywordId'),
                },
            }
          }

if __name__ == '__main__':
  sys.exit(main(sys.argv))
