#!/usr/bin/python -tt
#
# - Cameron Simpson <cs@cskk.id.au>
#

''' Utility routines to parse data streams into Blocks and Block streams into IndirectBlocks.
'''

from heapq import heappush, heappop
from itertools import chain
import sys
from cs.buffer import CornuCopyBuffer
from cs.logutils import warning, exception
from cs.pfx import Pfx, PfxThread
from cs.queues import IterableQueue
from cs.seq import tee
from .block import Block, IndirectBlock
from .scan import scanbuf

# constraints on the chunk sizes yields from blocked_chunks_of
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
        warning(
            "no pending subblocks at flush, presumably len(block.encode()) %d > MAX_BLOCKSIZE %d",
            len(enc), MAX_BLOCKSIZE)
      else:
        yield IndirectBlock(subblocks)
        subblocks = []
        subsize = 0
    subblocks.append(block)
    subsize += len(enc)

  # handle the termination case
  if subblocks:
    if len(subblocks) == 1:
      # one block unyielded - don't bother wrapping into an iblock
      block = subblocks[0]
    else:
      block = IndirectBlock(subblocks)
    yield block

def blockify(chunks, scanner=None, min_block=None, max_block=None):
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

class _PendingBuffer:
  ''' Class to manage the unbound chunks accrued by blocked_chunks_of below.
  '''

  def __init__(self, max_block, offset=0):
    if max_block < 1:
      raise ValueError("max_block must be >= 1, received: %s" % (max_block,))
    self.max_block = max_block
    self.pending = []
    self.pending_room = self.max_block
    self.offset = offset

  def flush(self):
    ''' Yield the pending chunks joined together, if any.
        Advance the offset.
    '''
    if self.pending:
      assert self.pending_room < self.max_block
      chunk = b''.join(self.pending)
      self.pending = []
      self.pending_room = self.max_block
      self.offset += len(chunk)
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

def blocked_chunks_of(
    chunks,
    scanner=None,
    min_block=None, max_block=None,
    histogram=None,
):
  ''' Generator which connects to a scanner of a chunk stream in
      order to emit low level edge aligned data chunks.

      Parameters:
      * `chunks`: a source iterable of data chunks, handed to `scanner`
      * `scanner`: optional callable accepting a CornuCopyBuffer and
        returning an iterable of ints, such as a generator. `scanner`
        may be None, in which case only the rolling hash is used
        to locate boundaries.
      * `min_block`: the smallest amount of data that will be used
        to create a Block, default MIN_BLOCKSIZE
      * `max_block`: the largest amount of data that will be used to
        create a Block, default MAX_BLOCKSIZE
      * `histogram`: if not None, a defaultdict(int) to collate counts.
        Integer indices count block sizes and string indices are used
        for 'bytes_total' and 'bytes_hash_scanned'.

      The iterable returned from `scanner(chunks)` yields ints which are
      considered desirable block boundaries.
  '''
  # pylint: disable=too-many-nested-blocks,too-many-statements
  # pylint: disable=too-many-branches,too-many-locals
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
    # The tee() arranges that chunks arrive before any offsets within them.
    if scanner is None:
      # No scanner, consume the chunks directly.
      parseQ = chunk_iter
    else:
      # Consume the chunks and offsets via a queue.
      # The scanner puts offsets onto the queue.
      # When the scanner fetches from the chunks, those chunks are copied to the queue.
      # When the scanner terminates, any remaining chunks are also copied to the queue.
      parseQ = IterableQueue()
      chunk_iter = tee(chunk_iter, parseQ)
      def run_parser():
        ''' Thread body to run the supplied scanner against the input data.
        '''
        bfr = CornuCopyBuffer(chunk_iter)
        # pylint: disable=broad-except
        try:
          for offset in scanner(bfr):
            # the scanner should yield only offsets, not chunks and offsets
            if not isinstance(offset, int):
              warning("discarding non-int from scanner %s: %s", scanner, offset)
            else:
              parseQ.put(offset)
        except Exception as e:
          exception("exception from scanner %s: %s", scanner, e)
        # Consume the remainder of chunk_iter; the tee() will copy it to parseQ.
        for _ in chunk_iter:
          pass
        # end of offsets and chunks
        parseQ.close()
      PfxThread(target=run_parser).run()
    # inbound chunks and offsets
    in_offsets = []     # heap of unprocessed edge offsets
    # prime `available_chunk` with the first data chunk, ready for get_next_chunk
    try:
      available_chunk = next(parseQ)
    except StopIteration:
      # no data! just return
      return
    def get_next_chunk():
      ''' Fetch and return the next data chunk from the `parseQ`.
          Return None at end of input.
          Also gather all the following offsets from the queue before return.
          Because this inherently means collecting the chunk beyond
          these offsets, we keep that in `available_chunk` for the
          next call.
          Sets parseQ to None if the end of the iterable is reached.
      '''
      nonlocal parseQ, in_offsets, hash_value, available_chunk
      if parseQ is None:
        assert available_chunk is None
        return None
      next_chunk = available_chunk
      available_chunk = None
      assert not isinstance(next_chunk, int)
      # scan the new chunk and load potential edges into the offset heap
      hash_value, chunk_scan_offsets = scanbuf(hash_value, next_chunk)
      for cso in chunk_scan_offsets:
        heappush(in_offsets, offset + cso)
      # gather items from the parseQ until the following chunk
      # or end of input
      while True:
        try:
          item = next(parseQ)
        except StopIteration:
          parseQ = None
          break
        else:
          if isinstance(item, int):
            heappush(in_offsets, item)
          else:
            available_chunk = item
            break
      return next_chunk
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
    last_offset = 0         # latest released boundary
    recompute_offsets()     # compute first_possible_point and max_possible_point
    hash_value = 0
    offset = 0
    chunk0 = None
    offset0 = None
    # unblocked outbound data
    pending = _PendingBuffer(max_block)
    # Read data chunks and locate desired boundaries.
    while True:
      chunk = get_next_chunk()
      if chunk is None:
        break
      # verify current chunk start offset against end of previous chunk
      assert chunk0 is None or offset == offset0 + len(chunk0), \
          "offset0=%d, len(chunk0)=%d: sum(%d) != current offset %d" \
          % (offset0, len(chunk0), offset0 + len(chunk0), offset)
      chunk0 = chunk
      offset0 = offset
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
            # if the flush discarded a full buffer we need to adjust our boundaries
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
            recompute_offsets()
          if not chunk:
            # consumed the end of the chunk, need a new one
            break
        advance_by = None
        # fetch the next available edge, None if nothing available or suitable
        while True:
          if in_offsets:
            next_offset = heappop(in_offsets)
            if next_offset > offset and next_offset >= first_possible_point:
              break
          else:
            next_offset = None
            break
        if next_offset is None or next_offset > chunk_end_offset:
          # no suitable edge: consume the chunk and advance
          take_to = chunk_end_offset
        else:
          # edge before end of chunk: use it
          take_to = next_offset
          release = True
        advance_by = take_to - offset
        assert advance_by > 0
    # yield any left over data
    for out_chunk in pending.flush():
      yield out_chunk
      if histogram is not None:
        out_chunk_size = len(out_chunk)
        histogram['bytes_total'] += out_chunk_size
        histogram[out_chunk_size] += 1

if __name__ == '__main__':
  from .blockify_tests import selftest
  selftest(sys.argv)
