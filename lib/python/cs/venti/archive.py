#!/usr/bin/python

''' Archive files for venti data.

    Archive files are records of data saved to a Store.
    Lines are appended to the archive file of the form:

      isodatetime unixtime dirent

    where unixtime is UNIX time (seconds since epoch) and dirent is the text
    encoding of a cs.venti.dir.Dirent.
'''

from __future__ import print_function
import os
import stat
import sys
import time
from datetime import datetime
import errno
from itertools import chain
from cs.fileutils import lockfile, shortpath
from cs.inttypes import Flags
from cs.lex import unctrl
from cs.logutils import D, info, warning, error
from cs.pfx import Pfx
from cs.seq import last
from cs.x import X
from . import totext, fromtext
from .block import dump_block
from .blockify import blockify, top_block_for
from .dir import decode_Dirent_text, Dir, FileDirent, DirFTP
from .file import filedata
from .paths import resolve, path_split, walk

CopyModes = Flags('delete', 'do_mkdir', 'trust_size_mtime')

def toc_archive(arpath, paths=None, verbose=False, fp=None):
  if fp is None:
    fp = sys.stdout
  with Pfx(arpath):
    last_entry = last_Dirent(arpath)
    if last_entry is None:
      error("no entries in archive")
      return 1
  when, rootD = last_entry
  for thisD, relpath, dirs, files in walk(rootD, topdown=True):
    print((relpath if len(relpath) else '.'), thisD.meta)
    for name in files:
      E = thisD[name]
      print(os.path.join(relpath, name), E.meta)
  return 0

def update_archive(arpath, ospath, modes, create_archive=False, arsubpath=None, log=None):
  ''' Update the archive file `arpath` from `ospath`.
     `ospath` is taken to match with the top of the archive plus the `arsubpath` if supplied.
  '''
  with Pfx("update %r <== %r", arpath, ospath):
    base = os.path.basename(ospath)
    # stat early once, fail early if necessary
    st = os.stat(ospath)
    if stat.S_ISDIR(st.st_mode):
      isdir = True
    elif stat.S_ISREG(st.st_mode):
      isdir = False
    else:
      raise ValueError("unsupported OS file type 0o%o, expected file or directory" % (st.st_mode,))

    # load latest archive root
    last_entry = last_Dirent(arpath)

    # prep the subpath components
    if arsubpath is not None:
      subpaths = path_split(arsubpath)
    else:
      subpaths = []

    if last_entry is not None:
      # attach to entry
      when, rootE = last_entry
    elif subpaths:
      # new archive point: make dir if subpath
      rootE = Dir('.')
    elif isdir:
      # new archive point: make dir if source is dir
      rootE = Dir(base)
    else:
      # new archive point: make file
      rootE = FileDirent(base)

    # create subdirectories
    E = rootE
    while len(subpaths) > 1:
      name = subpaths.pop(0)
      subE = E.get(name)
      if subE is None or not subE.isdir:
        subE = E.mkdir(name)
      E = subE

    # create leaf node
    if subpaths:
      name, = subpaths
      subE = E.get(name)
      if isdir and (subE is None or not subE.isdir):
        if subE is not None:
          del E[name]
        subE = E.mkdir(name)
      elif not isdir and (subE is None or not subE.isfile):
        subE = E[name] = FileDirent(name)
      E = subE

    # update target node
    if isdir:
      copy_in_dir(E, ospath, modes, log=log)
    else:
      copy_in_file(E, ospath, modes, log=log)

    # save archive state
    save_Dirent(arpath, rootE)

def save_Dirent(fp, E, when=None):
  ''' Save the supplied Dirent `E` to the file `path` (open file or pathname) with timestamp `when` (default now).
  '''
  if isinstance(fp, str):
    path = fp
    with lockfile(path):
      with open(path, "a") as fp:
        return save_Dirent(fp, E, when=when)
  return write_Dirent(fp, E, when=when)

def read_Dirents(fp):
  ''' Generator to yield (unixtime, Dirent) from an open archive file.
  '''
  for lineno, line in enumerate(fp, 1):
    with Pfx("%s:%d", fp, lineno):
      if not line.endswith('\n'):
        raise ValueError("incomplete? no trailing newline")
      line = line.rstrip()
      # allow optional trailing text, which will be the E.name part normally
      isodate, unixtime, dent = line.split(None, 3)[:3]
      when = float(unixtime)
      E = decode_Dirent_text(dent)
    # note: yield _outside_ Pfx
    yield when, E

def last_Dirent(arpath, missing_ok=False):
  ''' Return the latest archive entry.
  '''
  try:
    with open(arpath, "r") as arfp:
      try:
        return last(read_Dirents(arfp))
      except IndexError:
        return None, None
  except OSError as e:
    if e.errno == errno.ENOENT:
      if missing_ok:
        return None, None
    raise
  raise RuntimeError("NOTREACHED")

def strfor_Dirent(E):
  ''' Exposed function for 
  '''
  return E.textencode()

def write_Dirent(fp, E, when=None):
  ''' Write a Dirent to an open archive file; return the E.textencode() value used.
      Archive lines have the form:
        isodatetime unixtime totext(dirent) dirent.name
  '''
  encoded = strfor_Dirent(E)
  write_Dirent_str(fp, when, encoded, E.name)
  return encoded

def write_Dirent_str(fp, text, when=None, etc=None):
  if when is None:
    when = time.time()
  fp.write(datetime.fromtimestamp(when).isoformat())
  fp.write(' ')
  fp.write(str(when))
  fp.write(' ')
  fp.write(text)
  if etc is not None:
    fp.write(' ')
    fp.write(unctrl(etc))
  fp.write('\n')

def copy_in_dir(rootD, rootpath, modes, log=None):
  ''' Copy the os directory tree at `rootpath` over the Dir `rootD`.
      `modes` is an optional CopyModes value.
  '''
  with Pfx("copy_in(%s)", rootpath):
    if not os.path.isdir(rootpath):
      warning("not a directory?")
    rootpath_prefix = rootpath + '/'
    # TODO: try out scandir sometime
    for ospath, dirnames, filenames in os.walk(rootpath):
      with Pfx(ospath):
        dst = os.lstat(ospath)
        if not stat.S_ISDIR(dst.st_mode):
          warning("not a directory? SKIPPED")
          continue

        # get matching Dir from store
        if ospath == rootpath:
          relpath = '.'
          dirD = rootD
        elif ospath.startswith(rootpath_prefix):
          relpath = ospath[len(rootpath_prefix):]
          dirD, parent, subpaths = resolve(rootD, ospath[len(rootpath_prefix):])
          for name in subpaths:
            E = dirD.get(name)
            if E is None:
              dirD = dirD.chdir1(name)
            elif E.isdir:
              warning("surprise: resolve stopped at a subdir! subpaths=%r", subpaths)
              dirD = E
            else:
              del D[name]
              dirD = dirD.chdir1(name)
        else:
          warning("unexpected ospath, SKIPPED")
          continue

        if modes.delete:
          # Remove entries in dirD not present in the real filesystem
          allnames = set(dirnames)
          allnames.update(filenames)
          Dnames = sorted(dirD.keys())
          for name in Dnames:
            if name not in allnames:
              relsubname = os.path.join(relpath, name)
              log("delete %s", relsubname)
              del dirD[name]

        for dirname in sorted(dirnames):
          with Pfx("%s/", dirname):
            reldirname = os.path.join(relpath, dirname)
            if dirname not in dirD:
              log("mkdir  %s", reldirname)
              dirD.mkdir(dirname)
            else:
              E = dirD[dirname]
              if not E.isdir:
                # old name is not a dir - toss it and make a dir
                log("replace/mkdir %s", reldirname)
                del dirD[dirname]
                E = dirD.mkdir(dirname)

        for filename in sorted(filenames):
          with Pfx(filename):
            relfilename = os.path.join(relpath, filename)
            ##if modes.ignore_existing and filename in dirD:
            ##  log("skip     %s (already in archive)", relfilename)
            ##  continue
            filepath = os.path.join(ospath, filename)
            if not os.path.isfile(filepath):
              log("skip     %s (not regular file: %s)", relfilename, filepath)
              continue
            if filename not in dirD:
              fileE = FileDirent(filename)
              dirD[filename] = fileE
            else:
              fileE = dirD[filename]
              if modes.trust_size_mtime:
                B = fileE.block
                M = fileE.meta
                fst = os.stat(filepath)
                if fst.st_mtime == M.mtime and fst.st_size == B.span:
                  log("skipfile %s (same mtime/size)", relfilename)
                  continue
                else:
                  error("DIFFERING size/mtime: B.span=%d/M.mtime=%s VS st_size=%d/st_mtime=%s",
                    B.span, M.mtime, fst.st_size, fst.st_mtime)
            log("file     %s", relfilename)
            try:
              copy_in_file(fileE, filepath, modes)
            except OSError as e:
              error(str(e))
              continue
            except IOError as e:
              error(str(e))
              continue

        # finally, update the Dir meta info
        dirD.meta.update_from_stat(dst)

def copy_in_file(E, filepath, modes, log=None):
  ''' Store the file named `filepath` over the FileDirent `E`.
  '''
  with Pfx(filepath):
    with open(filepath, "rb") as fp:
      B = E.block = top_block_for(_blockify_file(fp, E))
      st = os.fstat(fp.fileno())
    if B.span != st.st_size:
      warning("MISMATCH: B.span=%d, st_size=%d", B.span, st.st_size)
      filedata = open(filepath, "rb").read()
      blockdata = B.all_data()
      X("len(filedata)=%d", len(filedata))
      X("len(blockdata)=%d", len(blockdata))
      open("data1file", "wb").write(filedata)
      open("data2block", "wb").write(blockdata)
      raise RuntimeError("ABORT")
    E.meta.update_from_stat(st)

def _blockify_file(fp, E):
  ''' Read data from the file `fp` and compare with the FileDirect `E`, yielding leaf Blocks for a new file.
      This underpins copy_in_file().
  '''
  # read file data in chunks matching the existing leaves
  # return the leaves while the data match
  data = None
  for B in E.block.leaves:
    data = fp.read(len(B))
    if len(data) == 0:
      # EOF
      return
    if len(data) != len(B):
      break
    if not B.matches_data(data):
      break
    data = None
    yield B
  # blockify the remaining file data
  chunks = filedata(fp)
  if data is not None:
    chunks = chain( [data], chunks )
  for B in blockify(chunks):
    yield B

def copy_out_dir(rootD, rootpath, modes=None, log=None):
  ''' Copy the Dir `rootD` onto the os directory `rootpath`.
      `modes` is an optional CopyModes value.
      Notes: `modes.delete` not implemented.
  '''
  if modes is None:
    modes = CopyModes()
  with Pfx("copy_out(rootpath=%s)", rootpath):
    for thisD, relpath, dirs, files in walk(rootD, topdown=True):
      if relpath:
        dirpath = os.path.join(rootpath, relpath)
      else:
        dirpath = rootpath
      with Pfx(dirpath):
        if not os.path.isdir(dirpath):
          if modes.do_mkdir:
            log("mkdir   %s", dirpath)
            try:
              os.mkdir(dirpath)
            except OSError as e:
              error("mkdir: %s", e)
              dirs[:] = ()
              files[:] = ()
              continue
          else:
            error("refusing to mkdir, not requested")
            dirs[:] = ()
            files[:] = ()
            continue
        # apply the metadata now in case of setgid etc
        thisD.meta.apply_posix(dirpath)
        for filename in sorted(files):
          with Pfx(filename):
            E = thisD[filename]
            if not E.isfile:
              warning("vt source is not a file, skipping")
              continue
            filepath = os.path.join(dirpath, filename)
            copy_out_file(E, filepath, modes, log=log)
        # apply the metadata again
        thisD.meta.apply_posix(dirpath)

def copy_out_file(E, ospath, modes=None, log=None):
  ''' Update the OS file `ospath` from the FileDirent `E` according to `modes`.
  '''
  if modes is None:
    modes = CopyModes()
  if not E.isfile:
    raise ValueError("expected FileDirent, got: %r" % (E,))
  try:
    # TODO: should this be os.stat if we don't support symlinks?
    st = os.lstat(ospath)
  except OSError as e:
    st = None
  else:
    if modes.ignore_existing:
      X("already exists, ignoring")
      return
  B = E.block
  M = E.meta
  if ( modes.trust_size_mtime
   and st is not None
   and ( M.mtime is not None and M.mtime == st.st_mtime
         and B.span == st.st_size
       )
     ):
    log("skip  %s (same size/mtime)", ospath)
    return
  # create or overwrite the file
  # TODO: backup mode in case of write errors?
  with Pfx(ospath):
    Blen = len(B)
    log("rewrite %s", ospath)
    with open(ospath, "wb") as fp:
      wrote = 0
      for chunk in B.chunks:
        fp.write(chunk)
        wrote += len(chunk)
    if Blen != wrote:
      error("Block len = %d, wrote %d", Blen, wrote)
    M.apply_posix(ospath)

class ArchiveFTP(DirFTP):

  def __init__(self, archivepath, prompt=None):
    if prompt is None:
      prompt = shortpath(archivepath)
    when, root = last_Dirent(archivepath, missing_ok=True)
    self._archivepath = archivepath
    super().__init__(root, prompt=prompt)

  def postloop(self):
    super().postloop()
    text = strfor_Dirent(self.root)
    with open(self._archivepath, "a") as afp:
      write_Dirent_str(afp, text, etc=self.root.name)
