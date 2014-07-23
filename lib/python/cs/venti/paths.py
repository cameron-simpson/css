#!/usr/bin/python
#
# Operations on pathnames using a Venti store.
#       - Cameron Simpson <cs@zip.com.au> 07may2013
#

import os
from cs.inttypes import Flags
from cs.logutils import Pfx, D, info, warning, error
from .file import file_top_block
from .dir import decode_Dirent_text, FileDirent

CopyModes = Flags('delete', 'do_mkdir', 'ignore_existing', 'trust_size_mtime')

def dirent_dir(direntpath, do_mkdir=False):
  dir, name = dirent_resolve(direntpath, do_mkdir=do_mkdir)
  if name is not None:
    if name in dir or not do_mkdir:
      dir = dir.chdir1(name)
    else:
      dir = dir.mkdir(name)
  return dir

def dirent_file(direntpath, do_create=False):
  E, name = dirent_resolve(direntpath)
  if name is None:
    return E
  if name in E:
    return E[name]
  if not do_create:
    raise ValueError("no such file: %s", direntpath)
  raise RuntimeError("file creation not yet implemented")

def dirent_resolve(direntpath, do_mkdir=False):
  rootD, tail = get_dirent(direntpath)
  return resolve(rootD, tail, do_mkdir=do_mkdir)

def get_dirent(direntpath):
  ''' Take `direntpath` starting with a text transcription of a Dirent and
      return the Dirent and the remaining path.
  '''
  try:
    hexpart, tail = direntpath.split('/', 1)
  except ValueError:
    hexpart = direntpath
    tail = ''
  return decode_Dirent_text(hexpart), tail

def resolve(rootD, subpath, do_mkdir=False):
  ''' Descend from the Dir `rootD` via the path `subpath`.
      Return the final Dirent, its parent, and any unresolved path components.
  '''
  if not rootD.isdir:
    raise ValueError("resolve: not a Dir: %s" % (rootD,))
  E = rootD
  parent = E.parent
  subpaths = [ s for s in subpath.split('/') if s ]
  while subpaths:
    if not E.isdir:
      raise ValueError("%s: not a Dir, remaining subpaths=%r" % (subpath, subpaths,))
    name = subpath[0]
    if name in E:
      parent = E
      E = E[name]
      subpaths.pop(0)
    elif do_mkdir:
      parent = E
      E = E.mkdir(name)
      subpaths.pop(0)
    else:
      break
  return E, parent, subpaths

def walk(rootD, topdown=True):
  ''' An analogue to os.walk to descend a vt Dir tree.
      Yields Dir, relpath, dirs, files for each directory in the tree.
      The top directory (`rootD`) has the relpath ''.
  '''
  if not topdown:
    raise ValueError("cs.venti.paths.walk: topdown must be true, got %r" % (topdown,))
  pending = [ (rootD, '') ]
  while pending:
    thisD, relpath, dirs, files = pending.pop(0)
    yield thisD, path, thisD.dirs(), thisD.files()
    for dir in dirs:
      subD = rootD.chdir1(dir)
      if relpath:
        subpath = os.path.join(relpath, dir)
      else:
        subpath = dir
      pending.push( (subD, subpath) )

def copy_in_dir(rootpath, rootD, modes=None):
  ''' Copy the os directory tree at `rootpath` over the Dir `rootD`.
      `modes` is an optional CopyModes value.
  '''
  if modes is None:
    modes = CopyModes(0)
  with Pfx("copy_in(%s)", rootpath):
    rootpath_prefix = rootpath + '/'
    for ospath, dirnames, filenames in os.walk(rootpath):
      with Pfx(ospath):
        if ospath == rootpath:
          dirD = rootD
        elif ospath.startswith(rootpath_prefix):
          dirD, name = resolve(rootD, ospath[len(rootpath_prefix):])
          dirD = dirD.chdir1(name)

        if not os.path.isdir(rootpath):
          warning("not a directory?")

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
            if filename in dirD:
              fileE = dirD[filename]
              B = fileE.getBlock()
              if modes.trust_size_mtime:
                M = fileE.meta
                st = os.stat(filepath)
                if st.st_mtime == M.mtime and st.st_size == B.span:
                  info("skipping, same mtime and size")
                  continue
                else:
                  debug("DIFFERING size/mtime: B.span=%d/M.mtime=%s VS st_size=%d/st_mtime=%s",
                    B.span, M.mtime, st.st_size, st.st_mtime)
              info("comparing with %s", B)
              matchBlocks = B.leaves
            try:
              E = copy_in_file(filepath, matchBlocks=matchBlocks)
            except OSError as e:
              error(str(e))
              continue
            except IOError as e:
              error(str(e))
              continue
            dirD[filename] = E

def copy_in_file(filepath, name=None, rsize=None, matchBlocks=None):
  ''' Store the file named `filepath`.
      Return the FileDirent.
  '''
  if name is None:
    name = os.path.basename(filepath)
  with Pfx(filepath):
    with open(filepath, "rb") as sfp:
      B = file_top_block(sfp, rsize=rsize, matchBlocks=matchBlocks)
      st = os.fstat(sfp.fileno())
      if B.span != st.st_size:
        error("MISMATCH: %s: B.span=%d, st_size=%d", filepath, B.span, st.st_size)
    E = FileDirent(name, None, B)
    E.meta.update_from_stat(st)
  return E

def copy_out(rootD, rootpath, modes=None):
  ''' Copy the Dir `rootD` onto the os directory `rootpath`.
      `modes` is an optional CopyModes value.
      Notes: `modes.delete` not implemented.
  '''
  with Pfx("copy_out(rootpath=%s)", rootpath):
    for thisD, relpath, dirs, files in walk(rootD, topdown=True):
      if relpath:
        path = os.path.join(rootpath, relpath)
      else:
        path = rootpath
      with Pfx(path):
        for filename in sorted(files):
          with Pfx(filename):
            E = thisD[filename]
            if not E.isfile:
              warning("vt source is not a file, skipping")
              continue
            filepath = os.path.join(path, filename)
            if modes.ignore_existing and os.path.exists(filepath):
              debug("already exists, ignoring")
              continue
            try:
              # TODO: should this be os.stat if we don't support symlinks?
              st = os.lstat(filepath)
            except OSError:
              pass
            else:
              B = E.getBlock()
              M = E.meta
              if ( modes.trust_size_mtime
               and ( M.mtime is not None and M.mtime == st.st_mtime
                     and B.span == st.st_size
                   )
                 ):
                debug("matching mtime and size, not overwriting")
                continue
              # create or overwrite the file
              # TODO: backup mode in case of write errors
              with Pfx(filepath):
                try:
                  with open(filepath, "wb") as fp:
                    for chunk in D[filename].getBlock().chunks():
                      fp.write(chunk)
                except OSError as e:
                  if e.errno == errno.ENOENT:
                    if modes.do_mkdir:
                      info("mkdir(%s)", path)
                      os.path.mkdir(path)
                      with open(filepath, "wb") as fp:
                        for chunk in D[filename].getBlock().chunks():
                          fp.write(chunk)
                    else:
                      error("%s: presuming missing directory, skipping other files and subdirectories here", e)
                      dirs[:] = []
                      break
                  else:
                    raise
                if M.mtime is not None:
                  os.utime(filepath, (st.st_atime, M.mtime))
