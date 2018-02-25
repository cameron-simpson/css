#!/usr/bin/python -tt
#
# Utility routines to parse data streams into Blocks and Block streams
# into IndirectBlocks.
#       - Cameron Simpson <cs@cskk.id.au>
#

from heapq import heapify, heappushpop, heappop
from itertools import chain
import sys
from cs.buffer import CornuCopyBuffer
from cs.logutils import debug, warning, error, exception, D
from cs.pfx import Pfx, PfxThread
from cs.queues import IterableQueue
from cs.seq import tee
from cs.x import X
from .block import Block, IndirectBlock
from .scan import scanbuf

MIN_BLOCKSIZE = 80          # less than this seems silly
MAX_BLOCKSIZE = 16383       # fits in 2 octets BS-encoded

# default read size for file scans
DEFAULT_SCAN_SIZE = 1024 * 1024

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

def blockify(data_chunks, scanner=None):
  return blocks_of(data_chunks, scanner)

def blocks_of(chunks, scanner, min_block=None, max_block=None):
  ''' Wrapper for blocked_chunks_of which yields Blocks from the data chunks.
  '''
  for chunk in blocked_chunks_of(chunks, scanner, min_block=min_block, max_block=max_block):
    yield Block(data=chunk)

def spliced_blocks(B, new_blocks):
  ''' Splice an iterable of (offset, Block) into the data of the Block `B`.
      Yield high level blocks.
  '''
  upto = 0      # data span yielded so far
  for offset, newB in new_blocks:
    if offset > upto:
      yield from B.top_blocks(upto, offset)
      upto = offset
    yield newB
    upto += len(newB)
  if upto < len(B):
    yield from B.top_blocks(upto, len(B))

class _PendingBuffer(object):
  ''' Class to manage the unbound chunks accrued by blocked_chunks_of below.
  '''

  def __init__(self, max_block, offset=0):
    if max_block < 1:
      raise ValueError("max_block must be >= 1, received: %s" % (max_block,))
    self.max_block = max_block
    self.offset = offset
    self._reset()

  def _reset(self):
    self.pending = []
    self.pending_room = self.max_block

  def flush(self):
    ''' Yield the pending chunks joined together, if any.
        Advance the offset.
    '''
    if self.pending:
      assert self.pending_room < self.max_block
      chunk = b''.join(self.pending)
      self._reset()
      self.offset += len(chunk)
      ##X("_PendingBuffer.flush: yield %d bytes", len(chunk))
      yield chunk

  def append(self, chunk):
    ''' Append `chunk` to the pending buffer.
        Yield any overflow.
    '''
    pending_room = self.pending_room
    while len(chunk) >= pending_room:
      self.pending.append(chunk[:pending_room])
      self.pending_room = 0
      yield from self.flush()
      chunk = chunk[pending_room:]
      pending_room = self.pending_room
    if chunk:
      self.pending.append(chunk)
      self.pending_room -= len(chunk)

def blocked_chunks_of(chunks, scanner,
        min_block=None, max_block=None,
        histogram=None):
  ''' Generator which connects to a scanner of a chunk stream in order to emit low level edge aligned data chunks.
      `chunks`: a source iterable of data chunks, handed to `scanner`
      `scanner`: a callable accepting a CornuCopyBuffer and
        returning an iterable of ints, such as a generator. `scanner`
        may be None, in which case only the rolling hash is used
        to locate boundaries.
      `min_block`: the smallest amount of data that will be used
        to create a Block, default MIN_BLOCKSIZE
      `max_block`: the largest amount of data that will be used to
        create a Block, default MAX_BLOCKSIZE
      `histogram`: if not None, a defaultdict(int) to collate counts.
        Integer indices count block sizes and string indices are used
        for 'bytes_total' and 'bytes_hash_scanned'.

      The iterable returned from `scanner(chunks)` yields ints which are
      considered desirable block boundaries.
  '''
  with Pfx("blocked_chunks_of"):
    if min_block is None:
      min_block = MIN_BLOCKSIZE
    elif min_block < 8:
      raise ValueError("rejecting min_block < 8: %s" % (min_block,))
    if max_block is None:
      max_block = MAX_BLOCKSIZE
    elif max_block >= 1024*1024:
      raise ValueError("rejecting max_block >= 1024*1024: %s" % (max_block,))
    if min_block >= max_block:
      raise ValueError("rejecting min_block:%d >= max_block:%d"
                       % (min_block, max_block))
    # obtain iterator of chunks; this avoids accidentally reusing the chunks
    # if for example chunks is a sequence
    chunk_iter = iter(chunks)
    # Set up parseQ, an iterable yielding a mix of source data and
    # offsets representing desirable block boundaries.
    # If there is no scanner, this is just chunk_iter.
    # If there is a scanner we dispatch the scanner in a separate
    # Thread and feed it a tee() of chunk_iter, which copies chunks
    # to the parseQ when chunks are obtained by the scanner. The
    # Thread runs the scanner and copies its output offsets to the
    # parseQ.
    if scanner is None:
      # No scanner, consume the chunks directly.
      parseQ = chunk_iter
    else:
      # Consume the chunks and offsets via a queue.
      # The scanner puts offsets onto the queue.
      # When the scanner fetches from the chunks, those chunks are copied to the queue.
      # When the scanner terminates, any remaining chunks are also copied to the queue.
      parseQ = IterableQueue();
      chunk_iter = tee(chunk_iter, parseQ)
      def run_parser():
        try:
          for offset in scanner(CornuCopyBuffer(chunk_iter)):
            # the scanner should yield only offsets, not chunks and offsets
            if not isinstance(offset, int):
              warning("discarding non-int from scanner %s: %s", scanner, offset)
            else:
              parseQ.put(offset)
        except Exception as e:
          exception("exception from scanner %s: %s", scanner, e)
        # Consume the remainder of chunk_iter; the tee() will copy it to parseQ.
        for chunk in chunk_iter:
          pass
        # end of offsets and chunks
        parseQ.close()
      PfxThread(target=run_parser).run()
    # inbound chunks and offsets
    in_chunks = []  # unprocessed chunks
    in_offsets = [] # inbound parse offsets
    def get_parse():
      ''' Fetch the next item from `parseQ` and add to the inbound chunks or offsets.
          Sets parseQ to None if the end of the iterable is reached.
      '''
      nonlocal parseQ, in_offsets, in_chunks
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
    max_possible_point = None
    def recompute_offsets():
      ''' Recompute relevant offsets from the block parameters.
          The first_possible_point is last_offset+min_block,
            the earliest point at which we will accept a block boundary.
          The max_possible_point is last_offset+max_block,
            the latest point at which we will accept a block boundary;
            we will choose this if no next_offset or hash offset
            is found earlier.
      '''
      nonlocal last_offset, first_possible_point, max_possible_point
      first_possible_point = last_offset + min_block
      max_possible_point   = last_offset + max_block
      ##X("recomputed offsets: last_offset=%d, first_possible_point=%d, max_possible_point=%d",
      ##  last_offset, first_possible_point, max_possible_point)
    # prepare initial state
    next_offset = 0
    last_offset = 0
    recompute_offsets()
    hash_value = 0
    offset = 0
    # unblocked outbound data
    pending = _PendingBuffer(max_block)
    # Read data chunks and locate desired boundaries.
    while parseQ is not None or in_chunks:
      while parseQ is not None and not in_chunks:
        get_parse()
      if not in_chunks:
        # no more data
        break
      chunk = in_chunks.pop(0)
      hash_value, chunk_scan_offsets = scanbuf(hash_value, chunk)
      chunk_scan_offsets = [ offset + cso for cso in chunk_scan_offsets ]
      heapify(chunk_scan_offsets)
      chunk = memoryview(chunk)
      chunk_end_offset = offset + len(chunk)
      # process current chunk
      advance_by = 0    # how much data to add to the pending buffer
      release = False   # whether we hit a boundary ==> flush the buffer
      while chunk:
        if advance_by > 0:
          # advance through this chunk
          # buffer the advance
          # release ==> flush the buffer and update last_offset
          assert advance_by is not None
          assert advance_by >= 0
          assert advance_by <= len(chunk)
          ##X("ADVANCE_BY %d: %s", advance_by, why)
          # save the advance bytes and yield any overflow
          for out_chunk in pending.append(chunk[:advance_by]):
            yield out_chunk
            if histogram is not None:
              out_chunk_size = len(out_chunk)
              histogram['bytes_total'] += out_chunk_size
              histogram[out_chunk_size] += 1
              histogram['buffer_overflow_chunks'] += 1
          offset += advance_by
          chunk = chunk[advance_by:]
          if last_offset != pending.offset:
            last_offset = pending.offset
            recompute_offsets()
          if release:
            release = False   # becomes true if we should flush after taking data
            # yield the current pending data
            for out_chunk in pending.flush():
              yield out_chunk
              if histogram is not None:
                out_chunk_size = len(out_chunk)
                histogram['bytes_total'] += out_chunk_size
                histogram[out_chunk_size] += 1
            last_offset = pending.offset
            ##X("RELEASED: "+release)
            recompute_offsets()
          if not chunk:
            # consumed the end of the chunk, need a new one
            break
        advance_by = None
        why = None
        if False:
          Xoffsets( { 'pending': pending.offset,
                      'offset': offset,
                      'last_offset': last_offset,
                      'next_offset': next_offset,
                      'first_point': first_possible_point,
                      'max_point': max_possible_point,
                      'chunk_end': chunk_end_offset,
                    } )
        # see if we can skip some data completely
        # we don't care where the next_offset is if offset < first_possible_point
        if first_possible_point > offset:
          advance_by = min(first_possible_point - offset, len(chunk))
          why = "first_possible_point > offset"
          continue
        # advance next_offset to something useful > offset
        while next_offset is not None and next_offset <= offset:
          while parseQ is not None and not in_offsets:
            get_parse()
          if in_offsets:
            next_offset2 = in_offsets.pop(0)
            if next_offset2 < next_offset:
              warning("next offset %d < current next_offset:%d",
                      next_offset2, next_offset)
            else:
              next_offset = heappushpop(chunk_scan_offsets, next_offset2)
              ##X("NEXT_OFFSET = %d", next_offset)
          elif chunk_scan_offsets:
            # nothing from parser, but still offsets in the scan
            next_offset = heappop(chunk_scan_offsets)
          else:
            ##X("END OF OFFSETS")
            next_offset = None
        # nothing to skip, nothing to hash scan
        # ==> take everything up to next_offset
        # (and reset the hash)
        if next_offset is None or next_offset > chunk_end_offset:
          take_to = chunk_end_offset
          why = "chunk_end_offset very close"
        else:
          take_to = next_offset
          why = "next_offset very close"
          release = True
        advance_by = take_to - offset

    # yield any left over data
    for out_chunk in pending.flush():
      yield out_chunk
      if histogram is not None:
        out_chunk_size = len(out_chunk)
        histogram['bytes_total'] += out_chunk_size
        histogram[out_chunk_size] += 1

def Xoffsets(d):
  X(' => '.join([ "%s:%s" % (k2,v2) for v2, k2 in sorted([ ((-1 if v is None else v), k) for k, v in d.items() ]) ]))

if __name__ == '__main__':
  from .blockify_tests import selftest
  selftest(sys.argv)
