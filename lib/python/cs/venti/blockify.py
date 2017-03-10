#!/usr/bin/python -tt
#
# Utility routines to parse data streams into Blocks and Block streams
# into IndirectBlocks.
#       - Cameron Simpson <cs@zip.com.au>
#

from functools import partial
from itertools import chain
import sys
from cs.logutils import debug, warning, D, X
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

_mp3_audio_ids = [ 2.5, None, 2, 1 ]
_mp3_layer     = [ None, 3, 2, 1 ]
_mp3_crc       = [ True, False ]
_mp3_br_v1_l1  = [ None, 32, 64, 96, 128, 160, 192, 224,
                   256, 288, 320, 352, 384, 416, 448, None ]
_mp3_br_v1_l2  = [ None, 32, 48, 56, 64, 80, 96, 112,
                   128, 160, 192, 224, 256, 320, 384, None ]
_mp3_br_v1_l3  = [ None, 32, 40, 48, 56, 64, 80, 96,
                   112, 128, 160, 192, 224, 256, 320, None ]
_mp3_br_v2_l1  = [ None, 32, 48, 56, 64, 80, 96, 112,
                   128, 144, 160, 176, 192, 224, 256, None ]
_mp3_br_v2_l23 = [ None, 8, 16, 24, 32, 40, 48, 56,
                   64, 80, 96, 112, 128, 144, 160, None ]
_mp3_sr_m1     = [ 44100, 48000, 32000, None ]
_mp3_sr_m2     = [ 22050, 24000, 16000, None ]
_mp3_sr_m25    = [ 11025, 12000, 8000, None ]

def mp3frames(fp):
  ''' Read MP3 data from `fp` and yield frame data chunks.
      Based on:
        http://www.mp3-tech.org/programmer/frame_header.html
  '''
  chunk = ''
  while True:
    while len(chunk) < 4:
      bs = fp.read(4-len(chunk))
      if len(bs) == 0:
        break
      chunk += bs
    if len(chunk) == 0:
      return
    assert len(chunk) >= 4, "short data at end of fp"

    if chunk.startswith("TAG"):
      frame_len = 128
    elif chunk.startswith("ID3"):
      D("ID3")
      # TODO: suck up a few more bytes and compute length
      return
    else:
      hdr_bytes = map(ord, chunk[:4])
      ##D("%r", hdr_bytes)
      assert hdr_bytes[0] == 255 and (hdr_bytes[1]&224) == 224, "not a frame header: %s" % (chunk,)
      audio_vid = _mp3_audio_ids[ (hdr_bytes[1]&24) >> 3 ]
      layer = _mp3_layer[ (hdr_bytes[1]&6) >> 1 ]

      has_crc = not _mp3_crc[ hdr_bytes[1]&1 ]

      bri = (hdr_bytes[2]&240) >> 4
      if audio_vid == 1:
        if layer == 1:
          bitrate = _mp3_br_v1_l1[bri]
        elif layer == 2:
          bitrate = _mp3_br_v1_l2[bri]
        elif layer == 3:
          bitrate = _mp3_br_v1_l3[bri]
        else:
          assert False, "bogus layer (%s)" % (layer,)
      elif audio_vid == 2 or audio_vid == 2.5:
        if layer == 1:
          bitrate = _mp3_br_v2_l1[bri]
        elif layer == 2 or layer == 3:
          bitrate = _mp3_br_v2_l23[bri]
        else:
          assert False, "bogus layer (%s)" % (layer,)
      else:
        assert False, "bogus audio_vid (%s)" % (audio_vid,)

      sri = (hdr_bytes[2]&12) >> 2
      if audio_vid == 1:
        samplingrate = _mp3_sr_m1[sri]
      elif audio_vid == 2:
        samplingrate = _mp3_sr_m2[sri]
      elif audio_vid == 2.5:
        samplingrate = _mp3_sr_m25[sri]
      else:
        assert False, "unsupported id (%s)" % (audio_vid,)

      padding = (hdr_bytes[2]&2) >> 1

      # TODO: surely this is wrong? seems to include header in audio sample
      if layer == 1:
        data_len = (12 * bitrate * 1000 / samplingrate + padding) * 4
      elif layer == 2 or layer == 3:
        data_len = 144 * bitrate * 1000 / samplingrate + padding
      else:
        assert False, "layer=%s" % (layer,)

      frame_len = data_len
      if has_crc:
        frame_len += 2

    ##print("vid =", audio_vid, "layer =", layer, "has_crc =", has_crc, "frame_len =", frame_len, "bitrate =", bitrate, "samplingrate =", samplingrate, "padding =", padding, file=sys.stderr)
    while len(chunk) < frame_len:
      bs = fp.read(frame_len - len(chunk))
      if len(bs) == 0:
        break
      chunk += bs
    assert len(chunk) >= frame_len

    yield chunk[:frame_len]
    chunk = chunk[frame_len:]

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

def blocked_chunks_of(chunks, parser, min_block=None, max_block=None, min_autoblock=None):
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
      The iterable returned from `parser(chunks)` is denoted `offsetQ`.
      It first yields an iterable denoted `chunkQ` (which will
      yield unaligned data chunks) and thereafter offsets which
      represent desirable Block bounaries.

      The parser must arrange that after an offset is
      collected from `offsetQ` sufficient data chunks will be
      available on `chunkQ` to reach that offset, allowing this
      function to assemble complete well aligned data chunks.

      The parser must always emit a final offset at the end of its data.

      The easiest `parser` functions to write are generators. One
      can allocate and yield an IterableQueue for the data chunks
      and then yield offsets directly. To coordinate with
      blocked_chunks_of the easiest thing is probably to put data
      onto `chunkQ` as soon as it is read, and then parse the read
      data for boundary offsets. The IterableQueue should have
      enough capacity to hold whatever chunks arrive before an
      offset is emitted.
  '''
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
  if parser is None:
    ##X("no parser, using chunks directly")
    offsetQ = iter(())
    chunkQ = iter(chunks)
    min_autoblock = min_block   # start the rolling hash earlier
  else:
    offsetQ = parser(chunks)
    try:
      chunkQ = next(offsetQ)
    except StopIteration as e:
      raise RuntimeError("chunkQ not received from offsetQ as first item: %s" % (e,))
  def new_offsets(next_offset, offsetQ):
    ''' Compute relevant offsets from the block parameters.
        The first_possible_point is last_offset+min_block,
          the earliest point at which we will accept a block boundary.
        The next_rolling_point is the offset at which we should
          start looking for automatic boundaries with the rolling
          hash algorithm. Without an upstream parser this is the same
          as first_possible_point, but if there is a parser then it
          is further to give more opportunity for a parser boundary
          to be used in preference to an automatic boundary.
        The next_offset is the next offset received from the upstream
          parser which is greater than or equal to the first_possible_point
          unless we have run out of upstream offsets.
    '''
    first_possible_point = last_offset + min_block
    next_rolling_point = last_offset + min_autoblock
    if next_offset is not None:
      while next_offset < first_possible_point:
        try:
          next_offset2 = next(offsetQ)
        except StopIteration:
          offsetQ = ()
          next_offset = None
          break
        if next_offset2 <= next_offset:
          warning("ignoring new offset %d <= current next_offset %d",
                  next_offset2, next_offset)
        else:
          next_offset = next_offset2
    ##X("new_offsets: first_possible_point=%d, next_rolling_point=%d, next_offset=%s",
    ##  first_possible_point, next_rolling_point, next_offset)
    return first_possible_point, next_rolling_point, next_offset
  # prepare initial state
  next_offset = 0
  last_offset = 0
  first_possible_point, next_rolling_point, next_offset \
    = new_offsets(next_offset, offsetQ)
  offset = 0
  pending = _PendingBuffer(max_block)
  # Read data chunks and locate desired boundaries.
  # Because this is a CPU hotspot, we put some work into minimising
  # the compute per byte. We scan the chunk in the following phases:
  #   - we completely skip any leading part of the chunk that falls below min_block
  #   - if next_offset if beyond the 
  for chunk in chunkQ:
    ##X("new chunk, %d bytes...", len(chunk))
    chunk_len_last = len(chunk)
    while chunk:
      # see if we can skip some data completely
      skip = min(first_possible_point-offset, len(chunk))
      if skip > 0:
        ##warning("skip %d bytes", skip)
        left = chunk[:skip]
        yield from pending.append(left)
        chunk = chunk[skip:]
        offset += skip
        ##X("redo with remaining chunk")
        continue
      ##X("skip=%d, nothing to skip", skip)
      # how far to scan with the rolling hash, being from here to
      # next_offset minus a min_block buffer, capped by the length of
      # the current chunk
      scan = len(chunk)
      ##X("scan to end of chunk would be %d bytes", scan)
      if next_offset is not None:
        scan = min(scan, next_offset-offset)
      ##X("  scan adjusted to %d (offset=%d, next_offset=%s)",
      ##  scan, offset, next_offset)
      if scan > 0:
        ##X("scan %d bytes with rolling hash...", scan)
        hash_value = 0
        offset0 = offset
        for upto, b in enumerate(chunk[:scan]):
          hash_value = ( ( ( hash_value & 0x001fffff ) << 7
                         )
                       | ( ( b & 0x7f )^( (b & 0x80)>>7 )
                         )
                       )
          if hash_value % 4093 == 1:
            # found an edge with the rolling hash
            # yield the pending data
            # advance pointers, recompute new scan points
            ##X("rolling hash found edge at %d bytes", upto)
            yield from pending.append(chunk[:upto])
            chunk = chunk[upto:]
            yield from pending.flush()
            offset += upto
            last_offset = offset
            first_possible_point, next_rolling_point, next_offset \
              = new_offsets(next_offset, offsetQ)
            ##X("leave scan loop after autoedge")
            break
        if offset > offset0:
          # we advanced, loop again with what remains of the chunk
          ##X("edge found, continue with remainder of chunk")
          continue
        ##X("no edge found, advance offset by scan:%d bytes", scan)
        yield from pending.append(chunk[:scan])
        chunk = chunk[scan:]
        offset += scan
        ##X("redo with remaining chunk")
        continue
      # no edge found, see if next_offset lies in the chunk
      ##X("nothing to scan, see if next_offset:%s is within the chunk", next_offset)
      if next_offset is not None:
        take = next_offset-offset
        if take <= len(chunk):
          ##X("buffer take:%d bytes", take)
          yield from pending.append(chunk[:take])
          chunk = chunk[take:]
          yield from pending.flush()
          offset = next_offset
          last_offset = offset
          first_possible_point, next_rolling_point, next_offset \
            = new_offsets(next_offset, offsetQ)
          ##X("redo with remaining chunk")
          continue
      # next_offset beyond this chunk, buffer chunk
      ##X("next_offset not near, buffer whole chunk")
      yield from pending.append(chunk)
      offset += len(chunk)
      ##X("leave chunk loop, get next chunkQ")
      break
    # end while chunk
  # end for chunkQ
  # yield any left over data
  yield from pending.flush()

if __name__ == '__main__':
  import cs.venti.blockify_tests
  cs.venti.blockify_tests.selftest(sys.argv)
