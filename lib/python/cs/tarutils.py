#!/usr/bin/env python3
#
# Tar utilities. - Cameron Simpson <cs@cskk.id.au>
#

''' Assorted tar related things, including a fast tar-based copy.

    My most heavily used use for this is my `cpdir` script which
    does a high performance directory copy by piping 2 `tar`s
    together.
    It runs this:

        from cs.tarutils import traced_cpdir
        sys.exit(traced_cpdir(*sys.argv[1:]))

'''

import os
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
from time import sleep
from typing import List

from cs.deco import fmtdoc
from cs.fs import shortpath
from cs.gimmicks import warning
from cs.pfx import pfx_call
from cs.progress import progressbar
from cs.queues import IterableQueue, QueueIterator
from cs.units import BINARY_BYTES_SCALE
from cs.upd import Upd, uses_upd  # pylint: disable=redefined-builtin

__version__ = '20240318'

DISTINFO = {
    'keywords': ["python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.deco',
        'cs.fs',
        'cs.gimmicks',
        'cs.pfx',
        'cs.progress',
        'cs.queues',
        'cs.units',
        'cs.upd',
    ],
}

TAR_EXE = 'tar'
DEFAULT_BCOUNT = 2048

@uses_upd
def _warning(msg, *a, upd: Upd):
  with upd.above():
    warning(msg, *a)

def _stat_diff(fspath: str, old_size: int):
  ''' `lstat(fspath)` and return the difference between its size and `old_size`.
      `lstat` failure warns and reports a difference of `0`.
  '''
  try:
    S = lstat(fspath)
  except FileNotFoundError:
    diff = 0
  except OSError as e:
    _warning("lstat(%r): %s", fspath, e)
    diff = 0
  else:
    if S_ISREG(S.st_mode):
      diff = S.st_size - old_size
      if diff < 0:
        _warning("%r: file shrank! %d => %d", fspath, old_size, S.st_size)
    else:
      # not a regular file - ignore the size
      diff = 0
  return diff

# pylint: disable=too-many-branches
def _watch_filenames(
    filenames_qit: QueueIterator, chdirpath: str, *, poll_interval=0.3
):
  ''' Consumer of `filenames_qit`, a `QueueIterator` as obtained from `IterableQueue`,
      yielding `(filename,diff)` being a 2-tuple of
      filename and incremental bytes consumed at this point.

      This code assumes that we see a filename once but that
      it may be before the file is written or after the file is
      written, and as such stats the filename twice: on sight and
      on sight of the next filename.

      The yielded results therefore mention each filename twice.
  '''
  current_filename = None
  current_size = None
  while True:
    if filenames_qit.empty():
      # poll the current file and wait for another name
      if current_filename is not None:
        diff = _stat_diff(current_filename, current_size)
        yield current_filename, diff
        current_size += diff
      if filenames_qit.closed:
        break
      sleep(poll_interval)
      continue
    try:
      new_filename = next(filenames_qit)
    except StopIteration:
      break
    if not isabspath(new_filename) and chdirpath != '.':
      new_filename = joinpath(chdirpath, new_filename)
    if current_filename != new_filename:
      # new file: poll the old file and reset the size to 0 for the new file
      if current_filename is not None:
        diff = _stat_diff(current_filename, current_size)
        yield current_filename, diff
      current_filename = new_filename
      current_size = 0
    diff = _stat_diff(current_filename, current_size)
    current_size += diff
    yield current_filename, diff
  # final poll
  if current_filename is not None:
    diff = _stat_diff(current_filename, current_size)
    yield current_filename, diff

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
      _warning("%s: err: " + errline)
  filenames_q.close()

# pylint: disable=too-many-locals
@uses_upd
@fmtdoc
def traced_untar(
    tarfd,
    *,
    chdirpath='.',
    label=None,
    tar_exe=TAR_EXE,
    bcount=DEFAULT_BCOUNT,
    total=None,
    _stat_fd=False,
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
      * `bcount`: blocking factor in 512 byte unites,
        default from `DEFAULT_BCOUNT`: `{DEFAULT_BCOUNT}`
  '''
  if isinstance(tarfd, str):
    with pfx_call(open, tarfd, 'rb') as tarf:
      return traced_untar(
          tarf,
          chdirpath=chdirpath,
          label=f'untar {shortpath(tarfd)} -> {chdirpath}',
          tar_exe=tar_exe,
          bcount=bcount,
          _stat_fd=tarfd.endswith('.tar'),
          upd=upd,
      )
  if label is None:
    label = f'untar {tarfd} -> {chdirpath}'
  if total is None and _stat_fd:
    # stat the file to get its size
    if isinstance(tarfd, int):
      fd = tarfd
    else:
      try:
        fd = tarfd.fileno()
      except AttributeError:
        # no .fileno()
        fd = -1
    if fd >= 0:
      try:
        S = os.fstat(fd)
      except OSError as e:
        _warning("os.fstat(%r): %s", tarfd, e)
      else:
        if S_ISREG(S.st_mode):
          total = S.st_size
  # pylint: disable=consider-using-with
  P = Popen(
      [
          tar_exe,
          '-x',
          '-v',
          '-C',
          chdirpath,
          '-b',
          str(bcount),
          '-f',
          '-',
      ],
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
    for filename, _ in progressbar(
        _watch_filenames(filenames_q, chdirpath),
        label=label,
        itemlenfunc=lambda f_d: f_d[1],
        total=total,
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
      Return the `Popen` object for the `tar` command.

      Parameters:
      * `srcpaths`: source filesystem paths
      * `chdirpath`: optional directory to which to `chdir` before accessing `srcpaths`
      * `tar_exe`: optional `tar` executable, default from `TAR_EXE`: `{TAR_EXE}`
      * `bcount`: blocking factor in 512 byte unites,
        default from `DEFAULT_BCOUNT`: `{DEFAULT_BCOUNT}`
  '''
  if not srcpaths:
    raise ValueError("empty srcpaths")
  if isinstance(output, str):
    if existspath(output):
      raise ValueError(f'path already exists: {output!r}')
  return Popen(
      [
          tar_exe,
          '-c',
          '-C',
          chdirpath,
          '-b',
          str(bcount),
          '-f',
          (output if isinstance(output, str) else '-'),
          '--',
          *srcpaths,
      ],
      stdin=DEVNULL,
      stdout=(None if isinstance(output, str) else output),
  )

@uses_upd
@fmtdoc
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
      Return `0` if both tars succeed, nonzero otherwise.

      Parameters:
      * `srcdirpath`: the source directory filesystem path
      * `dstdirpath`: the destination directory filesystem path,
        which must not already exist
      * `label`: optional label for the progress bar
      * `tar_exe`: optional `tar` executable, default from `TAR_EXE`: `{TAR_EXE}`
      * `bcount`: blocking factor in 512 byte unites,
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
