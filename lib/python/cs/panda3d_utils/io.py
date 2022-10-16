#!/usr/bin/env python3

from zlib import decompress

from cs.pfx import pfx_call

def pzread(filename):
  ''' Read the zlib compressed data from `filename`
      and return it uncompressed.
  '''
  with pfx_call(open, filename, 'rb') as f:
    return decompress(f.read())
