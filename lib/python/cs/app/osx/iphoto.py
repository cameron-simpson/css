#!/usr/bin/python
#
# Access iPhoto data.
#       - Cameron Simpson <cs@zip.com.au> 04apr2013
#

import os
import os.path
from collections import namedtuple
import sqlite3
from threading import RLock
from cs.env import envsub
from cs.obj import O
from cs.threads import locked_property

DEFAULT_LIBRARY = '$HOME/Pictures/iPhoto Library.photolibrary'

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

  def apdb_path(self, name):
    return os.path.join(self.path, 'Database', 'apdb', name+'.apdb')

  def _apdb(self, name):
    return sqlite3.connect(self.apdb_path(name))

  @locked_property
  def big_blobs_db(self): return self._apdb('BigBlobs')
  @locked_property
  def faces_db(self): return self._apdb('Faces')
  @locked_property
  def history_db(self): return self._apdb('History')
  @locked_property
  def imageproxies_db(self): return self._apdb('ImageProxies')
  @locked_property
  def library_db(self): return self._apdb('Library')
  @locked_property
  def properties_db(self): return self._apdb('Properties')

  @locked_property
  def masters(self):
    return tuple( RKMaster(row)
                  for row
                  in self.library_db.cursor().execute('select * from RKMaster')
                )

  @locked_property
  def albums(self):
    return tuple( RKAlbum(row)
                  for row
                  in self.library_db.cursor().execute('select * from RKAlbum')
                )

class _DBRow(O):
  def __init__(self, dbrow):
    self._loadrow(dbrow)
  def __str__(self):
    return str(self._row)
  def __getattr__(self, attr):
    return getattr(self._row, attr)

_RKMaster = namedtuple('RKMaster',
                       'modelId, uuid, name, projectUuid, importGroupUuid, '
                       'fileVolumeUuid, alternateMasterUuid, originalVersionUuid, '
                       'originalVersionName, fileName, type, subtype, '
                       'fileIsReference, isExternallyEditable, isTrulyRaw, '
                       'isMissing, hasAttachments, hasNotes, hasFocusPoints, '
                       'imagePath, fileSize, pixelFormat, '
                       'duration, imageDate, fileCreationDate, fileModificationDate, '
                       'imageHash, originalFileName, originalFileSize, imageFormat, '
                       'importedBy, createDate, isInTrash, faceDetectionState, '
                       'colorSpaceName, colorSpaceDefinition, fileAliasData, '
                       'streamAssetId, streamSourceUuid')

class RKMaster(_DBRow):
  def _loadrow(self, dbrow):
    self._row = _RKMaster(*dbrow)

_RKAlbum = namedtuple('RKAlbum',
                      'modelId, uuid, albumType, albumSubclass, serviceName, '
                      'serviceAccountName, serviceFullName, name, folderUuid, '
                      'queryFolderUuid, posterVersionUuid, selectedTrackPathUuid, '
                      'sortKeyPath, sortAscending, customSortAvailable, '
                      'versionCount, createDate, isFavorite, isInTrash, isHidden, '
                      'isMagic, publishSyncNeeded, colorLabelIndex, '
                      'faceSortKeyPath, recentUserChangeDate, filterData, '
                      'queryData, viewData, selectedVersionIds')

class RKAlbum(_DBRow):
  def _loadrow(self, dbrow):
    self._row = _RKAlbum(*dbrow)

if __name__ == '__main__':
  I = iPhoto()
  for A in I.albums:
    print A.name
    if A.name and A.name.startswith('kw-'):
      print A
