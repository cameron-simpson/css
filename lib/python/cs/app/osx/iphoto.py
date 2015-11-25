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
  for album in I.albums():
    print('uuid =', album.uuid, 'albumType =', album.albumType, 'name =', album.name)
  for folder in I.folders():
    print('uuid =', folder.uuid, 'folderType =', folder.folderType, 'name =', folder.name)
  for keyword in I.keywords():
    print('uuid =', keyword.uuid, 'name =', keyword.name)
  ##for master in I.masters():
  ##  print('uuid =', master.uuid, 'name =', master.name, 'originalFileName =', master.originalFileName, 'imagePath =', master.imagePath, 'pathname =', master.pathname)
  for v in I.versions():
    print('uuid =', v.uuid, 'masterUuid =', v.masterUuid)
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
    self._lock = RLock()
    self.dbs = iPhotoDBs(self)

  def pathto(self, rpath):
    if rpath.startswith('/'):
      raise ValueError('rpath may not start with a slash: %r' % (rpath,))
    return os.path.join(self.path, rpath)

  def dbnames(self):
    return self.dbs.dbnames()

  def dbpath(self, dbname):
    return self.dbs.pathto(dbname)

  def albums(self):
    return list(self.dbs.Library.table_rows('RKAlbum'))

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
    self._lock = iphoto._lock

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

  @locked
  def __getattr__(self, attr):
    dbmap = self.dbmap
    if attr in dbmap:
      return dbmap[attr]
    dbpath = self.pathto(attr)
    if os.path.exists(dbpath):
      db = iPhotoDB(self.iphoto, attr)
      dbmap[attr] = db
      return db
    raise AttributeError(attr)

class iPhotoDB(object):

  def __init__(self, iphoto, dbname):
    global SCHEMAE
    self.iphoto = iphoto
    self.name = dbname
    self.dbpath = iphoto.dbpath(dbname)
    self.conn = sqlite3.connect(self.dbpath)
    self.schema = SCHEMAE[dbname]
    self.table_row_classes = {}
    for table_name, schema in self.schema.items():
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

class Master_Mixin(object):

  @property
  def pathname(self):
    return os.path.join(self.I.pathto('Masters'), self.imagePath)

SCHEMAE = {'Library':
            { 'RKMaster':
                {
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
              'RKFolder':
                { 'columns':
                    ( 'modelId', 'uuid', 'folderType', 'name', 'parentFolderUuid',
                      'implicitAlbumUuid', 'posterVersionUuid',
                      'automaticallyGenerateFullSizePreviews', 'versionCount',
                      'minImageTimeZoneName', 'maxImageTimeZoneName', 'minImageDate',
                      'maxImageDate', 'folderPath', 'createDate', 'isExpanded',
                      'isHidden', 'isHiddenWhenEmpty', 'isFavorite', 'isInTrash',
                      'isMagic', 'colorLabelIndex', 'sortAscending', 'sortKeyPath',
                    ),
                },
              'RKKeyword':
                { 'columns':
                    ( 'modelId', 'uuid', 'name', 'searchName', 'parentId',
                      'hasChildren', 'shortcut',
                    ),
                },
              'RKAlbum':
                { 'columns':
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
              'RKVersion':
                { 'columns':
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
              'RKKeywordForVersion':
                { 'columns':
                    ( 'modelId', 'versionId', 'keywordId'),
                },
            }
          }

if __name__ == '__main__':
  sys.exit(main(sys.argv))
