#!/usr/bin/python -tt

from cs.venti.blocks import BlockList, BlockRef
from cs.venti.hash import MIN_BLOCKSIZE, MAX_BLOCKSIZE, MAX_SUBBLOCKS
from cs.misc import isdebug, debug
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

def blocksOfOLD(dataSource,vocab=None):
  ''' A generator that reads data from an iterable dataSource
      and yields it as blocks at appropriate edge points.
  '''
  if vocab is None:
    global DFLT_VOCAB
    vocab=DFLT_VOCAB
  buf=[]
  buflen=0
  RH=RollingHash()
  # invariant: all the bytes in buf[]
  # have been fed to the rolling hash
  for data in dataSource:
    doff=0
    lastdoff=0
    for c in data:
      RH.add(ord(c))
      doff+=1
      if buflen+doff < MIN_BLOCKSIZE:
        continue
      isEdge=False
      endoff=None
      rh=RH.value
      if buflen+doff >= MAX_BLOCKSIZE or rh == HASH_MAGIC:
        isEdge=True
        endoff=doff
      elif rh == HASH_MAGIC:
        isEdge=True
        endoff=doff
      elif rh in vocab:
        chkWord=data[:doff]
        for word, offset, subVocab in vocab[rh]:
          if chkWord.endswith(word):
            isEdge=True
            endoff=doff-len(word)+offset
      if isEdge:
        assert buflen+doff >= MIN_BLOCKSIZE and buflen+doff <= MAX_BLOCKSIZE
        buf.append(data[lastdoff:endoff])
        yield "".join(buf)
        lastdoff=endoff
        buf=[]
        buflen=0
        RH.reset()
    if lastdoff < len(data):
      buf.append(data[lastdoff:])
      buflen+=len(data)-lastdoff
  if len(buf) > 0:
    yield "".join(buf)

class strlist:
  def __init__(self):
    self.reset()
  def reset(self):
    self.buf=[]
    self.len=0
  def append(self,s):
    self.buf.append(s)
    self.len+=len(s)
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
  debug("blocksOf()...")
  if vocab is None:
    global DFLT_VOCAB
    vocab=DFLT_VOCAB
  vhs=vocab.keys()      # hashes liked by the vocab

  buf=strlist()
  RH=RollingHash()
  # invariant: all the bytes in buf[]
  # have been fed to the rolling hash
  for data in dataSource:
    skip=MIN_BLOCKSIZE-len(buf)
    if skip > 0:
      if skip >= len(data):
        RH.addString(data)
        buf.append(data)
        continue
      left, data = cut(data,skip)
      RH.addString(left)
      buf.append(left)

    while len(data) > 0:
      maxlen=MAX_BLOCKSIZE-len(buf)
      h, off = RH.findEdge(data,maxlen,vhs)
      assert off > 0 and off <= len(data), \
             "off=%d, len(data)=%d" % (off, len(data))
      if h is None:
        # no edge found
        assert off == min(maxlen,len(data))
        if off < len(data):
          left, data = cut(data, off)
        else:
          left, data = data, ''
        buf.append(left)
        yield str(buf)
        buf.reset()
        continue

      if h in vocab:
        # findEdge returns a hash/offset at the end of the vocabulary word.
        # Check that it is a vocab word, and figure out the desired cut point.
        # Because the hash is computed up to the offset, prefill the new buf
        # with the text from the cut point to the hash/offset.
        ds=str(data)
        for word, woffset, subVocab in vocab[h]:
          if ds.endswith(word):
            if subVocab is not None:
              # update for new vocabuary
              vocab=subVocab
              vhs=vocab.keys()
            # put the left part of the match word into the buffer
            # and yield it
            buffer.append(word[:woffset])
            left, right = cut(data,off-len(word)+woffset)
            debug("blocksOf: matched \"%s\" at \"%.20s\"" % (word, right))
            buf.append(left)
            yield str(buf)
            buf.reset()
            # prefill the buffer with the right part of the match word
            buf.append(word[woffset:])
            # crop data and proceed
            data = buffer(data,off)
            continue
        ##debug("h=%d, but no vocab match" % h)

      left, data = cut(data, off)
      buf.append(left)
      if h == HASH_MAGIC:
        yield str(buf)
        buf.reset()

    assert len(data) == 0

  if len(buf) > 0:
    yield str(buf)


HASH_MAGIC=511
class RollingHash:
  ''' Compute a rolling hash over 4 bytes of data.
      TODO: this is a lousy algorithm!
  '''
  def __init__(self):
    self.reset()

  def reset(self):
    self.value=0
    self.__window=[0,0,0,0]
    self.__woff=0       # offset to next storage place

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
    for i in range(maxlen):
      self.add(ord(data[i]))
      if self.value == HASH_MAGIC or self.value in hashcodes:
        ##debug("edge found, returning (hashcode=%d, offset=%d)" % (self.value,i+1))
        return self.value, i+1
    debug("no edge found, hash now %d, returning (None, %d)" % (self.value,maxlen))
    return None, maxlen

  def add(self,oc):
    rh=self.value
    w=self.__window
    woff=self.__woff
    c2 = w[woff]
    rh -= c2
    ##c2 = ((oc%16)<<4) + (oc/16)
    c2=oc
    rh += c2
    w[woff]=c2
    woff=(woff+1)%4
    self.__woff=woff
    self.value=rh
    return rh

  def addString(self,s):
    for c in s:
      self.add(ord(c))

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
    rh=RH.value
    self.setdefault(rh,[]).append((word,offset,subVocab))

# TODO: reform as list of (str, offset, sublist).
DFLT_VOCAB=Vocabulary({
                "\ndef ": 1,          # python top level function
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
