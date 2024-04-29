#!/usr/bin/env python3 -tt
#
# - Cameron Simpson <cs@cskk.id.au>
#

''' Utility routines to parse data streams into Blocks and Block streams into IndirectBlocks.
'''

from itertools import chain
import sys

from cs.buffer import CornuCopyBuffer
from cs.deco import fmtdoc, promote
from cs.logutils import warning
from cs.pfx import pfx
from cs.progress import progressbar
from cs.queues import IterableQueue
from cs.resources import uses_runstate
from cs.seq import tee
from cs.threads import bg as bg_thread
from cs.units import BINARY_BYTES_SCALE

from .block import Block, IndirectBlock, LiteralBlock
from .scan import (
    scan_offsets,
    scan_reblock,
    MIN_BLOCKSIZE,
    MAX_BLOCKSIZE,
)

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
      return LiteralBlock(data=b'')

    # we have a full IndirectBlock
    # if there are more, replace our blocks with
    #   indirect_blocks(topblock + nexttopblock + blocks)
    try:
      nexttopblock = next(blocks)
    except StopIteration:
      # just one IndirectBlock - we're done
      return topblock

    # add a layer of indirection and repeat
    blocks = indirect_blocks(chain((topblock, nexttopblock), blocks))

  raise RuntimeError("SHOULD NEVER BE REACHED")

def indirect_blocks(blocks):
  ''' A generator that yields full `IndirectBlock`s from an iterable
      source of `Block`s, except for the last `Block` which need not
      necessarily be bundled into an `IndirectBlock`.
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
            len(enc), MAX_BLOCKSIZE
        )
      else:
        yield IndirectBlock.from_subblocks(subblocks)
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
      block = IndirectBlock.from_subblocks(subblocks)
    yield block

@promote
def blockify(
    bfr: CornuCopyBuffer,
    *,
    chunks_name=None,
    scanner=None,
    min_block=None,
    max_block=None
):
  ''' Wrapper for `blocked_chunks_of` which yields `Block`s
      from the data from `bfr`.
  '''
  if chunks_name is None:
    chunks_name = bfr.__class__.__name__
  for chunk in progressbar(
      blocked_chunks_of(bfr, scanner=scanner, min_block=min_block,
                        max_block=max_block),
      label=f'blockify({chunks_name})',
      itemlenfunc=len,
      units_scale=BINARY_BYTES_SCALE,
      total=bfr.final_offset,
  ):
    yield Block.promote(chunk)

@promote
def block_for(bfr: CornuCopyBuffer, **blockify_kw) -> Block:
  ''' Return a top `Block` for the contents `bfr`, an iterable of
      `bytes`like objects such as a `CornuCopyBuffer`.
      This actually accepts any object suitable for `CornuCopyBuffer.promote`.

      Keyword arguments are passed to `blockify`.
  '''
  return top_block_for(blockify(bfr, **blockify_kw))

def spliced_blocks(B, new_blocks):
  ''' Splice (note *insert*) the iterable `new_blocks` into the data of the `Block` `B`.
      Yield high level blocks covering the result
      i.e. all the data from `B` with `new_blocks` inserted.

      The parameter `new_blocks` is an iterable of `(offset,Block)`
      where `offset` is a position for `Block` within `B`.
      The `Block`s in `new_blocks` must be in `offset` order
      and may not overlap.

      Example:

          >>> from .block import LiteralBlock as LB
          >>> B=LB(b'xxyyzz')
          >>> newBs=( (2,LB(b'aa')), (4,LB(b'bb')), (4,LB(b'cc')) )
          >>> splicedBs = spliced_blocks(B,newBs)
          >>> b''.join(map(bytes, splicedBs))
          b'xxaayybbcczz'
  '''
  # note that upto and offset count in the original space of `B`
  upto = 0  # data span from B yielded so far
  prev_offset = 0
  for offset, newB in new_blocks:
    # check splice poisition ordering
    assert offset >= prev_offset, (
        "new_block offset:%d < prev_offset:%d" % (offset, prev_offset)
    )
    prev_offset = offset
    # yield high level Blocks up to offset
    if offset > upto:
      # fill data from upto through to the new offset
      for fill_block in B.top_blocks(upto, offset):
        yield fill_block
        upto += len(fill_block)
        assert upto <= offset
      assert upto == offset
    elif offset < upto:
      raise ValueError(
          "new_blocks: offset=%d,newB=%s: this position has already been passed"
          % (offset, newB)
      )
    # splice in the new Block
    # the newly inserted data do not advance upto
    yield newB
  if upto < len(B):
    # yield high level Blocks for the data which follow
    yield from B.top_blocks(upto, len(B))

class _PendingBuffer:
  ''' Class to manage the unbound chunks accrued by `blocked_chunks_of` below.
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

# pylint: disable=too-many-statements,too-many-locals
@pfx
@uses_runstate
@fmtdoc
def blocked_chunks_of(
    chunks,
    *,
    scanner=None,
    min_block=None,
    max_block=None,
    runstate,
):
  ''' Generator which connects to a scanner of a chunk stream in
      order to emit low level edge aligned data chunks.

      Parameters:
      * `chunks`: a source iterable of data chunks, handed to `scanner`
      * `scanner`: optional callable accepting a `CornuCopyBuffer` and
        returning an iterable of `int`s, such as a generator. `scanner`
        may be `None`, in which case only the rolling hash is used
        to locate boundaries.
      * `min_block`: the smallest amount of data that will be used
        to create a Block, default from `MIN_BLOCKSIZE` (`{MIN_BLOCKSIZE}`)
      * `max_block`: the largest amount of data that will be used to
        create a Block, default from `MAX_BLOCKSIZE` (`{MAX_BLOCKSIZE}`)

      The iterable returned from `scanner(chunks)` yields `int`s which are
      considered desirable block boundaries.
  '''
  if min_block is None:
    min_block = MIN_BLOCKSIZE
  elif min_block < 8:
    raise ValueError("rejecting min_block < 8: %s" % (min_block,))
  if max_block is None:
    max_block = MAX_BLOCKSIZE
  elif max_block >= 1024 * 1024:
    raise ValueError("rejecting max_block >= 1024*1024: %s" % (max_block,))
  if min_block >= max_block:
    raise ValueError(
        "rejecting min_block:%d >= max_block:%d" % (min_block, max_block)
    )

  # No format aware scanner supplied, do a raw hash based reblocking.
  if not scanner:
    yield from scan_reblock(chunks, min_block=min_block, max_block=max_block)
    return

  # We have a format aware scanner to run against the data to locate
  # data format friendly block boundaries.
  #
  # We kick off a thread to run the scanner against the data
  # and write format aware offsets to a queue (offsetQ)
  # and tee the source data (chunks) to the thread and to the chunk
  # queue which is consuming the offsets.
  #
  # The final consumer reblocks the chunks according to the offsets
  # received and constrained by min_block and max_block.
  #

  # source data for aligned chunk construction
  dataQ = IterableQueue()
  # queue of offsets from the parser
  offsetQ = IterableQueue()
  # copy chunks to the parser and also to the post-parser chunk assembler
  tee_chunks = tee(chunks, dataQ)
  parse_bfr = CornuCopyBuffer(tee_chunks)

  def run_parser(runstate, bfr, min_block, max_block, offsetQ):
    ''' Thread body to scan `bfr` for offsets.
        The chunks are copied to `parseQ`, then their boundary offsets.

        If thwere is a scanner we scan the input data with it first.
        When it terminates (including from some exception), we scan
        the remaining chunks with scanbuf.

        The main function processes `parseQ` and uses its chunks and offsets
        to assemble aligned chunks of data.
    '''
    try:
      offset = 0
      if scanner:
        # Consume the chunks and offsets via a queue.
        # The scanner puts offsets onto the queue.
        # When the scanner fetches from the chunks, those chunks are copied to the queue.
        # Accordingly, chunks _should_ arrive before offsets within them.
        # pylint: disable=broad-except
        try:
          for offset in scanner(bfr):
            if runstate.cancelled:
              break
            # the scanner should yield only offsets, not chunks and offsets
            if not isinstance(offset, int):
              warning(
                  "discarding non-int from scanner %s: %s", scanner, offset
              )
            else:
              offsetQ.put(offset)
        except Exception as e:
          warning("exception from scanner %s: %s", scanner, e)
      # Consume the remainder of parse_bfr; the tee() will copy it to parseQ.
      # This is important to ensure that no chunk is missed.
      # We run these blocks through scanbuf() to find offsets.
      cso = bfr.offset  # offset after all the chunks so far
      assert offset <= cso
      sofar = cso - offset
      if sofar >= max_block:
        offsetQ.put(cso)
        sofar = 0
      for offset in scan_offsets(bfr, sofar=sofar, min_block=min_block,
                                 max_block=max_block):
        if runstate.cancelled:
          break
        offsetQ.put(cso + offset)
    finally:
      # end of offsets and chunks
      offsetQ.close()
      dataQ.close()

  # dispatch the parser
  bg_thread(
      run_parser,
      args=(runstate, parse_bfr, min_block, max_block, offsetQ),
      daemon=True,
  )

  # data source for assembling aligned chunks
  data_bfr = CornuCopyBuffer(dataQ)
  sofar = 0
  offset = None
  for offset in offsetQ:
    assert offset >= sofar
    block_size = offset - sofar
    assert block_size >= 0, (
        "block_size:%d <= 0 -- sofar=%d, offset=%d" %
        (block_size, sofar, offset)
    )
    if block_size < min_block:
      # skip over small edges
      assert scanner is not None, (
          "scanner=None but still got an overly near offset"
          " (sofar=%d, offset=%d => block_size=%d < min_block:%d)" %
          (sofar, offset, block_size, min_block)
      )
      continue
    subchunks = data_bfr.takev(block_size)
    assert sum(map(len, subchunks)) == block_size
    if block_size > max_block:
      # break up overly long blocks without a parser
      assert scanner is not None, (
          "scanner=None but still got an overly distant offset"
          " (sofar=%d, offset=%d => block_size=%d > max_block:%d)" %
          (sofar, offset, block_size, max_block)
      )
      yield from scan_reblock(
          subchunks, min_block=min_block, max_block=max_block
      )
    else:
      yield b''.join(subchunks)
    sofar += block_size
  bs = b''.join(data_bfr)
  if bs:
    assert len(bs) <= max_block
    yield bs

if __name__ == '__main__':
  from .blockify_tests import selftest
  selftest(sys.argv)
