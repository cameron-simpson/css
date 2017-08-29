#!/usr/bin/python
#
# Import/export operations.
#   - Cameron Simpson <cs@cskk.id.au> 26aug2017
#

import os
from os.path import basename, relpath, join as joinpath
import time
from cs.fileutils import read_from
from cs.logutils import info, warning, error
from cs.pfx import Pfx, XP
from cs.units import transcribe, TIME_SCALE, BINARY_BYTES_SCALE, DECIMAL_SCALE
from .blockify import blocks_of, top_block_for
from .dir import Dir, FileDirent
from .parsers import scanner_from_filename

def import_dir(srcpath, D, overlay=False, whole_read=False):
  ''' Import a directory tree, return a Dir and list of errors.
      `overlay`: replace existing entries, default False
      `whole_read`: read file contents even if size and mtime match
  '''
  with Pfx("import_dir(%r,overlay=%s,whole_read=%s)...", srcpath, overlay, whole_read):
    base = basename(srcpath)
    errors = []
    try:
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
                E = subD[filename]
                if overlay:
                  if not whole_read:
                    Estat0 = E.stat()
                    XP("Estat0=%s", Estat0)
                    Estat = E.meta.stat()
                    XP("Estat=%s", Estat)
                    try:
                      S = os.stat(filepath)
                    except OSError as e:
                      error("stat: %s", e)
                      errors.append( ('stat', filepath, joinpath(rpath, filename)) )
                      ok = False
                      continue
                    XP("S=%s", S)
                    if Estat.st_size == S.st_size and Estat.st_mtime == S.st_mtime:
                      info("same size and mtime, considering unchanged")
                      continue
                    X("%r: differing size/mtime", filepath)
                else:
                  error("already exists")
                  errors.append( ('conflict', filepath, joinpath(rpath, filename)) )
                  ok = False
                  continue
              F = import_file(filepath)
              subD[filename] = F
    except KeyboardInterrupt as e:
      error("keyboard interrupt: %s, returning partial import", e)
      errors.append( ('interrupt',) )
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
      S = os.fstat(fp.fileno())
    end = time.time()
    len_text = transcribe(len(B), BINARY_BYTES_SCALE, max=1, sep=' ')
    if end > start:
      elapsed = end - start
      elapsed_text = transcribe(elapsed, TIME_SCALE)
      KBps = int(len(B) / elapsed / 1024)
      print("%r: %s in %ss at %dKiBps" % (srcpath, len_text, elapsed, KBps))
    else:
      print("%r: %s in 0s" % (srcpath, len(B)))
    F = FileDirent(basename(srcpath), block=B)
    F.meta.mtime = S.st_mtime
    return F
