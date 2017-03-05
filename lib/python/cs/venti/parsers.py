#!/usr/bin/python
#
# Parsers for data streams, emitting data and offsets.
# These sit in front of the core rolling hash blockifier.
#   - Cameron Simpson <cs@zip.com.au> 05mar2017
#

def rolling_hash_parser(data_chunks, vocab=None, min_block=None, max_block=None):
  ''' Collect data strings from the iterable data_chunks and yield data Blocks with desirable boundaries.
      Note: this parser always yields the offset of the end of its
      data, making it suitably for cleaning up the last data from
      some parser which doesn't always emit its final offset.
  '''
  # also accept a bare bytes value
  if isinstance(data_chunks, bytes):
    data_chunks = (data_chunks,)
  if vocab is None:
    vocab = DFLT_VOCAB
  if min_block is None:
    min_block = MIN_BLOCKSIZE
  elif min_block < 8:
    raise ValueError("rejecting min_block < 8: %s", min_block)
  if max_block is None:
    max_block = MIN_BLOCKSIZE
  elif max_block >= 1024*1024:
    raise ValueError("rejecting max_block >= 1024*1024: %s", max_block)
  chunkQ = IterableQueue()
  yield chunkQ
  hash_value = 0    # initial rolling hash value
  offset = 0        # scan position
  last_offset = 0   # last offset yielded to consumer
  end_offset = 0    # offset of the end of the latest received chunk
  for data in data_chunks:
    chunkQ.put(data)
    end_offset += len(data)
    for data_offset, b in enumerate(data):
      # advance the rolling hash function
      hash_value = ( ( ( hash_value & 0x001fffff ) << 7
                     )
                   | ( ( b & 0x7f )^( (b & 0x80)>>7 )
                     )
                   )
      offset += 1
      if hash_value % 4093 == 1:
        yield offset
        last_offset = offset
      else:
        # test against the current vocabulary
        is_edge, edge_offset, subVocab = vocab.test_for_edge(data, data_offset)
        if is_edge:
          if edge_offset < 0 or data_offset + edge_offset >= len(data):
            raise RuntimeError("len(data)=%d and edge_offset=%s; data=%r"
                               % (len(data), edge_offset, data))
          # boundary offset is the current offset adjusted for the edge_offset
          suboffset = offset + edge_offset
          if suboffset > last_offset and suboffset <= end_offset:
            # only emit this offset if it is in range:
            # not before the previously emitted offset
            # and not after the end of the data chunk we have
            # provided to the parser handler
            yield suboffset
            last_offset = suboffset
          if subVocab is not None:
            # switch to new vocabulary
            vocab = subVocab
  if last_offset < end_offset:
    yield end_offset
  chunkQ.close()
