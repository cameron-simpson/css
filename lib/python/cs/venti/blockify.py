#!/usr/bin/python -tt
#
# Utility routines to parse data streams into Blocks and Block streams
# into IndirectBlocks.
#       - Cameron Simpson <cs@zip.com.au>
#

from itertools import chain
import sys
from struct import unpack_from
from threading import Thread
import unittest
from cs.logutils import debug, D
from cs.threads import IterableQueue
from cs.misc import D, eachOf
from cs.lex import unctrl
from cs.venti import defaults
from cs.venti.block import Block, IndirectBlock

MIN_BLOCKSIZE=80                                # less than this seems silly
MAX_BLOCKSIZE=16383                             # fits in 2 octets BS-encoded

class Blockifier(object):
  ''' A Blockifier accepts data or Blocks and stores them sequentially.
      Its .close() method returns the top Block representing the
      stored sequence.
  '''

  def __init__(self):
    self.S = defaults.S
    self.Q = IterableQueue()
    self.T = Thread(target=self._storeBlocks)
    self.T.start()

  def _storeBlocks(self):
    with self.S:
      self.topBlock = topIndirectBlock(self.Q)

  def add(self, data):
    self.Q.put(Block(data=data))
    B = self.S.add(data)

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
    D("push new fullIndirectBlocks()")
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
      if defaults.S.hashcode(data) == B.hashcode():
        D("MATCH %d bytes" % (len(data),))
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
    rsize=8192
  else:
    assert rsize > 0
  while True:
    s=fp.read(rsize)
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
      iblock=IndirectBlock()
    block.store(discard=True)
    iblock.append(block)

  # handle the termination case
  if len(iblock) > 0:
    if len(iblock) == 1:
      # one block unyielded - don't bother wrapping into an iblock
      block=iblock[0]
    else:
      block=iblock
    yield block

def blocksOf(dataSource, vocab=None):
  ''' Collect data strings from the iterable dataSource
      and yield data blocks with desirable boundaries.
  '''
  if vocab is None:
    global DFLT_VOCAB
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
        left = data[:skip]; data = data[skip:]
        buf.append(left); buflen += len(left)
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
        edgepos, word, offset, subVocab = m
        if subVocab:
          vocab = subVocab
        buf.append(data[:edgepos])
        data = data[edgepos:]
        yield Block("".join(buf))
        buf = []
        buflen = 0
        continue

      # no vocabulary words seen - append data to buf
      buf.append(data); buflen += len(data)
      data = ''

      # if buf gets too big, scan it with the rolling hash
      # we may have to rescan after finding an edge
      while buflen >= MAX_BLOCKSIZE:
        buf2 = []
        RH = RollingHash()
        for b in buf:
          while len(b):
            edgepos = RH.findEdge(b, len(b))
            if edgepos >= 0:
              buf2.append(b[:edgepos])
              yield Block("".join(buf2))
              buf2 = []
              b = b[edgepos:]
              RH.reset()
            else:
              buf2.append(b)
              break
        buf = buf2
        buflen = sum( len(b) for b in buf )

  # no more data - yield remaining buffer
  if buflen > 0:
    yield Block("".join(buf))

class RollingHash:
  ''' Compute a rolling hash over 4 bytes of data.
      TODO: this is a lousy algorithm!
  '''
  def __init__(self):
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
    D("H")
    assert probe_len > 0
    probe_len=min(probe_len,len(data))
    n=self.n
    for i in range(probe_len):
      self.addcode(ord(data[i]))
      if self.n%4093 == 1:
        debug("edge found, returning (hashcode=%d, offset=%d)", self.value(), i+1)
        return i+1
    debug("no edge found, hash now %d, returning (None, %d)", self.value(), probe_len)
    return -1

  def addcode(self,oc):
    self.n=( ( ( self.n&0x001fffff ) << 7
             )
           | ( ( oc&0x7f )^( (oc&0x80)>>7 )
             )
           )

  def addString(self,s):
    n=self.n
    for c in s:
      oc=ord(c)
      n=( ( ( self.n&0x001fffff ) << 7
          )
        | ( ( oc&0x7f )^( (oc&0x80)>>7 )
          )
        )
    self.n=n

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

class TestAll(unittest.TestCase):

  def setUp(self):
    self.fp = open(__file__)

  def tearDown(self):
    self.fp.close()

  def test00blockifyAndRetrieve(self):
    data = self.fp.read()
    blocks = list(blocksOf([data]))
    data2 = "".join( b.data() for b in blocks )
    self.assertEqual(len(data), len(data2), "data mismatch: len(data)=%d, len(data2)=%d" % (len(data), len(data2)))
    self.assertEqual(data, data2, "data mismatch: data and data2 same length but contents differ")
    ##for b in blocks: print "[", b.data(), "]"

  def test01blockifier(self):
    from cs.venti.cache import MemCacheStore
    with MemCacheStore():
      BL = Blockifier()
      alldata = []
      for data in self.fp:
        BL.add(data)
        alldata.append(data)
      top = BL.close()
      alldata = ''.join(alldata)
      stored = top[:]
      self.assertEqual( ''.join(alldata), top[:] )

if __name__ == '__main__':
  unittest.main()
