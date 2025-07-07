#!/usr/bin/env python3
#
# Empty, used to contain content_length(), now in cs.rfc2616.
#

from typing import Iterable, Mapping
import zlib

from cs.logutils import warning
from cs.pfx import Pfx
from cs.rfc2616 import content_encodings

# TODO: left over bytes? do I need to send a final b'' to the decompressor?
# I'd imagine it should be self terminating, but...
def decompress_stream(bss: Iterable[bytes]) -> Iterable[bytes]:
  ''' Decompress an iterable of `bytes` stream using gzip compatible decompression.
  '''
  # accept gzip header and trailer, automatically accept zlib as well
  # details: https://docs.python.org/3/library/zlib.html#zlib.decompress
  decompressor = zlib.decompressobj(wbits=47)
  offset = 0
  for i, zbs in enumerate(bss):
    try:
      bs = decompressor.decompress(zbs)
    except zlib.error as e:
      warning(
          "decompress_stream: block %d, offset %d: failed to decompress %r...: %s",
          i,
          offset,
          zbs[:16],
          e,
      )
      raise
    if len(bs) > 0:
      yield bs
    offset += len(zbs)
  if decompressor.unconsumed_tail:
    warning(
        "decompress_stream: after block %d, at offset %d: %d bytes of uncomsumed compressed data: %r",
        i, offset, decompressor.unconsumed_tail
    )

def decode_content(headers: Mapping[str, str],
                   bss: Iterable[bytes]) -> Iterable[bytes]:
  ''' A generator to decode `bss` according to the `Content-Encoding` header of `headers`.
  '''
  for encoding in content_encodings(headers):
    with Pfx("decode_content: %r", encoding):
      if encoding == 'identity':
        pass
      elif encoding in ('gzip', 'x-gzip'):
        bss = decompress_stream(bss)
      else:
        warning("cannot decode content-encoding, passing unchanged")
  yield from iter(bss)
