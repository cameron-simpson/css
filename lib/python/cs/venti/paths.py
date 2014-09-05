#!/usr/bin/python
#
# Operations on pathnames using a Venti store.
#       - Cameron Simpson <cs@zip.com.au> 07may2013
#

import os
from cs.logutils import Pfx, D, info, warning, error
from .file import file_top_block
from .dir import decode_Dirent_text, FileDirent

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

def path_split(path):
  ''' Split path into components, discarding the empty string and ".".
      The returned subparts are useful for path traversal.
  '''
  return [ subpath for subpath in path.split('/') if subpath != '' and subpath != '.' ]

def resolve(rootD, subpath, do_mkdir=False):
  ''' Descend from the Dir `rootD` via the path `subpath`.
      `subpath` may be a str or an array of str.
      Return the final Dirent, its parent, and any unresolved path components.
  '''
  if not rootD.isdir:
    raise ValueError("resolve: not a Dir: %s" % (rootD,))
  E = rootD
  parent = E.parent
  if isinstance(subpath, str):
    subpaths = path_split(subpath)
  else:
    subpaths = subpath
  while subpaths and E.isdir:
    name = subpaths[0]
    if name == '' or name == '.':
      # stay on this Dir
      pass
    elif name == '..':
      # go up a level if available
      if E.parent is None:
        break
      E = E.parent
    elif name in E:
      parent = E
      E = E[name]
    elif do_mkdir:
      parent = E
      E = E.mkdir(name)
    else:
      break
    subpaths.pop(0)
  return E, parent, subpaths

def walk(rootD, topdown=True):
  ''' An analogue to os.walk to descend a vt Dir tree.
      Yields Dir, relpath, dirnames, filenames for each directory in the tree.
      The top directory (`rootD`) has the relpath ''.
  '''
  if not topdown:
    raise ValueError("topdown must be true, got %r" % (topdown,))
  # queue of (Dir, relpath)
  pending = [ (rootD, '') ]
  while pending:
    thisD, relpath = pending.pop(0)
    dirnames = thisD.dirs()
    filenames = thisD.files()
    yield thisD, relpath, dirnames, filenames
    for dirname in reversed(dirnames):
      subD = rootD.chdir1(dirname)
      if relpath:
        subpath = os.path.join(relpath, dirname)
      else:
        subpath = dirname
      pending.append( (subD, subpath) )

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
