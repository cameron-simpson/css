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
from itertools import takewhile
from cs.fileutils import lockfile
from cs.inttypes import Flags
from cs.lex import unctrl
from cs.logutils import D, Pfx, info, warning, error, X
from cs.seq import last
from . import totext, fromtext
from .blockify import blockify, top_block_for
from .dir import decode_Dirent_text, Dir, FileDirent
from .file import filedata
from .paths import resolve, path_split, walk

CopyModes = Flags('delete', 'do_mkdir', 'replace', 'ignore_existing', 'trust_size_mtime')

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

def update_archive(arpath, ospath, modes, create_archive=False, arsubpath=None):
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
      copy_in_dir(E, ospath, modes)
    else:
      copy_in_file(E, ospath, modes)

    # save archive state
    save_Dirent(arpath, rootE)

def save_Dirent(path, E, when=None):
  ''' Save the supplied Dirent `E` to the file `path` with timestamp `when` (default now).
  '''
  with lockfile(path):
    with open(path, "a") as fp:
      write_Dirent(fp, E, when=when)

def read_Dirents(fp):
  ''' Generator to yield (unixtime, Dirent) from an open archive file.
  '''
  lineno = 0
  for line in fp:
    with Pfx("%s:%d", fp, lineno):
      lineno += 1
      if not line.endswith('\n'):
        raise ValueError("incomplete? no trailing newline")
      line = line.rstrip()
      # allow optional trailing text, which will be the E.name part normally
      isodate, unixtime, dent = line.split(None, 3)[:3]
      when = float(unixtime)
      E = decode_Dirent_text(dent)
    # note: yield _outside_ Pfx
    yield when, E

def last_Dirent(arpath):
  ''' Return the latest archive entry.
  '''
  try:
    with open(arpath, "r") as arfp:
      try:
        return last(read_Dirents(arfp))
      except IndexError:
        return None
  except OSError as e:
    if e.errno != errno.ENOENT:
      raise
  return None

def write_Dirent(fp, E, when=None):
  ''' Write a Dirent to an open archive file:
        isodatetime unixtime totext(dirent) dirent.name
  '''
  if when is None:
    when = time.time()
  fp.write(datetime.fromtimestamp(when).isoformat())
  fp.write(' ')
  fp.write(str(when))
  fp.write(' ')
  fp.write(E.textencode())
  fp.write(' ')
  fp.write(unctrl(E.name))
  fp.write('\n')

def copy_in_dir(rootD, rootpath, modes):
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
        if not os.path.isdir(ospath):
          warning("not a directory? SKIPPED")
          continue

        # get matching Dir from store
        if ospath == rootpath:
          dirD = rootD
        elif ospath.startswith(rootpath_prefix):
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
              info("delete %s", name)
              del dirD[name]

        for dirname in sorted(dirnames):
          with Pfx("%s/", dirname):
            if dirname not in dirD:
              dirD.mkdir(dirname)
            else:
              E = dirD[dirname]
              if not E.isdir:
                # old name is not a dir - toss it and make a dir
                del dirD[dirname]
                E = dirD.mkdir(dirname)

        for filename in sorted(filenames):
          with Pfx(filename):
            if modes.ignore_existing and filename in dirD:
              info("skipping, already Stored")
              continue
            filepath = os.path.join(ospath, filename)
            if not os.path.isfile(filepath):
              warning("not a regular file, skipping")
              continue
            matchBlocks = None
            if filename not in dirD:
              fileE = FileDirent(filename)
              dirD[filename] = fileE
            else:
              fileE = dirD[filename]
              if modes.trust_size_mtime:
                B = fileE.block
                M = fileE.meta
                st = os.stat(filepath)
                if st.st_mtime == M.mtime and st.st_size == B.span:
                  info("skipping, same mtime and size")
                  continue
                else:
                  error("DIFFERING size/mtime: B.span=%d/M.mtime=%s VS st_size=%d/st_mtime=%s",
                    B.span, M.mtime, st.st_size, st.st_mtime)
            try:
              copy_in_file(fileE, filepath, modes)
            except OSError as e:
              error(str(e))
              continue
            except IOError as e:
              error(str(e))
              continue

def copy_in_file(E, filepath, modes):
  ''' Store the file named `filepath` over the FileDirent `E`.
  '''
  with Pfx(filepath):
    with open(filepath, "rb") as fp:
      B = E.block = top_block_for(_blockify_file(fp, E))
      st = os.fstat(fp.fileno())
    if B.span != st.st_size:
      warning("MISMATCH: B.span=%d, st_size=%d", B.span, st.st_size)
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
    yield B
  # blockify the remaining file data
  chunks = filedata(fp)
  if data is not None:
    chunks = chain( [data], chunks )
  for B in blockify(chunks):
    yield B

def copy_out_dir(rootD, rootpath, modes=None):
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
            copy_out_file(E, filepath, modes)
        # apply the metadata again
        thisD.meta.apply_posix(dirpath)

def copy_out_file(E, ospath, modes=None):
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
    X("matching mtime and size, not overwriting")
    return
  # create or overwrite the file
  # TODO: backup mode in case of write errors?
  with Pfx(ospath):
    X("rewrite %r", ospath)
    Blen = len(B)
    with open(ospath, "wb") as fp:
      wrote = 0
      for chunk in B.chunks:
        X("%s: chunk len %d", ospath, len(chunk))
        fp.write(chunk)
        wrote += len(chunk)
    if Blen != wrote:
      error("Block len = %d, wrote %d", Blen, wrote)
    M.apply_posix(ospath)
