#!/usr/bin/python

''' Utility functions for random data.
'''

import os
from os.path import join as joinpath, isdir as isdirpath
from random import randint

def rand0(maxn):
  ''' Generate a pseudorandom interger from 0 to `maxn`-1.
  '''
  return randint(0, maxn - 1)

def randbool():
  ''' Return a pseudorandom Boolean value.
  '''
  return randint(0, 1) == 0

def make_randblock(size):
  ''' Generate a pseudorandom chunk of bytes of the specified size.
  '''
  return bytes(randint(0, 255) for _ in range(size))

def randomish_chunks(
    min_size, max_size=None, *, basis_block=None, total=None, limit=None
):
  ''' Generator yielding bytes-like data chunks indefinitely.

      Parameters:
      * `min_size`: the minimum chunk size
      * `max_size`: optional maximum chunk size, default: `min_size`
      * `basis_block`: optional backing block from which to compose the chunks;
        default: `make_randblock(max_size)`
      * `total`: a total count of bytes to yield;
        default: `None`, indicating no upper bound
      * `limit`: limit on the chunks returned;
        default: `None`, indicating no limit

      Each chunk has a random size from `min_size` to `max_size` inclusive
      and is composed of data chosen randomly from `basis_block`.
      As such it may contain repeating data and probably subsequent
      chunks will contain subsequences which appeared in earlier
      chunks.

      The objective of this generator is to generator fairly random data
      fairly cheaply, as it is expensive to generate fresh binary chunks
      continuously from something like:

          bytes(randint(0, 255) for _ in range(size))

      which is what happens inside `make_randblock`. Instead we
      make size a block once and assemble chunks from its contents.
  '''
  if min_size < 0:
    raise ValueError("min_size:%s < 0" % (min_size,))
  if max_size is None:
    max_size = min_size
  elif max_size < min_size:
    raise ValueError("max_size:%s < min_size:%s" % (max_size, min_size))
  if basis_block is None:
    basis_block = make_randblock(max_size)
  if total is not None and total < 0:
    raise ValueError("total:%s < 0" % (total,))
  basis_block = memoryview(basis_block)
  basis_len = len(basis_block)
  while ((total is None or total > 0) and (limit is None or limit > 0)):
    size = randint(min_size, max_size)
    if total is not None:
      size = min(size, total)
    bss = []
    while size > 0:
      bs_offset = randint(0, basis_len - 1)
      bs_len = min(basis_len - bs_offset, size)
      assert bs_len > 0
      bss.append(basis_block[bs_offset:bs_offset + bs_len])
      size -= bs_len
      if total is not None:
        total -= bs_len
      if limit is not None:
        limit -= 1
    yield b''.join(bss)

def create_random_file(filepath, filesize):
  ''' Create a file of a specified `filesize`
      filled with a random assortment of data.
  '''
  if filesize < 0:
    raise ValueError("filesize:%s < 0" % (filesize,))
  with open(filepath, 'wb') as f:
    for bs in randomish_chunks(65536, total=filesize):
      f.write(bs)

def create_random_filetree(
    top_dirpath, file_count, *, max_depth=3, max_file_size=1024000, name_len=1
):
  ''' Create a random set of files below `top_dirpath`.
      Return a list of `(filepath,filesize)` for the files created.
  '''
  files = []
  seq = 0
  for _ in range(file_count):
    parts = []
    for _ in range(max_depth):
      parts.append(str(seq % 10) * name_len)
      seq += 1
    if parts:
      dirpath = joinpath(top_dirpath, *parts[:-1])
      os.makedirs(dirpath)
      assert isdirpath(dirpath), "not a directory: %r" % (dirpath,)
    filepath = joinpath(dirpath, parts[-1])
    filesize = randint(0, max_file_size)
    create_random_file(filepath, filesize)
    files.append((filepath, filesize))
  return files
