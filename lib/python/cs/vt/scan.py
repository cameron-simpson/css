#!/usr/bin/env python3
#

''' The byte scanning function scanbuf.
    In C by choice, in Python if not.

    Import the C buffer scanner, building it if necessary.
    Fall back to the pure python one if we fail.
'''

from distutils.core import setup, Extension
from os import chdir, getcwd
from os.path import dirname, join as joinpath
import sys
##from time import sleep
from cs.context import stackattrs
from cs.logutils import error, warning

# default constraints on the chunk sizes yielded from scans
MIN_BLOCKSIZE = 80  # less than this seems silly
MAX_BLOCKSIZE = 16383  # fits in 2 octets BS-encoded

def py_scanbuf(hash_value, chunk):
  ''' Pure Python scanbuf if there's no C version.
      (Implementation 1, no block size constraints.)
  '''
  offsets = []
  for offset, b in enumerate(chunk):
    hash_value = (
        ((hash_value & 0x001fffff) << 7)
        | ((b & 0x7f) ^ ((b & 0x80) >> 7))
    )
    if hash_value % 4093 == 4091:
      offsets.append(offset)
  return hash_value, offsets

def py_scanbuf2(chunk, hash_value, sofar, min_block, max_block):
  ''' Pure Python scanbuf for use if there's no C version.
      (Implementation 2, honours block size constraints.)
      Return `(hash_value2,offsets)`
      being the updates rolling hash value at the end of `chunk`
      and a list of block edge offsets.

      Parameters:
      * `chunk`: a `bytes`like buffer of data
      * `hash_value`: initial value of the rolling hash
      * `sofar`: the length of the initial partially scanner block prior to `chunk`
      * `min_block`: the smallest block size allowed
      * `max_block`: the largest block size allowed
  '''
  offsets = []
  block_size = sofar
  for offset, b in enumerate(chunk):
    hash_value = (
        ((hash_value & 0x001fffff) << 7)
        | ((b & 0x7f) ^ ((b & 0x80) >> 7))
    )
    if block_size >= min_block:
      if block_size >= max_block or hash_value % 4093 == 4091:
        offsets.append(offset)
        block_size = 0
    block_size += 1
  return hash_value, offsets

# endeavour to obtain the C implementations of canbuf and scanbuf2
# but fall back to the pure Python implementations
try:
  from ._scan import scanbuf
  from ._scan import scanbuf2
except ImportError as e:
  warning("%s: building _scan from _scan.c", e)

  def do_setup():
    ''' Run distutils.core.setup from the top of the lib tree.
        Side effect: changes directory, needs undoing.
    '''
    pkgdir = dirname(__file__)
    chdir(dirname(dirname(pkgdir)))
    return setup(
        ext_modules=[Extension("cs.vt._scan", [joinpath(pkgdir, '_scan.c')])],
    )

  ### delay, seemingly needed to make the C version look "new"
  ##sleep(2)
  owd = getcwd()
  with stackattrs(sys, argv=[sys.argv[0], 'build_ext', '--inplace']):
    try:
      do_setup()
    except SystemExit as e:
      chdir(owd)
      error("SETUP FAILS: %s:%s", type(e), e)
    else:
      chdir(owd)
      try:
        from ._scan import scanbuf
        from ._scan import scanbuf2
      except ImportError as e:
        error("import fails after setup: %s", e)
        scanbuf = py_scanbuf
        scanbuf2 = py_scanbuf2

def scan(
    chunks,
    *,
    sofar=0,
    hash_value=0,
    min_block=None,
    max_block=None,
    scan_buffer=None
):
  ''' Scan `chunks` with the basic hash based scanner, yield offsets.
  '''
  if min_block is None:
    min_block = MIN_BLOCKSIZE
  elif min_block < 8:
    raise ValueError("rejecting min_block < 8: %s" % (min_block,))
  if max_block is None:
    max_block = MAX_BLOCKSIZE
  elif max_block > 1024 * 1024:
    raise ValueError("rejecting max_block > 1024*1024: %s" % (max_block,))
  if min_block >= max_block:
    raise ValueError(
        "rejecting min_block:%d >= max_block:%d" % (min_block, max_block)
    )
  if scan_buffer is None:
    scan_buffer = scanbuf2
  chunk_base = 0
  for chunk in chunks:
    hash_value, scan_offsets = scan_buffer(
        chunk, hash_value, sofar, min_block, max_block
    )
    for offset in scan_offsets:
      yield chunk_base + offset
    # Update the length of the unpartitioned data at the end of chunk.
    # Measure from the last offset if there were offset
    # otherwise add the chunk length because it was not subdivided.
    sofar = (len(chunk) - offset if scan_offsets else sofar + len(chunk))
    chunk_base += len(chunk)
