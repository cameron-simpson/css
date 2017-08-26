#!/usr/bin/python
#
# Import/export operations.
#   - Cameron Simpson <cs@cskk.id.au> 26aug2017
#

import os
from os.path import basename, relpath, join as joinpath
import time
from cs.fileutils import read_from
from cs.logutils import info
from cs.pfx import Pfx, XP
from .blockify import blocks_of, top_block_for
from .dir import Dir, FileDirent
from .parsers import scanner_from_filename

def import_dir(srcpath, do_merge=False):
  ''' Import a directory tree, return a Dir and list of errors.
  '''
  with Pfx("import_dir(%r,do_merge=%s)...", srcpath, do_merge):
    base = basename(srcpath)
    D = Dir('.').mkdir(base)
    errors = []
    for dirpath, dirnames, filenames in os.walk(srcpath):
      rpath = relpath(dirpath, srcpath)
      with Pfx(repr(rpath)):
        # arrange ordered descent
        dirnames[:] = sorted(dirnames)
        subD = D.makedirs(rpath)
        for filename in sorted(filenames):
          with Pfx("file %r", filename):
            filepath = joinpath(dirpath, filename)
            if filename in subD:
              if do_merge:
                error("already exists, merge not yet implemented")
                errors.append( ('conflict', filepath, joinpath(rpath, filename)) )
                ok = False
                continue
            F = import_file(filepath)
            subD[filename] = F
  return D, errors

def import_file(srcpath):
  ''' Read an OS file into the Store, return a FileDirent.
  '''
  with Pfx("import_file(%r)", srcpath):
    start = time.time()
    with open(srcpath, 'rb') as fp:
      scanner = scanner_from_filename(srcpath)
      blocks = blocks_of(read_from(fp), scanner)
      B = top_block_for(blocks)
    end = time.time()
    if end > start:
      elapsed = end - start
      print("%r: %d bytes in %ss at %s B/s" % (srcpath, len(B), elapsed, len(B) / elapsed))
    else:
      print("%r: %d bytes in 0s" % (srcpath, len(B)))
    return FileDirent(basename(srcpath), block=B)
