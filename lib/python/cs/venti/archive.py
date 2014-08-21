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
import sys
import time
from datetime import datetime
import errno
from itertools import takewhile
from cs.inttypes import Flags
from cs.lex import unctrl
from cs.logutils import D, Pfx, error, X
from . import totext, fromtext
from .blockify import blockify
from .dir import decode_Dirent_text, Dir
from .file import filedata

CopyModes = Flags('delete', 'do_mkdir', 'ignore_existing', 'trust_size_mtime')

def retrieve(arfile, paths=None):
  ''' Retrieve Dirents for the named file paths, or None if a
      path does not resolve.
      If `paths` if missing or None, retrieve the latest Dirents
      for all paths named in the archive file.
  '''
  with Pfx(arfile):
    found = {}
    if arfile == '-':
      arfp = sys.stdin
      assert not arfp.isatty(), "stdin may not be a tty"
    else:
      arfp = open(arfile)
    for unixtime, E in read_Dirents(arfp):
      if paths is None or E.name in paths:
        found[E.name] = E
    if arfile != '-':
      arfp.close()
    if paths is None:
      paths = found.keys()
    return [ (path, found.get(path)) for path in paths ]

def toc_report(fp, path, E, verbose):
  if verbose:
    print(path, file=fp)
  else:
    print(E.meta, path, file=fp)
  if E.isdir:
    for subpath in sorted(E.keys()):
      toc_report(fp, os.path.join(path, subpath), E[subpath], verbose)

def toc_archive(arfile, paths=None, verbose=False, fp=None):
  if fp is None:
    fp = sys.stdout
  for path, E in retrieve(arfile, paths):
    if E is None:
      error("no entry for %s", path)
    else:
      toc_report(fp, path, E, verbose)

def update_archive(arpath, ospath, modes, create_archive=False):
  ''' Update the archive file `arpath` from `ospath`.
     `ospath` is taken to match with the top of the archive.
  '''
  last_entry = None
  with Pfx("update %r", arpath):
    try:
      with open(arfile, "r") as arfp:
        try:
          last_entry = last(read_Dirents(arfp))
        except IndexError:
          last_entry = None
    except OSError as e:
      if e.errno != errno.ENOENT:
        raise
      if not create_archive:
        raise ValueError("missing archive (%s), not creating" % (e,))
    base = os.path.basename(ospath)
    if os.path.isdir(ospath):
      if last_entry is None:
        E = Dir(base)
      else:
        when, E = last_entry
        if not E.isdir:
          E = Dir(base)
      copy_in_dir(E, ospath, modes)
    elif os.path.isfile(ospath):
      if last_entry is None:
        E = FileDirent(base)
      else:
        when, E = last_entry
        if not E.isfile:
          E = FileDirent(base)
      copy_in_file(E, ospath, modes)
    else:
      raise ValueError("unsupported ospath (%r), not directory or file" % (ospath,))
    save_Dirent(arpath, E)

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
    rootpath_prefix = rootpath + '/'
    # TODO: try out scandir sometime
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
            if filename not in dirD:
              fileE = FileDirent(filename)
              dirD[filename] = fileE
            else:
              fileE = dirD[filename]
              if modes.trust_size_mtime:
                M = fileE.meta
                st = os.stat(filepath)
                if st.st_mtime == M.mtime and st.st_size == B.span:
                  info("skipping, same mtime and size")
                  continue
                else:
                  debug("DIFFERING size/mtime: B.span=%d/M.mtime=%s VS st_size=%d/st_mtime=%s",
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
  X("copy_in_file(%r)", filepath)
  with Pfx(filepath):
    with open(filepath, "rb") as sfp:
      B = top_block_for(_blockify_file(sfp, E))
      st = os.fstat(sfp.fileno())
      if B.span != st.st_size:
        warning("MISMATCH: B.span=%d, st_size=%d", B.span, st.st_size)
  E = FileDirent(name, None, B)
  E.meta.update_from_stat(st)

def _blockify_file(fp, E):
  ''' Read data from the file `fp` and compare with the FileDirect `E`, yielding leaf Blocks for a new file.
      This supports update_file().
  '''
  # read file data in chunks matching the existing leaves
  # return the leaves while the data match
  for B in E.getBlock().leaves:
    data = sfp.read(len(B))
    if len(data) == 0:
      # EOF
      return
    if len(data) != len(B):
      break
    if not B.matches_data(data):
      break
    yield B
  # blockify the remaining file data
  for B in blockify(chain( [data], filedata(fp) )):
    yield B
