#!/usr/bin/python -tt
#
# Utility routines to parse data streams into Blocks and Block streams
# into IndirectBlocks.
#       - Cameron Simpson <cs@zip.com.au>
#

import sys
from struct import unpack_from
from threading import Thread
from cs.threads import IterableQueue
from cs.misc import isdebug, debug, D, eachOf
from cs.lex import unctrl
from cs.venti import defaults
from cs.venti.block import Block, IndirectBlock

MIN_BLOCKSIZE=80                                # less than this seems silly
MAX_BLOCKSIZE=16383                             # fits in 2 octets BS-encoded
 
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
      topblock=blockSource.next()
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
    print >>sys.stderr, "push new fullIndirectBlocks()"
    blockSource = fullIndirectBlocks(eachOf(([topblock,
                                              nexttopblock
                                             ], blockSource)))

  assert False, "not reached"

def blockFromString(s):
  return topIndirectBlock(blocksOf([s]))

def blockFromFile(fp):
  return topIndirectBlock(blocksOf(filedata(fp)))

def filedata(fp, rsize=None):
  ''' A generator to yield chunks of data from a file.
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

  buf = []      # list of strings-to-collate-into-a block
  buflen = 0    # cumulative length of buf
  RH=RollingHash() # rolling hash of contents of buf

  # invariant: all the bytes in buf[]
  # have been fed to the rolling hash
  for data in dataSource:
    ##print >>sys.stderr, "blockOf(data = %d bytes), buflen=%d" % (len(data),buflen)
    while len(data) > 0:
      # if buflen < MIN_BLOCKSIZE pad with stuff from data
      skip = MIN_BLOCKSIZE - buflen
      if skip > 0:
        if skip > len(data):
          skip = len(data)
        left = data[:skip]; data = data[skip:]
        buf.append(left); buflen += len(left)
        RH.addString(left)
        continue

      # we don't like to make blocks bigger than MAX_BLOCKSIZE
      probe_len = MAX_BLOCKSIZE - buflen
      assert probe_len > 0, \
                "probe_len <= 0 (%d), MAX_BLOCKSIZE=%d, len(buf)=%d" \
                % (probe_len, MAX_BLOCKSIZE, len(buf))
      # don't try to look beyond the end of the data either
      probe_len = min(probe_len, len(data))

      # look for a vocabulary word
      word,  woffset = vocab.findWord(data, 0, probe_len)
      if word is not None:
        # word match found
        vword, voffset, vsubvocab = vocab[word]
        if vsubvocab is not None:
          # change vocabulary
          vocab = vsubvocab
        # put the desired part of the data into the buffer
        # and flush the buffer
        cutoff = woffset + voffset      # start of word plus offset to boundary
        left = data[:cutoff]; data = data[cutoff:]
        buf.append(left)
        yield Block("".join(buf))
        buf = []; buflen = 0
        RH.reset()
        continue

      # no vocabulary word - look for the magic hash value
      cutoff = RH.findEdge(data,probe_len)
      assert cutoff == -1 or cutoff > 0, "findEdge()=%d" % (cutoff,)
      # POST: if cutoff == -1, probe_len bytes added to RH
      #       otherwise, cutoff bytes added to RH
      if cutoff > 0:
        left = data[:cutoff]; data = data[cutoff:]
        buf.append(left)
        yield Block("".join(buf))
        buf = []; buflen = 0
        RH.reset()
        continue

      assert probe_len > 0
      left = data[:probe_len]; data = data[probe_len:]
      buf.append(left); buflen += len(left)
      RH.addString(left)

      assert buflen <= MAX_BLOCKSIZE
      if buflen == MAX_BLOCKSIZE:
        # full block - release it
        yield Block("".join(buf))
        buf = []; buflen = 0
        RH.reset()

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
    self.n=0

  def value(self):
    return self.n

  def findEdge(self,data,probe_len):       ## hashcodes):
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
        ##debug("edge found, returning (hashcode=%d, offset=%d)" % (self.value,i+1))
        D("(self.n=%d:i+1=%d)", self.n, i+1)
        return i+1
    ##debug("no edge found, hash now %d, returning (None, %d)" % (self.value(),probe_len))
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
  def __init__(self,vocabDict=None):
    dict.__init__(self)
    self.__startChars={}
    self.__maxWordLen=0
    if vocabDict is not None:
      for word in vocabDict:
        self.addWord(word,vocabDict[word])

  def addWord(self,word,info):
    assert len(word) > 0
    assert word not in self, "word \"%s\" already present" % unctrl(word)
    if type(info) is int:
      offset=info
      subVocab=None
    else:
      offset, subVocab = info
      subVocab=Vocabulary(subVocab)
    self.__startChars.setdefault(word[0],[]).append(word)
    self.__maxWordLen=max(self.__maxWordLen,len(word))
    self[word]=(word,offset,subVocab)
    ##RH=RollingHash()
    ##RH.addString(word)
    ##rh=RH.value()
    ##self.setdefault(rh,[]).append((word,offset,subVocab))

  def findWord(self,s,offset,maxoffset):
    ''' Locate the earliest occurence in the string 's' of a vocabuary word.
        Return (word, offset) being the matched word and its offset
        respectively. Return (None, None) on no match.
    '''
    assert len(s) > 0
    assert offset >= 0
    assert maxoffset > offset
    assert maxoffset <= len(s)
    fword=None
    foff=None
    for ch in self.__startChars.keys():
      words=self.__startChars[ch]
      if fword is None:
        roffset=maxoffset
      else:
        assert foff < maxoffset
        roffset=foff
      fpos=s.find(ch,offset,roffset)
      while fpos > 0 and (fword is None or fpos < foff):
        for w in words:
          if s[fpos:fpos+len(w)] == w:
            foff=fpos
            fword=w
            roffset=foff
            break
        offset=fpos+1
        fpos=s.find(ch,offset,roffset)
    return fword, foff

# TODO: reform as list of (str, offset, sublist).
DFLT_VOCAB=Vocabulary({
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
