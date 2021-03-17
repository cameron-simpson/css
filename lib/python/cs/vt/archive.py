#!/usr/bin/python

''' Archive files.

    Archive files are records of data saved to a Store.
    Lines are appended to the archive file of the form:

      isodatetime unixtime dirent

    where unixtime is UNIX time (seconds since epoch) and dirent is the text
    transcription of a Dirent.
'''

from __future__ import print_function
from abc import ABC, abstractmethod
from datetime import datetime
import errno
import os
from os.path import isfile
import time
from icontract import require
from cs.binary import BinaryMultiValue, BSSFloat
from cs.fileutils import lockfile, shortpath
from cs.inttypes import Flags
from cs.lex import unctrl, get_ini_clause_entryname
from cs.logutils import warning, exception, debug
from cs.pfx import Pfx, pfx
from cs.py.func import prop
from .dir import _Dirent, DirentRecord
from .meta import NOUSERID, NOGROUPID

CopyModes = Flags('delete', 'do_mkdir', 'trust_size_mtime')

class ArchiveEntry(BinaryMultiValue('ArchiveEntry',
                                    dict(when=BSSFloat, dirent=DirentRecord))):
  ''' An Archive entry record.
  '''

def Archive(path, missing_ok=False, weird_ok=False, config=None):
  ''' Return an Archive from the specification `path`.

      If the `path` begins with `'['`
      then it is presumed to be a Store Archive
      obtained via the Store's `.get_Archive(name)` method
      and the `path` should have the form:

          [clausename]name

      where *clausename* is a configuration clause name
      and *name* is an identifier used to specify an Archive
      associated with the Store.
  '''
  if path.startswith('['):
    # expect "[clausename]name"
    clause_name, archive_name, offset = get_ini_clause_entryname(path)
    if offset < len(path):
      raise ValueError(
          "unparsed text after archive name: %r" % (path[offset:],)
      )
    S = config[clause_name]
    return S.get_Archive(archive_name, missing_ok=missing_ok)
  # otherwise a file pathname
  if not path.endswith('.vt'):
    if weird_ok:
      warning("unusual Archive path: %r", path)
    else:
      raise ValueError(
          "invalid Archive path (should end in '.vt'): %r" % (path,)
      )
  if not missing_ok and not isfile(path):
    raise ValueError("not a file: %r" % (path,))
  return FilePathArchive(path)

class BaseArchive(ABC):
  ''' Abstract base class for StoreArchive and FileArchive.
  '''

  def __init__(self):
    self._last = None
    self._last_s = None
    self.notify_update = []

  @abstractmethod
  def __iter__(self):
    raise NotImplementedError("no .__iter__")

  @prop
  def last(self):
    ''' The last ArchiveEntry from the Archive, or ArchiveEntry(None,None).
    '''
    last_entry = self._last
    if last_entry is None:
      for entry in self:
        last_entry = entry
      if last_entry is None:
        return ArchiveEntry(when=None, dirent=None)
      self._last = last_entry
    return last_entry

  @staticmethod
  @pfx
  def parse(fp, first_lineno=1):
    ''' Parse lines from an open archive file, yield `(when, E)`.
    '''
    for lineno, line in enumerate(fp, first_lineno):
      with Pfx(str(lineno)):
        if not line.endswith('\n'):
          raise ValueError("incomplete? no trailing newline")
        line = line.strip()
        if not line or line.startswith('#'):
          continue
        # allow optional trailing text, which will be the E.name part normally
        fields = line.split(None, 3)
        _, unixtime, dent = fields[:3]
        when = float(unixtime)
        E, offset = _Dirent.from_str(dent)
        if offset != len(dent):
          warning("unparsed dirent text: %r", dent[offset:])
        ##info("when=%s, E=%s", when, E)
        yield ArchiveEntry(when=when, dirent=E)

  @staticmethod
  def read(fp, first_lineno=1):
    ''' Read the next entry from an open archive file, return (when, E).
        Return (None, None) at EOF.
    '''
    for entry in BaseArchive.parse(fp, first_lineno=first_lineno):
      return entry
    return ArchiveEntry(when=None, dirent=None)

  @staticmethod
  def write(fp, E, when=None, etc=None):
    ''' Write a Dirent to an open archive file. Return the Dirent transcription.

        Archive lines have the form:

            isodatetime unixtime transcribe(dirent) dirent.name
    '''
    if when is None:
      when = time.time()
    # produce a local time with knowledge of its timezone offset
    dt = datetime.fromtimestamp(when).astimezone()
    assert dt.tzinfo is not None
    # precompute strings to avoid corrupting the archive file
    iso_s = dt.isoformat()
    when_s = str(when)
    Es = E if isinstance(E, str) else str(E)
    etc_s = None if etc is None else unctrl(etc)
    fp.write(iso_s)
    fp.write(' ')
    fp.write(when_s)
    fp.write(' ')
    fp.write(Es)
    if etc is not None:
      fp.write(' ')
      fp.write(etc_s)
    fp.write('\n')
    fp.flush()
    return Es

  def append(self, E, when, etc):
    ''' The append method should add an update to the Archive.
    '''
    raise NotImplementedError("no .append")

  def update(self, E, *, when=None, previous=None, force=False, source=None):
    ''' Save the supplied Dirent `E` with timestamp `when`.
        Return the Dirent transcription.

        Parameters:
        * `E`: the Dirent to save.
        * `when`: the POSIX timestamp for the save, default now.
        * `previous`: optional previous Dirent transcription; defaults
          to the latest Transcription from of the Archive
        * `force`: append an entry even if the previous entry has the
          same transcription as `previous`, default False
        * `source`: optional source indicator for the update, default None
    '''
    assert isinstance(E,
                      _Dirent), "expected E<%s> to be a _Dirent" % (type(E),)
    etc = E.name
    if not force:
      # see if we should discard this update
      if previous is None:
        previous = self._last_s
      if previous is not None:
        # do not save if the previous transcription is unchanged
        Es = str(E)
        if Es == previous:
          return Es
    if when is None:
      when = time.time()
    s = self.append(E, when, etc)
    self._last = when, E
    self._last_s = s
    for notify in self.notify_update:
      try:
        notify(E, when=when, source=source)
      except Exception as e:
        exception(
            "notify[%s](%s,when=%s,source=%s): %s", notify, E, when, source, e
        )
    return s

class FilePathArchive(BaseArchive):
  ''' Manager for an archive.vt file.
  '''

  def __init__(self, arpath):
    ''' Initialise this Archive.

        `arpath`: path to file holding the archive records.
    '''
    super().__init__()
    self.path = arpath

  def __str__(self):
    return "%s(%s)" % (type(self).__name__, shortpath(self.path))

  @pfx
  def __iter__(self):
    ''' Generator yielding (unixtime, Dirent) from the archive file.
    '''
    path = self.path
    with Pfx(path):
      try:
        with open(path) as fp:
          yield from self.parse(fp)
      except OSError as e:
        if e.errno == errno.ENOENT:
          return
        raise

  def append(self, E, when, etc):
    ''' Append an update to the fle.
    '''
    path = self.path
    with lockfile(path):
      with open(path, "a") as fp:
        s = self.write(fp, E, when=when, etc=etc)
    return s

class FileOutputArchive(BaseArchive):
  ''' An Archive which just writes updates to an open file.
  '''

  def __init__(self, fp):
    super().__init__()
    self.fp = fp

  def __str__(self):
    return "%s(%s)" % (type(self).__name__, self.fp)

  def __iter__(self):
    return iter(())

  def append(self, E, when, etc):
    ''' Append an update to the fle.
    '''
    s = self.write(self.fp, E, when=when, etc=etc)
    return s

def apply_posix_stat(src_st, ospath):
  ''' Apply a stat object to the POSIX OS object at `ospath`.
  '''
  with Pfx("apply_posix_stat(%r)", ospath):
    path_st = os.stat(ospath)
    if src_st.st_uid == NOUSERID or src_st.st_uid == path_st.st_uid:
      uid = -1
    else:
      uid = src_st.st_uid
    if src_st.st_gid == NOGROUPID or src_st.st_gid == path_st.st_gid:
      gid = -1
    else:
      gid = src_st.st_gid
    if uid != -1 or gid != -1:
      with Pfx("chown(uid=%d,gid=%d)", uid, gid):
        debug(
            "chown(%r,%d,%d) from %d:%d", ospath, uid, gid, path_st.st_uid,
            path_st.st_gid
        )
        try:
          os.chown(ospath, uid, gid)
        except OSError as e:
          if e.errno == errno.EPERM:
            warning("%s", e)
          else:
            raise
    st_perms = path_st.st_mode & 0o7777
    mst_perms = src_st.st_mode & 0o7777
    if st_perms != mst_perms:
      with Pfx("chmod(0o%04o)", mst_perms):
        debug("chmod(%r,0o%04o) from 0o%04o", ospath, mst_perms, st_perms)
        os.chmod(ospath, mst_perms)
    mst_mtime = src_st.st_mtime
    if mst_mtime > 0:
      st_mtime = path_st.st_mtime
      if mst_mtime != st_mtime:
        with Pfx("chmod(0o%04o)", mst_perms):
          debug(
              "utime(%r,atime=%s,mtime=%s) from mtime=%s", ospath,
              path_st.st_atime, mst_mtime, st_mtime
          )
          os.utime(ospath, (path_st.st_atime, mst_mtime))
