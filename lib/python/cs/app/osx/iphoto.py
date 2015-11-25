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
  for master in I.masters():
    print('uuid =', master.uuid, 'name =', master.name)

SCHEMAE = {'Library':
            { 'RKMaster':
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
              'RKAlbum':
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
          }

class iPhotoDB(object):

  def __init__(self, iphoto, dbname):
    global SCHEMAE
    self.iphoto = iphoto
    self.name = dbname
    self.dbpath = iphoto.dbpath(dbname)
    self.conn = sqlite3.connect(self.dbpath)
    self.schema = SCHEMAE[dbname]
    self.table_row_classes = {}
    for table_name in self.schema.keys():
      self.table_row_classes[table_name] = namedtuple('%s_Row' % (table_name,),
                                                      self.schema[table_name])

  def table_rows(self, table_name):
    row_class = self.table_row_classes.get(table_name, lambda *row: row)
    for row in self.conn.cursor().execute('select * from %s' % (table_name,)):
      yield row_class(*row)

class iPhotoDBs(object):

  def __init__(self, iphoto):
    self.iphoto = iphoto
    self.dbmap = {}
    self._lock = iphoto._lock

  @property
  def dbdirpath(self):
   return os.path.join(self.iphoto.path, 'Database', 'apdb')

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

  def dbnames(self):
    return self.dbs.dbnames()

  def dbpath(self, dbname):
    return self.dbs.pathto(dbname)

  def albums(self):
    return list(self.dbs.Library.table_rows('RKAlbum'))

  def masters(self):
    return list(self.dbs.Library.table_rows('RKMaster'))

if __name__ == '__main__':
  sys.exit(main(sys.argv))
