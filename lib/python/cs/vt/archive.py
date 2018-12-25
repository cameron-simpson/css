#!/usr/bin/python

''' Archive files.

    Archive files are records of data saved to a Store.
    Lines are appended to the archive file of the form:

      isodatetime unixtime dirent

    where unixtime is UNIX time (seconds since epoch) and dirent is the text
    transcription of a Dirent.
'''

from __future__ import print_function
from datetime import datetime
import errno
import os
from os.path import realpath, isfile
import time
from cs.fileutils import lockfile, shortpath
from cs.inttypes import Flags
from cs.lex import unctrl
from cs.logutils import warning, exception, debug
from cs.pfx import Pfx, gen as pfxgen
from cs.py.func import prop
from .dir import _Dirent, DirFTP
from .meta import NOUSERID, NOGROUPID

CopyModes = Flags('delete', 'do_mkdir', 'trust_size_mtime')

# shared mapping of archive paths to Archive instances
_ARCHIVES = {}

def Archive(path, mapping=None, missing_ok=False, weird_ok=False):
  ''' Return an Archive for the named file.
      Maintains a mapping of issued Archives in order to reuse that
      same Archive for a given path.
  '''
  global _ARCHIVES
  if not path.endswith('.vt'):
    if weird_ok:
      warning("unusual Archive path: %r", path)
    else:
      raise ValueError("invalid Archive path (should end in '.vt'): %r" % (path,))
  if not missing_ok and not isfile(path):
    raise ValueError("not a file: %r" % (path,))
  if mapping is None:
    mapping = _ARCHIVES
  path = realpath(path)
  A = mapping.get(path)
  if A is None:
    mapping[path] = A = _Archive(path)
  return A

class _Archive(object):
  ''' Manager for an archive.vt file.
  '''

  def __init__(self, arpath):
    ''' Initialise this Archive.
        `arpath`: path to file holding the archive records
    '''
    self.path = arpath
    self._last = None
    self._last_s = None
    self.notify_update = []

  def __str__(self):
    return "Archive(%s)" % (shortpath(self.path),)

  @prop
  def last(self):
    ''' Return the last (unixtime, Dirent) from the file, or (None, None).
    '''
    last_entry = self._last
    if last_entry is None:
      for entry in self:
        last_entry = entry
      if last_entry is None:
        return None, None
      self._last = last_entry
    return last_entry

  @pfxgen
  def __iter__(self):
    ''' Generator yielding (unixtime, Dirent) from the archive file.
    '''
    path = self.path
    with Pfx(path):
      try:
        with open(path) as fp:
          entries = self.parse(fp)
          for when, E in entries:
            if when is None and E is None:
              break
            yield when, E
      except OSError as e:
        if e.errno == errno.ENOENT:
          return
        raise

  def update(self, E, when=None, previous=None, force=False, source=None):
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
    assert isinstance(E, _Dirent), "expected E<%s> to be a _Dirent" % (type(E),)
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
    path = self.path
    with lockfile(path):
      with open(path, "a") as fp:
        s = self.write(fp, E, when=when, etc=etc)
    self._last = when, E
    self._last_s = s
    for notify in self.notify_update:
      try:
        notify(E, when=when, source=source)
      except Exception as e:
        exception(
            "notify[%s](%s,when=%s,source=%s): %s",
            notify, E, when, source, e)
    return s

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
    if isinstance(E, str):
      Es = E
    else:
      Es = str(E)
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

  @staticmethod
  @pfxgen
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
        yield when, E

  @staticmethod
  def read(fp, first_lineno=1):
    ''' Read the next entry from an open archive file, return (when, E).
        Return (None, None) at EOF.
    '''
    for when, E in _Archive.parse(fp, first_lineno=first_lineno):
      return when, E
    return None, None

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
            "chown(%r,%d,%d) from %d:%d",
            ospath, uid, gid, path_st.st_uid, path_st.st_gid)
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
              "utime(%r,atime=%s,mtime=%s) from mtime=%s",
              ospath, path_st.st_atime, mst_mtime, st_mtime)
          os.utime(ospath, (path_st.st_atime, mst_mtime))

class ArchiveFTP(DirFTP):
  ''' Initial sketch for FTP interface to an Archive.
  '''

  def __init__(self, arpath, prompt=None):
    self.path = arpath
    self.archive = Archive(arpath)
    if prompt is None:
      prompt = shortpath(arpath)
    _, rootD = self.archive.last
    self.rootD = rootD
    super().__init__(rootD, prompt=prompt)

  def postloop(self):
    ''' Sync the Archive at the end of the FTP session.
    '''
    super().postloop()
    self.archive.update(self.rootD)
