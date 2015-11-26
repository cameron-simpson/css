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
import sqlite3
from threading import RLock
from cs.env import envsub
from cs.logutils import Pfx, warning, error, setup_logging, XP
from cs.obj import O
from cs.threads import locked, locked_property

DEFAULT_LIBRARY = '$HOME/Pictures/iPhoto Library.photolibrary'

USAGE = '''Usage: %s [/path/to/iphoto-library-path] op [op-args...]
  dump dbname   Dump database rows in CSV format.
  ls            List apdb names.
  ls albums     List album names.
'''

def main(argv=None):
  ''' Main program associate with the cs.app.osx.iphoto module.
  '''
  if argv is None:
    argv = [ 'cs.app.osx.iphoto' ]
  cmd = os.path.basename(argv.pop(0))
  usage = USAGE % (cmd,)
  setup_logging(cmd)
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
      if op == 'ls':
        if not argv:
          for dbname in sorted(I.dbnames()):
            print(dbname)
        else:
          obclass = argv.pop(0)
          with Pfx(obclass):
            if obclass == 'albums':
              names = [ A.name for A in I.albums ]
            else:
              warning("unknown class %r", obclass)
              badopts = True
            if argv:
              warning("extra arguments: %r", argv)
              badopts = True
            if not badopts:
              for name in sorted(names):
                print(name)
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
  for album in I.albums.read_rows():
    print('uuid =', album.uuid, 'albumType =', album.albumType, 'name =', album.name)
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
    self.dbs._load_all()

  def pathto(self, rpath):
    if rpath.startswith('/'):
      raise ValueError('rpath may not start with a slash: %r' % (rpath,))
    return os.path.join(self.path, rpath)

  def dbnames(self):
    return self.dbs.dbnames()

  def dbpath(self, dbname):
    return self.dbs.pathto(dbname)

  def __getattr__(self, attr):
    # read_*s ==> iterator of rows from table "*"
    if attr.startswith('read_') and attr.endswith('s'):
      nickname = attr[5:-1]
      return self.table_by_nickname[nickname].read_rows
    # *s ==> table "*"
    if attr.endswith('s') and attr[:-1] in self.table_by_nickname:
      return self.table_by_nickname[attr[:-1]]
    raise AttributeError(attr)

  def folders(self):
    return list(self.dbs.Library.table_rows('RKFolder'))

  def keywords(self):
    return list(self.dbs.Library.table_rows('RKKeyword'))

  def masters(self):
    return list(self.dbs.Library.table_rows('RKMaster'))

  def versions(self):
    return list(self.dbs.Library.table_rows('RKVersion'))

  def keywords_to_versions(self):
    return list(self.dbs.Library.table_rows('RKKeywordForVersion'))

class iPhotoDBs(object):

  def __init__(self, iphoto):
    self.iphoto = iphoto
    self.dbmap = {}
    self.named_tables = {}
    self._lock = iphoto._lock

  def _load_all(self):
    for dbname in 'Library',:
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
      table_name = schema['table_name']
      klass = namedtuple('%s_Row' % (table_name,), ['I'] + list(schema['columns']))
      mixin = schema.get('mixin')
      if mixin is not None:
        class Mixed(klass, mixin):
          pass
        klass = Mixed
      self.table_row_classes[table_name] = klass

  def table_rows(self, table_name):
    I = self.iphoto
    row_class = self.table_row_classes.get(table_name, lambda *row: row)
    for row in self.conn.cursor().execute('select * from %s' % (table_name,)):
      yield row_class(*([I] + list(row)))

class iPhotoTable(object):

  def __init__(self, db, nickname, schema):
    self.nickname = nickname
    self.db = db
    self.schema = schema
    table_name = schema['table_name']
    klass = namedtuple('%s_Row' % (table_name,), ['I'] + list(schema['columns']))
    mixin = schema.get('mixin')
    if mixin is not None:
      class Mixed(klass, mixin):
        pass
      klass = Mixed
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

SCHEMAE = {'Library':
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
