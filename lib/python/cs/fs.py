#!/usr/bin/env python3

''' Assorted filesystem related utility functions,
    some of which have been bloating cs.fileutils for too long.
'''

import os
from os.path import join as joinpath, relpath

from cs.x import X

def rpaths(
    dirpath='.', *, only_suffixes=None, skip_suffixes=None, sort_paths=False
):
  ''' Yield relative file paths from a directory.

      Parameters:
      * `dirpath`: optional top directory, default `'.'`
      * `only_suffixes`: optional iterable of suffixes of interest;
        if provided only files ending in these suffixes will be yielded
      * `skip_suffixes`: optional iterable if suffixes to ignore;
        if provided files ending in these suffixes will not be yielded
      * `sort_paths`: optional flag specifying that filenames should be sorted,
        default `False`
  '''
  if only_suffixes is not None:
    only_suffixes = tuple(only_suffixes)
  if skip_suffixes is not None:
    skip_suffixes = tuple(skip_suffixes)
  X("os.walk %s", dirpath)
  for subpath, subdirnames, filenames in os.walk(dirpath):
    X("RPATHS SUBPATH = %s", subpath)
    if sort_paths:
      subdirnames[:] = sorted(subdirnames)
      filenames = sorted(filenames)
    for filename in filenames:
      if skip_suffixes is not None and filename.endswith(skip_suffixes):
        continue
      if only_suffixes is not None and not filename.endswith(only_suffixes):
        continue
      yield relpath(joinpath(subpath, filename), dirpath)
