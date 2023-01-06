#!/usr/bin/env python3
#
# Tar utilities. - Cameron Simpson <cs@cskk.id.au>
#

''' Assorted tar related things.
'''

from os import mkdir, lstat
from os.path import (
    exists as existspath,
    join as joinpath,
    isabs as isabspath,
    isdir as isdirpath,
)
from stat import S_ISREG
from subprocess import Popen, DEVNULL, PIPE
from threading import Thread
from typing import Iterable, List

from cs.deco import fmtdoc
from cs.fs import shortpath
from cs.logutils import warning
from cs.pfx import pfx_call
from cs.progress import progressbar
from cs.queues import IterableQueue
from cs.units import BINARY_BYTES_SCALE
from cs.upd import uses_upd  # pylint: disable=redefined-builtin

TAR_EXE = 'tar'
DEFAULT_BCOUNT = 2048

# pylint: disable=too-many-branches
def _watch_filenames(filenames: Iterable[str], chdirpath: str):
  ''' Consumer of `filenames`, an iterable of filenames,
      yielding `(filename,diff)` being a 2-tuple of
      filename and incremental bytes consumed at this point.

      This code assumes that we one see a filename once but that
      it may be before the file is written or after the file is
      written, and as such stats the filename twice: on sight and
      on sight of the next filename.

      The yielded results therefore mention each filename twice.
  '''
  # consume filenames_q
  ofilename = None
  osize = None
  for filename in filenames:
    if not isabspath(filename) and chdirpath != '.':
      filename = joinpath(chdirpath, filename)
    if ofilename is not None:
      # check final size of previous file
      try:
        S = lstat(ofilename)
      except OSError as e:
        # the previous went away!
        warning("lstat(%r): %s", ofilename, e)
        diff = 0
      else:
        diff = S.st_size - osize
        if diff < 0:
          warning("%r: file shrank! %d => %d", ofilename, osize, S.st_size)
      yield ofilename, diff
      ofilename = None
    try:
      S = lstat(filename)
    except FileNotFoundError:
      continue
    except OSError as e:
      # pretend the size is 0
      warning("%r: stat: %s", filename, e)
      continue
    if not S_ISREG(S.st_mode):
      continue
    size = S.st_size
    yield filename, size
    ofilename = filename
    osize = size
  if ofilename is not None:
    # check final size of final file
    try:
      S = lstat(ofilename)
    except FileNotFoundError:
      diff = 0
    except OSError as e:
      # the previous went away!
      warning("lstat(%r): %s", ofilename, e)
      diff = 0
    else:
      if not S_ISREG(S.st_mode):
        warning("%r: no longer a file? S=%s", ofilename, S)
      else:
        diff = S.st_size - osize
        if diff < 0:
          warning("%r: file shrank! %d => %d", ofilename, osize, S.st_size)
        yield ofilename, diff

def _read_tar_stdout_filenames(f, filenames_q):
  for line in f:
    filename = line.rstrip('\n')
    filenames_q.put(filename)
  filenames_q.close()

def _read_tar_stderr(f, filenames_q):
  for errline in f:
    errline = errline.rstrip('\n')
    if errline.startswith("x "):
      filenames_q.put(errline[2:])
    else:
      warning("%s: err: " + errline)
  filenames_q.close()

@uses_upd
def traced_untar(
    tarfd,
    *,
    chdirpath='.',
    label=None,
    tar_exe=TAR_EXE,
    bcount=DEFAULT_BCOUNT,
    upd
):
  ''' Read tar data from `tarfd` and extract.
      Return the `tar` exit code.

      Parameters:
      * `tarfd`: the source tar data,
        suitable for `subprocess.Popen`'s `stdin` parameter
      * `chdirpath`: optional directory to which to `chdir` before accessing `srcpaths`
      * `label`: optional label for the progress bar
      * `tar_exe`: optional `tar` executable, default from `TAR_EXE`: `{TAR_EXE}`
      * `bcount`: blocking factor in 152 byte unites,
        default from `DEFAULT_BCOUNT`: `{DEFAULT_BCOUNT}`
  '''
  if label is None:
    label = f'untar {tarfd} -> {chdirpath}'
  # pylint: disable=consider-using-with
  P = Popen(
      [tar_exe, '-x', '-v', '-C', chdirpath, '-b',
       str(bcount), '-f', '-'],
      stdin=tarfd,
      stdout=PIPE,
      stderr=PIPE,
      text=True,
  )
  ##with open(P.stdout, 'r', buffering=1) as fout:
  ##  with open(P.stderr, 'r', buffering=1) as ferr:
  with upd.insert(0) as filename_proxy:
    filenames_q = IterableQueue()
    filenames_q.open()  # secondary open for the stderr processor
    # consumer of tar stdout
    # expects bare filenames
    Thread(
        target=_read_tar_stdout_filenames, args=(P.stdout, filenames_q)
    ).start()
    # consumer of tar stderr, recognising "x filename" lines
    # copies "x filename" filename to filenames_q,
    # issues warnings for other messages
    Thread(target=_read_tar_stderr, args=(P.stderr, filenames_q)).start()
    # consume filenames->(filename,diff) generator
    filename = None
    diff = 0
    for filename, diff in progressbar(
        _watch_filenames(filenames_q, chdirpath),
        label=label,
        itemlenfunc=lambda f_d: diff,
        upd=upd,
        report_print=True,
        units_scale=BINARY_BYTES_SCALE,
    ):
      filename_proxy.text = filename
  return P.wait()

@fmtdoc
def tar(
    *srcpaths: List[str],
    chdirpath='.',
    output,
    tar_exe=TAR_EXE,
    bcount=DEFAULT_BCOUNT
):
  ''' Tar up the contents of `srcpaths` to `output`.

      Parameters:
      * `srcpaths`: source filesystem paths
      * `chdirpath`: optional directory to which to `chdir` before accessing `srcpaths`
      * `tar_exe`: optional `tar` executable, default from `TAR_EXE`: `{TAR_EXE}`
      * `bcount`: blocking factor in 152 byte unites,
        default from `DEFAULT_BCOUNT`: `{DEFAULT_BCOUNT}`
  '''
  if not srcpaths:
    raise ValueError("empty srcpaths")
  if isinstance(output, str):
    if existspath(output):
      raise ValueError(f'path already exists: {output!r}')
  return Popen(
      [
          tar_exe, '-c', '-C', chdirpath, '-b',
          str(bcount), '-f',
          (output if isinstance(output, str) else '-'), '--', *srcpaths
      ],
      stdin=DEVNULL,
      stdout=(None if isinstance(output, str) else output),
  )

@uses_upd
def traced_cpdir(
    srcdirpath,
    dstdirpath,
    *,
    label=None,
    tar_exe=TAR_EXE,
    bcount=DEFAULT_BCOUNT,
    upd
):
  ''' Copy a directory to a new place using piped tars with progress reporting.
      Return `0` if both tars success, nonzero otherwise.

      Parameters:
      * `srcdirpath`: the source directory filesystem path
      * `dstdirpath`: the destination directory filesystem path,
        which must not already exist
      * `label`: optional label for the progress bar
      * `tar_exe`: optional `tar` executable, default from `TAR_EXE`: `{TAR_EXE}`
      * `bcount`: blocking factor in 152 byte unites,
        default from `DEFAULT_BCOUNT`: `{DEFAULT_BCOUNT}`
  '''
  if label is None:
    label = f'cpdir {shortpath(srcdirpath)} {shortpath(dstdirpath)}'
  if not isdirpath(srcdirpath):
    raise ValueError(f'not a directory: {srcdirpath!r}')
  pfx_call(mkdir, dstdirpath)
  tarP = tar(
      '.', chdirpath=srcdirpath, output=PIPE, tar_exe=tar_exe, bcount=bcount
  )
  untar_returncode = traced_untar(
      tarP.stdout,
      chdirpath=dstdirpath,
      label=label,
      tar_exe=tar_exe,
      bcount=bcount,
      upd=upd,
  )
  tar_returncode = tarP.wait()
  return tar_returncode or untar_returncode
