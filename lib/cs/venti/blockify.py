#!/usr/bin/python -tt

import sys
from struct import unpack_from
from cs.venti.blocks import BlockList, BlockRef
from cs.venti.hash import MIN_BLOCKSIZE, MAX_BLOCKSIZE, MAX_SUBBLOCKS
from cs.misc import isdebug, debug
from cs.lex import unctrl
import __main__

def blocksOfFP(fp,rsize=None):
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
    blockrefs=eachOf([bref,bref2],blockrefs)
  return bref

def topBlockRefFP(fp,rsize=None,S=None):
  ''' Return a top BlockRef for the data in a file.
  '''
  return topBlockRef(blockrefsOf(blocksOfFP(fp,rsize),S=S),S=S)

def topBlockRefString(s,S=None):
  return topBlockRef(blockrefsOf(blocksOf([s]),S=S),S=S)

class strlist:
  def __init__(self):
    self.reset()
  def reset(self):
    self.buf=[]
    self.len=0
  def take(self,s,slen):
    assert slen > 0 and slen <= len(s), "slen=%d, len(s)=%d" % (slen, len(s))
    right=buffer(s,slen)
    self.buf.append(buffer(s,0,slen))
    self.len+=slen
    return right
  def __len__(self):
    return self.len
  def __str__(self):
    return "".join(str(b) for b in self.buf)

def cut(bufable,pos):
  return buffer(bufable,0,pos), buffer(bufable,pos)

def blocksOf(dataSource,vocab=None):
  ''' A generator that reads data from an iterable dataSource
      and yields it as blocks at appropriate edge points.
  '''
  if vocab is None:
    global DFLT_VOCAB
    vocab=DFLT_VOCAB
  vhs=vocab.keys()      # hashes liked by the vocab

  datalen=0
  yieldlen=0

  buf=strlist()
  RH=RollingHash()
  # invariant: all the bytes in buf[]
  # have been fed to the rolling hash
  for data in dataSource:
    datalen+=len(data)
    ##sys.stderr.write("D")
    while len(data) > 0:
      skip=MIN_BLOCKSIZE-len(buf)
      if skip > 0:
        if skip >= len(data):
          RH.addString(data)
          data=buf.take(data, len(data))
          continue
        left, data = cut(data,skip)
        RH.addString(left)
        buf.take(left, len(left))

      maxlen=MAX_BLOCKSIZE-len(buf)
      ##debug("len(buf)=%d, MAX_BLOCKSIZE=%d, maxlen=%d" % (len(buf), MAX_BLOCKSIZE,maxlen))
      assert maxlen > 0, \
                "maxlen <= 0 (%d), MAX_BLOCKSIZE=%d, len(buf)=%d" \
                % (maxlen, MAX_BLOCKSIZE, len(buf))
      h, off = RH.findEdge(data,maxlen,vhs)
      assert off > 0 and off <= len(data), \
             "off=%d, len(data)=%d" % (off, len(data))
      if h is None:
        # no edge found
        ##debug("h is None: off=%d, maxlen=%d, len(data)=%d, len(buf)=%d" % (off,maxlen,len(data),len(buf)))
        assert off == min(maxlen,len(data))
        data = buf.take(data, off)
        ##debug("h is None: after take(data,off=%d): len(data)=%d, len(buf)=%d" % (off,len(data),len(buf)))
        if len(buf) == MAX_BLOCKSIZE:
          ##sys.stderr.write("Y")
          y=str(buf)
          yieldlen+=len(y)
          yield y
          buf.reset()
        else:
          assert len(buf) < MAX_BLOCKSIZE, \
                 "off=%d,len(buf)=%d, MAX_BLOCKSIZE=%d, maxlen=%d" % (off,len(buf), MAX_BLOCKSIZE,maxlen)
        continue

      if h in vocab:
        # findEdge returns a hash/offset at the end of the vocabulary word.
        # Check that it is a vocab word, and figure out the desired cut point.
        # Because the hash is computed up to the offset, prefill the new buf
        # with the text from the cut point to the hash/offset.
        ds=str(data[:off])
        for word, woffset, subVocab in vocab[h]:
          if ds.endswith(word):
            # update for new vocabuary, if any
            if subVocab is not None:
              vocab=subVocab
              vhs=vocab.keys()
            # put the desired part of the data into the buffer
            # and yield the buffer
            data = buf.take(data, off-len(word)+woffset)
            ##sys.stderr.write("Y")
            y=str(buf)
            yieldlen+=len(y)
            yield y
            buf.reset()
            # put the undesired tail of the word into the buffer
            data = buf.take(data, len(word)-woffset)
            off=None
            break
        if off is None:
          continue

      assert off <= len(data), "off=%d, len(data)=%d" % (off,len(data))
      data = buf.take(data, off)
      if len(buf) >= MAX_BLOCKSIZE:
        ##sys.stderr.write("Y")
        y=str(buf)
        yieldlen+=len(y)
        yield y
        buf.reset()
      elif h == HASH_MAGIC:
        ##sys.stderr.write("Y")
        y=str(buf)
        yieldlen+=len(y)
        yield y
        buf.reset()

    assert len(data) == 0

  if len(buf) > 0:
    ##sys.stderr.write("Y")
    y=str(buf)
    yieldlen+=len(y)
    yield y

  assert yieldlen == datalen, "yieldlen(%d) != datalen(%d)" % (yieldlen,datalen)

HASH_MAGIC=511
class RollingHash:
  ''' Compute a rolling hash over 4 bytes of data.
      TODO: this is a lousy algorithm!
  '''
  def __init__(self):
    self.reset()

  def reset(self):
    self.buf=[0,0,0,0]
    self.n=0

  def value(self):
    buf = self.buf
    n = self.n
    return unpack_from("<L","".join(buf[n:]+buf[:n]))[0]

  def findEdge(self,data,maxlen,hashcodes):
    ''' Add characters from data to the rolling hash until maxlen characters
        are accumulated or the hash value matches HASH_MAGIC or a value in
        hashcodes. Normally hashcodes will be a list of hashcodes derived
        from a vocabulary of strings used to identify match points.
        The function returns a tuple of (hashcode, offset).
        If maxlen or the end of the data sequence is reached before
        a matching hashcode, the return hashcode value will be None.
    '''
    global HASH_MAGIC
    assert maxlen > 0
    maxlen=min(maxlen,len(data))
    n=self.n
    for i in range(maxlen):
      self.buf[n]=data[i]
      self.n=n=(n+1)%4
      v=self.value()
      if v%16381 == HASH_MAGIC or v in hashcodes:
        ##debug("edge found, returning (hashcode=%d, offset=%d)" % (self.value,i+1))
        return v, i+1
    ##debug("no edge found, hash now %d, returning (None, %d)" % (self.value(),maxlen))
    return None, maxlen

  def addString(self,s):
    n=self.n
    for c in s:
      self.buf[n]=c
      n=(n+1)%4
    self.n=n

class Vocabulary(dict):
  ''' A class for representing match vocabuaries.
  '''
  def __init__(self,vocabDict=None):
    dict.__init__(self)
    if vocabDict is not None:
      for word in vocabDict:
        self.addWord(word,vocabDict[word])

  def addWord(self,word,info):
    if type(info) is int:
      offset=info
      subVocab=None
    else:
      offset, subVocab = info
      subVocab=Vocabulary(subVocab)
    RH=RollingHash()
    RH.addString(word)
    rh=RH.value()
    self.setdefault(rh,[]).append((word,offset,subVocab))

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
