#!/usr/bin/env python3

from zlib import decompress

def pzread(filename):
  ''' Read the zlib compressed data from `filename`
      and return it uncompressed.
  '''
  with open(filename, 'rb') as f:
    return decompress(f.read())
