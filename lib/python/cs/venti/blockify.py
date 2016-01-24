#!/usr/bin/python -tt
#
# Utility routines to parse data streams into Blocks and Block streams
# into IndirectBlocks.
#       - Cameron Simpson <cs@zip.com.au>
#

from itertools import chain
import sys
from cs.logutils import debug, warning, D, X
from .block import Block, IndirectBlock, dump_block

MIN_BLOCKSIZE = 80      # less than this seems silly
MAX_BLOCKSIZE = 16383   # fits in 2 octets BS-encoded

def top_block_for(blocks):
  ''' Return a top Block for a stream of Blocks.
  '''
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
    debug("push new indirect_blocks()")
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

def blockify(data_chunks, vocab=None):
  ''' Collect data strings from the iterable data_chunks and yield data blocks with desirable boundaries.
  '''
  # also accept a bare bytes value
  if isinstance(data_chunks, bytes):
    data_chunks = (data_chunks,)
  if vocab is None:
    vocab = DFLT_VOCAB

  buf = []          # list of data chunks to assemble into a single Block
  buflen = 0        # cumulative length of buf
  hash_value = 0    # rolling hash value
  def bufBlock():
    # prepare and yield new Block then reset the buffer
    # closure FTW!
    B = Block(data=b''.join(buf))
    buf[:] = []
    buflen = 0
    hash_value = 0
    return B

  for data in data_chunks:
    offset = 0
    # loop to yield Blocks, one Block per iteration
    while offset < len(data):
      # note start of this chunk
      start = offset
      is_edge = False
      # phase 1: just compute rolling hash over initial MIN_BLOCKSIZE
      max_offset = min(len(data), offset + (MIN_BLOCKSIZE - buflen))
      while offset < max_offset:
        hash_value, is_edge = test_for_edge(data, offset, hash_value)
        offset += 1
      # phase 2: scan for rolling hash edge or vocab edge up to MAX_BLOCKSIZE
      max_offset = min(len(data), start + (MAX_BLOCKSIZE - buflen))
      while not is_edge and offset < max_offset:
        hash_value, is_edge = test_for_edge(data, offset, hash_value)
        if is_edge:
          break
        else:
          is_edge, edge_offset, subVocab = vocab.test_for_edge(data, offset)
          if is_edge:
            offset += edge_offset
            if offset < start or offset >= len(data):
              raise RuntimeError("bad offset after test_for_vocab_edge")
            if subVocab is not None:
              vocab = subVocab
            break
        offset += 1
      # save this data slice
      buf.append(data[start:offset])
      yield bufBlock()

def test_for_edge(data, offset, hash_value):
  ''' Add the byte at `data[offset]` to the hash_value and test for the boundary condition; return (new_hash_value, is_boundary).
  '''
  b = data[offset]
  hash_value = ( ( ( hash_value & 0x001fffff ) << 7
                 )
               | ( ( b & 0x7f )^( (b & 0x80)>>7 )
                 )
               )
  return hash_value, hash_value % 4093 == 1

class Vocabulary(dict):
  ''' A class for representing match vocabuaries.
  '''

  def __init__(self, vocabDict=None):
    dict.__init__(self)
    self.start_bytes = bytearray()
    if vocabDict is not None:
      for word in vocabDict:
        self.addWord(word, vocabDict[word])

  def addWord(self, word, info):
    ''' Add a word to the vocabulary.
        `word` is the string to match.
        `info` is either an integer offset or a tuple of (offset, Vocabulary).
        The offset is the position within the match string of the boundary.
        If supplied, Vocaulary is a new Vocabulary to use if this word matches.
    '''
    if len(word) <= 0:
      raise ValueError("word too short: %r", word)
    if isinstance(info, int):
      offset = info
      subVocab = None
    else:
      offset, subVocab = info
      subVocab = Vocabulary(subVocab)
    ch1 = word[0]
    if ch1 not in self.start_bytes:
      self.start_bytes.append(ch1)
      self[ch1] = []
    self[ch1].append( (word, offset, subVocab) )

  def test_for_edge(self, bs, offset):
    ''' Probe for a vocabulary word in `bs` at `offset`. Return (is_edge, edge_offset, subVocab).
    '''
    is_edge = False
    b = bs[offset]
    wordlist = self.get(b)
    if wordlist is not None:
      for word, edge_offset, subVocab in wordlist:
        if bs.startswith(word, offset):
          return True, edge_offset, subVocab
    return False, None, None

  # TODO: OBSOLETE
  def match(self, bs, pos, endpos):
    ''' Locate the earliest occurence in the bytes 'bs' of a vocabuary word.
        Return (edgepos, word, offset, subVocab) for a match or None on no match.
        `edgepos` is the boundary position.
        `word` will be present at edgepos-offset.
        `subVocab` is the new vocabulary to use from this point, or None
        for no change.
    '''
    assert pos >= 0 and pos < len(bs)
    assert endpos > pos and endpos <= len(bs)
    matched = None
    for ch in self.start_bytes:
      wordlist = self[ch]
      findpos = pos
      while findpos < endpos:
        cpos = bs.find(ch, findpos, endpos)
        if cpos < 0:
          break
        findpos = cpos + 1      # start here on next find
        for word, offset, subVocab in wordlist:
          if bs.startswith(word, cpos):
            edgepos = cpos + offset
            matched = (edgepos, word, offset, subVocab)
            endpos = edgepos
            break
    return matched

# TODO: reform as list of (bytes, offset, sublist).
DFLT_VOCAB = Vocabulary({
                b"\ndef ": 1,         # python top level function
                b"\n  def ": 1,       # python class method, 2 space indent
                b"\n    def ": 1,     # python class method, 4 space indent
                b"\nclass ": 1,       # python top level class
                b"\npackage ": 1,     # perl package
                b"\n}\n\n": 3,        # C-ish function ending
                b"\n};\n\n": 3,       # JavaScript method assignment ending
                b"\nFrom ":           # UNIX mbox separator
                  [ 1,
                    { b"\n\n--": 2,   # MIME separators
                      b"\r\n\r\n--": 4,
                      b"\nFrom ": 1,
                    },
                  ],
              })

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

if __name__ == '__main__':
  import cs.venti.blockify_tests
  cs.venti.blockify_tests.selftest(sys.argv)
