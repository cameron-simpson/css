#!/usr/bin/python -tt

from cs.venti.blocks import BlockList, BlockRef
from cs.venti.hash import MIN_BLOCKSIZE, MAX_BLOCKSIZE, MAX_SUBBLOCKS
from cs.misc import isdebug
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

def blocksOf(dataSource,vocab=None):
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
