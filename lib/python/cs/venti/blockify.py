#!/usr/bin/python -tt
#
# Utility routines to parse data streams into Blocks and Block streams
# into IndirectBlocks.
#       - Cameron Simpson <cs@zip.com.au>
#

from functools import partial
from itertools import chain
import sys
from cs.logutils import PfxThread, debug, warning, exception, D, X
from cs.queues import IterableQueue
from cs.seq import tee
from .block import Block, IndirectBlock, dump_block
##from .parsers import rolling_hash_parser

MIN_BLOCKSIZE = 80          # less than this seems silly
MIN_AUTOBLOCKSIZE = 1024    # provides more scope for upstream block boundaries
MAX_BLOCKSIZE = 16383       # fits in 2 octets BS-encoded

def top_block_for(blocks):
  ''' Return a top Block for a stream of Blocks.
  '''
  # obtain stream of full indirect blocks from `blocks`
  blocks = indirect_blocks(blocks)

  # Fetch the first two indirect blocks from the generator.
  # If there is none, return a single empty direct block.
  # If there is just one, return it directly.
  # Otherwise there are at least two:
  # replace the blocks with another level of indirect_blocks()
  # reading from the two fetched blocks and the tail of the current
  # blocks then lather, rinse, repeat.
  #
  while True:
    try:
      topblock = next(blocks)
    except StopIteration:
      # no blocks - return the empty block - no data
      return Block(data=b'')

    # we have a full IndirectBlock
    # if there are more, replace our blocks with
    #   indirect_blocks(topblock + nexttopblock + blocks)
    try:
      nexttopblock = next(blocks)
    except StopIteration:
      # just one IndirectBlock - we're done
      return topblock

    # add a layer of indirection and repeat
    blocks = indirect_blocks(chain( ( topblock, nexttopblock ), blocks ))

  raise RuntimeError("SHOULD NEVER BE REACHED")

def indirect_blocks(blocks):
  ''' A generator that yields full IndirectBlocks from an iterable
      source of Blocks, except for the last Block which need not
      necessarily be bundled into an IndirectBlock.
  '''
  subblocks = []
  subsize = 0
  for block in blocks:
    enc = block.encode()
    if subsize + len(enc) > MAX_BLOCKSIZE:
      # overflow
      if not subblocks:
        # do not yield empty indirect block, flag logic error instead
        warning("no pending subblocks at flush, presumably len(block.encode()) %d > MAX_BLOCKSIZE %d",
                len(enc), MAX_BLOCKSIZE)
      else:
        yield IndirectBlock(subblocks)
        subblocks = []
        subsize = 0
    subblocks.append(block)
    subsize += len(enc)

  # handle the termination case
  if len(subblocks) > 0:
    if len(subblocks) == 1:
      # one block unyielded - don't bother wrapping into an iblock
      block = subblocks[0]
    else:
      block = IndirectBlock(subblocks)
    yield block

def blockify(data_chunks, parser=None):
  return blocks_of(data_chunks, parser)

def blocks_of(chunks, parser, min_block=None, max_block=None):
  ''' Wrapper for blocked_chunks_of which yields Blocks from the data chunks.
  '''
  for chunk in blocked_chunks_of(chunks, parser, min_block=min_block, max_block=max_block):
    yield Block(data=chunk)

class _PendingBuffer(object):
  ''' Class to manage the unbound chunks accrued by blocked_chunks_of below.
  '''

  def __init__(self, max_block):
    if max_block < 1:
      raise ValueError("max_block must be >= 1, received: %s" % (max_block,))
    self.max_block = max_block
    self._reset()

  def _reset(self):
    self.pending = []
    self.pending_room = self.max_block

  def flush(self):
    ''' Yield the pending chunks joined together, if any.
    '''
    if self.pending:
      assert self.pending_room < self.max_block
      yield b''.join(self.pending)
      self.pending = []
      self.pending_room = self.max_block

  def append(self, chunk):
    ''' Append `chunk` to the pending buffer.
        Yield any overflow.
    '''
    pending_room = self.pending_room
    while len(chunk) > pending_room:
      self.pending.append(chunk[:pending_room])
      self.pending_room = 0
      yield from self.flush()
      chunk = chunk[pending_room:]
      pending_room = self.pending_room
    self.pending.append(chunk)
    self.pending_room -= len(chunk)
    if self.pending_room == 0:
      yield from self.flush()

def blocked_chunks_of(chunks, parser, min_block=None, max_block=None, min_autoblock=None, dup_chunks=False):
  ''' Generator which connects to a parser of a chunk stream to emit low level edge aligned data chunks.
      `chunks`: a source iterable of data chunks, handed to `parser`
      `parser`: a callable accepting an iterable of data chunks and
        returning an iterable, such as a generator. `parser` may
        be None, in which case only the rolling hash is used to
        locate boundaries.
      `min_block`: the smallest amount of data that will be used
        to create a Block, default MIN_BLOCKSIZE
      `max_block`: the largest amount of data that will be used to
        create a Block, default MAX_BLOCKSIZE
      `min_autoblock`: the smallest amount of data that will be
        used for the rolling hash fallback if `parser` is not None,
        default MIN_AUTOBLOCK
      `dup_chunks`: default false; if true, the parser is not
        expected to yield the chunk data; instead a queue is made and
        the input chunks are tee()d to the parser and the queue.

      The iterable returned from `parser(chunks)` returns a mix of
      ints, which are considered desirable block boundaries, and
      bytes/memoryview objects which contain data from `chunks`.
      If `dup_chunks` is true, the parser should only yield offsets.

      The easiest `parser` functions to write are generators and
      one simple method of processing is to yield items from `chunks`
      as soon as they are collected, and then to parse data yielding
      edge offsets if found.
  '''
  with Pfx("blocked_chunks_of"):
    if min_block is None:
      min_block = MIN_BLOCKSIZE
    elif min_block < 8:
      raise ValueError("rejecting min_block < 8: %s" % (min_block,))
    if min_autoblock is None:
      min_autoblock = MIN_AUTOBLOCKSIZE
    elif min_autoblock < min_block:
      raise ValueError("rejecting min_autoblock:%d < min_block:%d"
                       % (min_autoblock, min_block,))
    if max_block is None:
      max_block = MAX_BLOCKSIZE
    elif max_block >= 1024*1024:
      raise ValueError("rejecting max_block >= 1024*1024: %s" % (max_block,))
    if min_block >= max_block:
      raise ValueError("rejecting min_block:%d >= max_block:%d"
                       % (min_block, max_block))
    # obtain iterator of chunks; avoids accidentally reusing chunks
    # if for example chunks is a sequence
    chunk_iter = iter(chunks)
    if parser is None:
      # no parser, consume the chunks directly
      parseQ = chunk_iter
      min_autoblock = min_block   # start the rolling hash earlier
    else:
      # consume the chunks via a queue
      parseQ = IterableQueue(16);
      chunk_iter = tee(chunk_iter, parseQ)
      def run_parser():
        try:
          for parsed in parser(chunk_iter):
            if dup_chunks:
              # the parser should yield only offsets, not chunks and offsets
              if not isinstance(parsed, int):
                warning("discarding non-int from parser %s: %s", parser, parsed)
              else:
                parseQ.put(parsed)
        except Exception as e:
          exception("exception from parser %s: %s", parser, e)
        # consume the remained of chunk_iter
        # the tee() will copy it to parseQ
        for chunk in chunk_iter:
          pass
        parseQ.close()
      PfxThread(target=run_parser).run()
    def get_parse():
      ''' Fetch the next item from `parseQ` and add to the inbound chunks or offsets.
          Sets parseQ to None if the end of the iterable is reached.
      '''
      nonlocal parseQ
      try:
        parsed = next(parseQ)
      except StopIteration:
        parseQ = None
      else:
        if isinstance(parsed, int):
          in_offsets.append(parsed)
        else:
          in_chunks.append(parsed)
    last_offset = None
    first_possible_point = None
    next_rolling_point = None
    max_possible_point = None
    def recompute_offsets():
      ''' Recompute relevant offsets from the block parameters.
          The first_possible_point is last_offset+min_block,
            the earliest point at which we will accept a block boundary.
          The next_rolling_point is the offset at which we should
            start looking for automatic boundaries with the rolling
            hash algorithm. Without an upstream parser this is the same
            as first_possible_point, but if there is a parser then it
            is further to give more opportunity for a parser boundary
            to be used in preference to an automatic boundary.
      '''
      nonlocal last_offset, first_possible_point, next_rolling_point, max_possible_point
      first_possible_point = last_offset + min_block
      next_rolling_point = last_offset + min_autoblock
      max_possible_point = last_offset + max_block
    # prepare initial state
    next_offset = 0
    last_offset = 0
    recompute_offsets()
    hash_value = 0
    offset = 0
    # inbound chunks and offsets
    in_chunks = []
    in_offsets = []
    # unblocked outbound data
    pending = _PendingBuffer(max_block)
    # Read data chunks and locate desired boundaries.
    while parseQ is not None or in_chunks:
      while parseQ is not None and not in_chunks:
        get_parse()
      if in_chunks:
        chunk = memoryview(in_chunks.pop(0))
        # process current chunk
        while chunk:
          chunk_end_offset = offset + len(chunk)
          advance_by = None
          release = False   # becomes true if we should flush after taking data
          # see if we can skip some data completely
          # we don't care where the nnext_offset is if offset < first_possible_point
          if first_possible_point > offset:
            advance_by = min(first_possible_point - offset, len(chunk))
            hash_value = 0
          elif next_offset == offset:
            # flush buffer if any but zero advance
            release = True
            advance_by = 0
          else:
            # advance next_offset to something useful > offset
            while next_offset is not None and next_offset <= offset:
              while parseQ is not None and not in_offsets:
                get_parse()
              if in_offsets:
                next_offset2 = in_offsets.pop(0)
                assert isinstance(next_offset2, int)
                if next_offset2 < next_offset:
                  warning("next offset %d < current next_offset:%d",
                          next_offset2, next_offset)
                else:
                  next_offset = next_offset2
              else:
                next_offset = None
            ##X("skip=%d, nothing to skip", skip)
            # how far to scan with the rolling hash, being from here to
            # next_offset minus a min_block buffer, capped by the length of
            # the current chunk
            scan_to = min(max_possible_point, chunk_end_offset)
            if next_offset is not None:
              scan_to = min(scan_to, next_offset-min_block)
            if scan_to > offset:
              scan_len = scan_to - offset
              found_offset = None
              chunk_prefix = chunk[:scan_len]
              for upto, b in enumerate(chunk[:scan_len]):
                hash_value = ( ( ( hash_value & 0x001fffff ) << 7
                               )
                             | ( ( b & 0x7f )^( (b & 0x80)>>7 )
                               )
                             )
                if hash_value % 4093 == 1:
                  # found an edge with the rolling hash
                  ##X("rolling hash found edge at %d bytes", upto)
                  release = True
                  advance_by = upto + 1
                  break
              if advance_by is None:
                advance_by = scan_len
            else:
              # nothing to skip, nothing to hash scan
              # ==> take everything up to next_offset
              # (and reset the hash)
              if next_offset is None:
                take_to = chunk_end_offset
              else:
                take_to = min(next_offset, chunk_end_offset)
              advance_by = take_to - offset
          # advance through this chunk
          # buffer the advance
          # release ==> flush the buffer and update last_offset
          assert advance_by is not None
          assert advance_by >= 0
          assert advance_by <= len(chunk)
          yield from pending.append(chunk[:advance_by])
          offset += advance_by
          chunk = chunk[advance_by:]
          if release:
            yield from pending.flush()
            last_offset = offset
            recompute_offsets()
            hash_value = 0
    # yield any left over data
    yield from pending.flush()

if __name__ == '__main__':
  import cs.venti.blockify_tests
  cs.venti.blockify_tests.selftest(sys.argv)
