#!/usr/bin/python
#
# Function associated with bytes, bytearrays, memoryviews and buffers in general.
#   - Cameron Simpson <cs@zip.com.au> 18mar2017
#

def extend(buf, min_size, chunks, copy_chunks=None):
  ''' Extend `buf` to at least `min_size` bytes, using data chunks from the iterator `chunks`.
      Return `buf` if unchanged, or the new buffer if extended.
      If there are insufficient data from `chunks` the returned buffer will be short.
      If `chunkQ` is not None, pass every new data chunk to `chunkQ.put`.
  '''
  length = len(buf)
  if length < min_size:
    bufs = [buf]
    while length < min_size:
      try:
        next_chunk = next(chunks)
      except StopIteration:
        break
      if copy_chunks is not None:
        copy_chunks(next_chunk)
      bufs.append(next_chunk)
      length += len(next_chunk)
    buf = memoryview(b''.join(bufs))
  return buf
