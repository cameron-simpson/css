#!/usr/bin/python
#
# Operations on pathnames using a Venti store.
#       - Cameron Simpson <cs@zip.com.au> 07may2013
#

import os
from cs.logutils import Pfx, D, info, warning
from .dir import decode_Dirent_text

def dirent_dir(direntpath, do_mkdir=False):
  dir, name = dirent_resolve(direntpath, do_mkdir=do_mkdir)
  if name is not None:
    if name in dir or not do_mkdir:
      dir = dir.chdir1(name)
    else:
      dir = dir.mkdir(name)
  return dir

def dirent_file(direntpath, do_create=False):
  dir, name = dirent_resolve(direntpath)
  if name is None:
    raise ValueError("no filename component: %s", direntpath)
  if name in dir:
    return dir[name]
  if not do_create:
    raise ValueError("no such file: %s", direntpath)
  raise RuntimeError("file creation not yet implemented")

def dirent_resolve(direntpath, do_mkdir=False):
  rootD, tail = get_dirent(direntpath)
  return resolve(rootD, tail, domkdir=do_mkdir)

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
      Return the final Dir and the remaining component of subpath
      (or None if there was no final component).
  '''
  subpaths = [ s for s in subpath.split('/') if s ]
  while len(subpaths) > 1:
    name = subpath.pop(0)
    rootD = rootD.mkdir(name) if do_mkdir else rootD.chdir1(name)
  if subpaths:
    return rootD, subpaths[0]
  return rootD, None

def walk(rootD):
  ''' An analogue to os.walk to descend a vt Dir tree.
      Yields Dir, relpath, dirs, files for each directory in the tree.
      The top directory (`rootD`) has the relpath ''.
  '''
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

def copy_in(rootpath, rootD, delete=False, ignore_existing=False, trust_size_mtime=False):
  ''' Copy the os directory tree at `rootpath` over the Dir `rootD`.
  '''
  with Pfx("update_dir(%s)", rootpath):
    rootpath_prefix = rootpath + '/'
    for ospath, dirnames, filenames in os.walk(rootpath):
      info(ospath)
      with Pfx(ospath):
        if ospath == rootpath:
          D = rootD
        elif ospath.startswith(rootpath_prefix):
          D, name = resolve(rootD, ospath[len(rootpath_prefix):])
          D = D.chdir1(name)

        if not os.path.isdir(rootpath):
          warning("not a directory?")

        if delete:
          # Remove entries in D not present in the real filesystem
          allnames = set(dirnames)
          allnames.update(filenames)
          Dnames = sorted(D.keys())
          for name in Dnames:
            if name not in allnames:
              info("delete %s", name)
              del D[name]

        for dirname in sorted(dirnames):
          if dirname not in D:
            D.mkdir(dirname)
          else:
            E = D[dirname]
            if not E.isdir:
              # old name is not a dir - toss it and make a dir
              del D[dirname]
              E = D.mkdir(dirname)

        for filename in sorted(filenames):
          filepath = os.path.join(ospath, filename)
          if not os.path.isfile(filepath):
            warning("not a regular file, skipping %s", filepath)
            continue

            try:
              E = D.storeFilename(filepath, filename,
                              trust_size_mtime=trust_size_mtime,
                              ignore_existing=ignore_existing)
            except OSError as e:
              error("%s: %s", filepath, e)
            except IOError as e:
              error("%s: %s", filepath, e)

def copy_out(rootD, rootpath, makedirs=False, delete=False, ignore_existing=False, trust_size_mtime=False):
  ''' Copy the Dir `rootD` onto the os directory `rootpath`.
      Notes: `delete` not implemented.
  '''
  with Pfx("restore(rootpath=%s)", rootpath):
    if not os.path.isdir(rootpath):
      if makedirs:
        os.makedirs(rootpath)
    for thisD, relpath, dirs, files in walk(rootD):
      if relpath:
        path = os.path.join(rootpath, relpath)
      else:
        path = rootpath
      with Pfx(path):
        if not os.path.isdir(path):
          info("mkdir")
          os.path.mkdir(path)
        for filename in sorted(files):
          with Pfx(filename):
            E = thisD[filename]
            if not E.isfile:
              warning("vt source is not a file, skipping")
              continue
            filepath = os.path.join(path, filename)
            if ignore_existing and os.path.exists(filepath):
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
              if ( trust_size_mtime
               and ( M.mtime is not None and M.mtime == st.st_mtime
                     and B.span == st.st_size
                   )
                 ):
                debug("matching mtime and size, not overwriting")
                continue
              # create or overwrite the file
              # TODO: backup mode in case of write errors
              with Pfx(filepath):
                with open(filepath, "wb") as fp:
                  for chunk in D[filename].getBlock().chunks():
                    fp.write(chunk)
                if M.mtime is not None:
                  os.utime(filepath, (st.st_atime, M.mtime))
