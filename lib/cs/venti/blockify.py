#!/usr/bin/python -tt
#
# Utility routines for blocks and BlockRefs.
#       - Cameron Simpson <cs@zip.com.au>
#

import sys
from struct import unpack_from
from threading import Thread
from cs.venti.blocks import BlockList, BlockRef
from cs.venti.hash import MIN_BLOCKSIZE, MAX_BLOCKSIZE, MAX_SUBBLOCKS
from cs.threads import IterableQueue
from cs.misc import isdebug, debug, D, eachOf
from cs.lex import unctrl
import __main__

def blocksOfFP(fp, rsize=None):
  ''' A generator that reads data from a file and yields blocks.
  '''
  return blocksOf(filedata(fp,rsize))

def filedata(fp,rsize=None):
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

def blockrefsOf(blockSource,S=None):
  ''' A generator that yields direct BlockRefs from a data source.
  '''
  if S is None:
    S=__main__.S
  for block in blockSource:
    yield BlockRef(S.store(block),False,len(block))

def fullBlockRefsOf(blockrefs,S=None):
  ''' A generator that yields full indirect BlockRefs from an iterable
      source of blockrefs, except for the last blockref which need not
      necessarily be bundled into an iblock.
  '''
  if S is None:
    S=__main__.S
  BL=BlockList()
  for bref in blockrefs:
    if len(BL) >= MAX_SUBBLOCKS:
      # overflow
      yield BL
      BL=BlockList()
    BL.append(bref)
  if len(BL) == 0:
    # never received any blockrefs
    h=S.store("")
    bref=BlockRef(S.store(""),False,0)
  elif len(BL) == 1:
    # one block unyielded - don't bother wrapping into an iblock
    bref=BL[0]
  else:
    # wrap into iblock for return
    bref=BlockRef(S.store(BL.encode()),True,BL.span())
  yield bref
 
def topBlockRef(blockrefs,S=None):
  ''' Return a top BlockRef for a stream of BlockRefs.
  '''
  blockrefs=fullBlockRefsOf(blockrefs,S=S)
  while True:
    bref=blockrefs.next()
    try:
      bref2=blockrefs.next()
    except StopIteration:
      # just one blockref - return it
      break
    # reapply to the next layer of indirection
    blockrefs=fullBlockRefsOf(eachOf(([bref,bref2],blockrefs)))
  return bref

def topBlockRefFP(fp,rsize=None,S=None):
  ''' Return a top BlockRef for the data in a file.
  '''
  return topBlockRef(blockrefsOf(blocksOfFP(fp,rsize),S=S),S=S)

def topBlockRefString(s,S=None):
  return topBlockRef(blockrefsOf(blocksOf([s]),S=S),S=S)

class strlist:
  def __init__(self):
    self._flushlen=0
    self._reset()
  def _reset(self):
    self.buf=[]
    self.len=0
  def take(self,s,slen,RH=None):
    assert slen > 0 and slen <= len(s), "slen=%d, len(s)=%d" % (slen, len(s))
    right=buffer(s,slen)
    left=buffer(s,0,slen)
    if RH is not None:
      RH.addString(right)
    assert len(left) == slen
    self.buf.append(left)
    self.len+=slen
    return right
  def flush(self,Q,RH):
    s=str(self)
    if len(s) == MAX_BLOCKSIZE:
      D("Y")
    else:
      D("Y(%d)", len(s))
    self._flushlen+=len(s)
    Q.put(s)
    self._reset()
    RH.reset()
  def __len__(self):
    return self.len
  def __str__(self):
    return "".join(str(b) for b in self.buf)

def cut(bufable,pos):
  return buffer(bufable,0,pos), buffer(bufable,pos)

def blocksOf(dataSource,vocab=None):
  ''' Return an iterator that yields blocks from an iterable dataSource.
  '''
  IQ=IterableQueue(1)
  T=Thread(target=blockify,args=(dataSource,vocab,IQ))
  T.setDaemon(True)
  T.start()
  return IQ

def blockify(dataSource,vocab,Q):
  ''' Collect data strings from the iterable dataSource
      and put blocks onto the output Queue 'Q'.
      This is usually run as a daemon thread for blockOf().
  '''
  if vocab is None:
    global DFLT_VOCAB
    vocab=DFLT_VOCAB

  datalen=0
  buf=strlist()
  RH=RollingHash()
  # invariant: all the bytes in buf[]
  # have been fed to the rolling hash
  for data in dataSource:
    D("D")
    datalen+=len(data)
    while len(data) > 0:
      # pad buffer if less than minimum block size
      ##D("d(%d)", len(data))
      skip=max(MIN_BLOCKSIZE-len(buf),0)
      if skip > 0:
        if skip >= len(data):
          skip = len(data)
        data = buf.take(data, skip, RH)
        continue

      # we don't like to make blocks bigger than MAX_BLOCKSIZE
      maxlen=MAX_BLOCKSIZE-len(buf)
      assert maxlen > 0, \
                "maxlen <= 0 (%d), MAX_BLOCKSIZE=%d, len(buf)=%d" \
                % (maxlen, MAX_BLOCKSIZE, len(buf))
      # don't try to look beyond the end of the data either
      maxlen=min(maxlen,len(data))

      # look for a vocabulary word
      data=str(data)
      word, woffset = vocab.findWord(data,0,maxlen)
      if word is not None:
        # word match found
        vword, voffset, vsubvocab = vocab[word]
        if vsubvocab is not None:
          # update for new vocabuary
          vocab=vsubvocab
        # put the desired part of the data into the buffer
        # and flush the buffer
        data = buf.take(data, woffset+voffset)
        D("W")
        buf.flush(Q,RH)
        continue

      # no vocabulary word - look for the magic hash value
      off = RH.findEdge(data,maxlen)
      assert off == -1 or off > 0, "findEdge()=%d" % off
      # POST: if off == -1, maxlen bytes added to RH
      #       otherwise, off bytes added to RH
      if off > 0:
        data = buf.take(data, off)
        ##D("off=%d", off)
        buf.flush(Q,RH)
        continue

      data = buf.take(data, maxlen)
      assert len(buf) <= MAX_BLOCKSIZE
      if len(buf) == MAX_BLOCKSIZE:
        D("M")
        buf.flush(Q,RH)

  # no more data - flush remaining buffer and close output
  if len(buf) > 0:
    buf.flush(Q,RH)
  assert buf._flushlen == datalen, "buf._flushlen(%d) != datalen(%d)" % (buf._flushlen,datalen)
  Q.close()

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

  def findEdge(self,data,maxlen):       ## hashcodes):
    ''' Add characters from data to the rolling hash until maxlen characters
        are accumulated or the magic hash code is encountered.
        Return the offset where the match was found, or -1 if no match.
        POST: -1: maxlen characters added to the hash.
              >=0: offset characters added to the hash
    '''
    D("H")
    assert maxlen > 0
    maxlen=min(maxlen,len(data))
    n=self.n
    for i in range(maxlen):
      self.addcode(ord(data[i]))
      if self.n%4093 == 1:
        ##debug("edge found, returning (hashcode=%d, offset=%d)" % (self.value,i+1))
        D("(self.n=%d:i+1=%d)", self.n, i+1)
        return i+1
    ##debug("no edge found, hash now %d, returning (None, %d)" % (self.value(),maxlen))
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
