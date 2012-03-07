#!/usr/bin/python -tt
#
# Utility routines to parse data streams into Blocks and Block streams
# into IndirectBlocks.
#       - Cameron Simpson <cs@zip.com.au>
#

from itertools import chain
import sys
from threading import Thread
from cs.logutils import debug
from cs.threads import IterableQueue
from cs.misc import eachOf
from cs.venti import defaults
from .block import Block, IndirectBlock

MIN_BLOCKSIZE = 80      # less than this seems silly
MAX_BLOCKSIZE = 16383   # fits in 2 octets BS-encoded

class Blockifier(object):
  ''' A Blockifier accepts data or Blocks and stores them sequentially.
      Data chunks are presumed to be as desired, and are not reblocked;
      each is stored directly.
      The .close() method returns the top Block representing the
      stored sequence.
  '''

  def __init__(self):
    self.topBlock = None
    self.S = defaults.S
    self.Q = IterableQueue()
    self.T = Thread(target=self._storeBlocks)
    self.T.start()

  def _storeBlocks(self):
    with self.S:
      self.topBlock = topIndirectBlock(self.Q)

  def add(self, data):
    self.Q.put(Block(data=data))
    return self.S.add(data)

  def addBlock(self, B):
    self.Q.put(B)
  
  def close(self):
    self.Q.close()
    self.T.join()
    self.T = None
    self.Q = None
    return self.topBlock
 
def topIndirectBlock(blockSource):
  ''' Return a top Block for a stream of Blocks.
  '''
  blockSource = fullIndirectBlocks(blockSource)

  # Fetch the first two indirect blocks from the generator.
  # If there is none, return a single empty direct block.
  # If there is just one, return it.
  # Otherwise there are two (implicitly: or more):
  # replace the blockSource with another level of fullIndirectBlocks()
  # reading from the two fetched blocks and the tail of the surrent
  # blockSource then lather, rinse, repeat.
  #    
  while True:
    try:
      topblock = blockSource.next()
    except StopIteration:
      # no blocks - return the empty block - no data
      return Block(data="")

    # we have a full IndirectBlock
    # if there are more, replace our blockSource with
    #   fullIndirectBlocks(topblock + nexttopblock + blockSource)
    try:
      nexttopblock = blockSource.next()
    except StopIteration:
      # just one IndirectBlock - we're done
      return topblock

    # add a layer of indirection and repeat
    debug("push new fullIndirectBlocks()")
    blockSource = fullIndirectBlocks(eachOf(([topblock,
                                              nexttopblock
                                             ], blockSource)))

  assert False, "not reached"

def blockFromString(s):
  return topIndirectBlock(blocksOf([s]))

def blockFromFile(fp, rsize=None, matchBlocks=None):
  return topIndirectBlock(fileBlocks(fp, rsize=rsize, matchBlocks=matchBlocks))

def fileBlocks(fp, rsize=None, matchBlocks=None):
  ''' Yield Blocks containing the content of this file.
      If rsize is not None, specifies the preferred read() size.
      If matchBlocks is not None, specifies a source of Blocks for comparison.
      This lets us store a file with reference to its previous version
      without playing the "look for edges" game.
  '''
  data = None
  if matchBlocks:
    # fetch Blocks from the comparison file until a mismatch
    for B in matchBlocks:
      blen = len(B)
      if blen == 0:
        continue
      data = fp.read(blen)
      if len(data) == 0:
        return
      # compare hashcodes to avoid fetching data for B if we have its hash
      if defaults.S.hash(data) == B.hashcode:
        debug("MATCH %d bytes", len(data))
        yield B
        data = None
        continue
      break

  # blockify the remaining data
  datachunks = filedata(fp, rsize=rsize)
  if data:
    datachunks = chain([data], datachunks)
  for B in blocksOf(datachunks):
    yield B

def filedata(fp, rsize=None):
  ''' A generator to yield chunks of data from a file.
      These chunks don't need to be preferred-edge aligned;
      blocksOf() does that.
  '''
  if rsize is None:
    rsize = 8192
  else:
    assert rsize > 0
  while True:
    s = fp.read(rsize)
    if len(s) == 0:
      break
    yield s

def fullIndirectBlocks(blockSource):
  ''' A generator that yields full IndirectBlocks from an iterable
      source of Blocks, except for the last Block which need not
      necessarily be bundled into an IndirectBlock.
  '''
  S = defaults.S
  iblock = IndirectBlock()
  # how many subblock refs will fit in a block: flags(1)+span(2)+hash
  max_subblocks = int(MAX_BLOCKSIZE/(3+S.hashclass.hashlen))
  for block in blockSource:
    if len(iblock.subblocks()) >= max_subblocks:
      # overflow
      yield iblock
      iblock = IndirectBlock()
    block.store(discard=True)
    iblock.append(block)

  # handle the termination case
  if len(iblock) > 0:
    if len(iblock) == 1:
      # one block unyielded - don't bother wrapping into an iblock
      block = iblock[0]
    else:
      block = iblock
    yield block

def blocksOf(dataSource, vocab=None):
  ''' Collect data strings from the iterable dataSource
      and yield data blocks with desirable boundaries.
  '''
  if vocab is None:
    vocab = DFLT_VOCAB

  buf = []      # list of strings-to-collate-into-a-block
  buflen = 0    # cumulative length of buf

  # invariant: no block edge has been seen in buf based on the vocabulary
  #            the rolling hash has not been used yet
  for data in dataSource:
    while len(data) > 0:
      # if buflen < MIN_BLOCKSIZE pad with stuff from data
      skip = MIN_BLOCKSIZE - buflen
      if skip > 0:
        if skip > len(data):
          skip = len(data)
        left = data[:skip]
        data = data[skip:]
        buf.append(left)
        buflen += len(left)
        continue

      # we don't like to make blocks bigger than MAX_BLOCKSIZE
      probe_len = MAX_BLOCKSIZE - buflen
      assert probe_len > 0, \
                "probe_len <= 0 (%d), MAX_BLOCKSIZE=%d, len(buf)=%d" \
                % (probe_len, MAX_BLOCKSIZE, len(buf))
      # don't try to look beyond the end of the data either
      probe_len = min(probe_len, len(data))

      # look for a vocabulary word
      m = vocab.match(data, 0, probe_len)
      if m:
        edgepos, _, _, subVocab = m
        if subVocab:
          vocab = subVocab
        buf.append(data[:edgepos])
        data = data[edgepos:]
        yield Block("".join(buf))
        buf = []
        buflen = 0
        continue

      # no vocabulary words seen - append data to buf
      buf.append(data)
      buflen += len(data)
      data = ''

      # if buf gets too big, scan it with the rolling hash
      # we may have to rescan after finding an edge
      if buflen >= MAX_BLOCKSIZE:
        data2 = "".join(buf)
        RH = RollingHash()
        while len(data2) >= MAX_BLOCKSIZE:
          edgepos = RH.findEdge(data2, len(data2))
          if edgepos < 0:
            edgepos = MAX_BLOCKSIZE
          yield Block(data2[:MAX_BLOCKSIZE])
          data2 = data2[MAX_BLOCKSIZE:]
          RH.reset()
        buf = [data2]
        buflen = len(data2)

  # no more data - yield remaining buffer
  if buflen > 0:
    yield Block("".join(buf))

class RollingHash(object):
  ''' Compute a rolling hash over 4 bytes of data.
      TODO: this is a lousy algorithm!
  '''
  def __init__(self):
    self.n = None
    self.reset()

  def reset(self):
    self.n = 0

  def value(self):
    return self.n

  def findEdge(self, data, probe_len):
    ''' Add characters from data to the rolling hash until probe_len characters
        are accumulated or the magic hash code is encountered.
        Return the offset where the match was found, or -1 if no match.
        POST: -1: probe_len characters added to the hash.
              >=0: offset characters added to the hash
    '''
    assert probe_len > 0
    probe_len = min(probe_len, len(data))
    n = self.n
    for i in range(probe_len):
      o = ord(data[i])
      n = ( ( ( n & 0x001fffff ) << 7
            )
          | ( ( o & 0x7f )^( (o & 0x80)>>7 )
            )
          )
      if n % 4093 == 1:
        debug("edge found, returning (hashcode=%d, offset=%d)", self.value(), i+1)
        self.n = n
        return i+1
    self.n = n
    debug("no edge found, hash now %d, returning (None, %d)", self.value(), probe_len)
    return -1

class Vocabulary(dict):
  ''' A class for representing match vocabuaries.
  '''

  def __init__(self, vocabDict=None):
    dict.__init__(self)
    self.__startChars = ""
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
    assert len(word) > 0
    if type(info) is int:
      offset = info
      subVocab = None
    else:
      offset, subVocab = info
      subVocab = Vocabulary(subVocab)
    ch1 = word[0]
    if ch1 not in self.__startChars:
      self.__startChars += ch1
      self[ch1] = []
    self[ch1].append( (word, offset, subVocab) )

  def match(self, s, pos, endpos):
    ''' Locate the earliest occurence in the string 's' of a vocabuary word.
        Return (edgepos, word, offset, subVocab) for a match or None on no match.
        `edgepos` is the boundary position.
        `word` will be present at edgepos-offset.
        `subVocab` is the new vocabulary to use from this point, or None
        for no change.
    '''
    assert pos >= 0 and pos < len(s)
    assert endpos > pos and endpos <= len(s)
    matched = None
    for ch in self.__startChars:
      wordlist = self[ch]
      findpos = pos
      while findpos < endpos:
        cpos = s.find(ch, findpos, endpos)
        if cpos < 0:
          break
        findpos = cpos + 1      # start here on next find
        for word, offset, subVocab in wordlist:
          if s.startswith(word, cpos):
            edgepos = cpos + offset
            matched = (edgepos, word, offset, subVocab)
            endpos = edgepos
            break
    return matched

# TODO: reform as list of (str, offset, sublist).
DFLT_VOCAB = Vocabulary({
                "\ndef ": 1,          # python top level function
                "\n  def ": 1,        # python class method, 2 space indent
                "\n    def ": 1,      # python class method, 4 space indent
                "\nclass ": 1,        # python top level class
                "\npackage ": 1,      # perl package
                "\n}\n\n": 3,         # C-ish function ending
                "\n};\n\n": 3,        # JavaScript method assignment ending
                "\nFrom ":            # UNIX mbox separator
                  [ 1,
                    { "\n\n--": 2,    # MIME separators
                      "\r\n\r\n--": 4,
                      "\nFrom ": 1,
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
      s = fp.read(4-len(chunk))
      if len(s) == 0:
        break
      chunk += s
    if len(chunk) == 0:
      return
    assert len(chunk) >= 4, "short data at end of fp"

    if chunk.startswith("TAG"):
      frame_len = 128
    elif chunk.startswith("ID3"):
      print >>sys.stderr, "ID3"
      # TODO: suck up a few more bytes and compute length
      return
    else:
      hdr_bytes = map(ord, chunk[:4])
      ##print >>sys.stderr, hdr_bytes

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

    ##print >>sys.stderr, "vid =", audio_vid, "layer =", layer, "has_crc =", has_crc, "frame_len =", frame_len, "bitrate =", bitrate, "samplingrate =", samplingrate, "padding =", padding
    while len(chunk) < frame_len:
      s = fp.read(frame_len - len(chunk))
      if len(s) == 0:
        break
      chunk += s
    assert len(chunk) >= frame_len

    yield chunk[:frame_len]
    chunk = chunk[frame_len:]

if __name__ == '__main__':
  import cs.venti.blockify_tests
  cs.venti.blockify_tests.selftest(sys.argv)
