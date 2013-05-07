#!/usr/bin/python
#
# Operations on pathnames using a Venti store.
#       - Cameron Simpson <cs@zip.com.au> 07may2013
#

import os
from cs.logutils import D, info

def resolve(rootD, subpath, do_mkdir=False):
  ''' Descend from the Dir `rootD` via the path `subpath`.
      Return the final Dir and the remaining component of subpath
      (or None if there was no final component).
  '''
  subpaths = [ s for s in subpath.split('/') if s ]
  while len(subpaths) > 1:
    name = subpath.pop(0)
    D = D.mkdir(name) if do_mkdir else D.chdir1(name)
  if subpaths:
    return D, subpaths[0]
  return D, None

def update_dir(rootD, rootpath, delete=False, ignore_existing=False, trust_size_mtime=False):
  ''' Update a Dir `rootD` with the contents of the specified
      directory `rootpath` from the OS.
  '''
  with Pfx("update_dir(%s)", rootpath):
    rootpath_prefix = rootpath + '/'
    for ospath, dirs, files in os.walk(rootpath):
      info(ospath)
      with Pfx(ospath):
        if ospath == rootpath:
          D = rootD
        elif ospath.startswith(rootpath_prefix):
          D, name = resolve(rootD, ospath[len(rootpath_prefix):])
          D = D.chdir1(name)

        if not os.path.isdir(rootdir):
          warning("not a directory?")

        if not delete:
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
