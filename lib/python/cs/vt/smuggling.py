#!/usr/bin/python
#
# Import/export operations.
#   - Cameron Simpson <cs@cskk.id.au> 26aug2017
#

import os
from os.path import basename, relpath, join as joinpath
import time
from cs.fileutils import read_from
from cs.logutils import debug, info, warning, error, loginfo
from cs.pfx import Pfx, XP
from cs.units import transcribe, TIME_SCALE, BINARY_BYTES_SCALE, DECIMAL_SCALE
from .blockify import blockify, top_block_for
from .dir import Dir, FileDirent
from .parsers import scanner_from_filename

def import_dir(srcpath, D, delete=False, overlay=False, whole_read=False):
  ''' Import a directory tree, return a Dir and list of errors.
      `delete`: delete entries for things not in `srcpath`
      `overlay`: replace existing entries, default False
      `whole_read`: read file contents even if size and mtime match
  '''
  global loginfo
  U = loginfo.upd
  if U:
    out = U.out
    nl = U.nl
  else:
    out = lambda txt, *a: None
    nl = info
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
            filepath = joinpath(dirpath, filename)
            rfilepath = joinpath(rpath, filename)
            out(rfilepath)
            with Pfx(filepath):
              if filename in subD:
                E = subD[filename]
                if overlay:
                  if not whole_read:
                    Estat = E.meta.stat()
                    try:
                      S = os.stat(filepath)
                    except OSError as e:
                      error("stat: %s", e)
                      errors.append( ('stat', filepath, joinpath(rpath, filename)) )
                      ok = False
                      continue
                    if Estat.st_size == S.st_size and Estat.st_mtime == S.st_mtime:
                      debug("same size and mtime, considering unchanged")
                      continue
                else:
                  error("already exists")
                  errors.append( ('conflict', filepath, joinpath(rpath, filename)) )
                  ok = False
                  continue
                nl("%s: update", rfilepath)
              else:
                nl("%s: new", rfilepath)
              F = import_file(filepath)
              subD[filename] = F
          if delete:
            existing = list(subD.keys())
            for name in existing:
              with Pfx(repr(name)):
                if name not in dirnames and name not in filenames:
                  nl("%s: delete", joinpath(rpath, name))
                  del subD[name]
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
      blocks = blockify(read_from(fp), scanner)
      B = top_block_for(blocks)
      S = os.fstat(fp.fileno())
    end = time.time()
    len_text = transcribe(len(B), BINARY_BYTES_SCALE, max=1, sep=' ')
    if end > start:
      elapsed = end - start
      elapsed_text = transcribe(elapsed, TIME_SCALE)
      KBps = int(len(B) / elapsed / 1024)
      info("%r: %s in %ss at %dKiBps" % (srcpath, len_text, elapsed, KBps))
    else:
      info("%r: %s in 0s" % (srcpath, len(B)))
    F = FileDirent(basename(srcpath), block=B)
    F.meta.mtime = S.st_mtime
    return F
