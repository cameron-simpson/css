#!/usr/bin/python -tt
#

from cs.venti import MIN_BLOCKSIZE, MAX_BLOCKSIZE, MAX_SUBBLOCKS
from cs.threads import PreQueue
from cs.lex import unctrl
from cs.misc import the, ifdebug, ifverbose, warn, verbose

# TODO: reform as list of (str, offset, sublist).
matches={ "\ndef ": 1,          # python top level function
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
        }

def makeMatchIndex(matches):
  ''' Converts a match dictionary into a hash index dictionary.
  '''
  index={}
  for s in matches.keys():
    RH=RollingHash()
    RH.addString(s[-4:])
    rh=RH.value
    if ifverbose():
      warn("hash(%s) = %d" % (unctrl(s),rh))
    offset=matches[s]
    if type(offset) is int:
      subindex=None
    else:
      offset, submatches = offset
      subindex=makeMatchIndex(submatches)
    node=(s,offset,subindex)
    if rh in index:
      index[rh].append(node)
    else:
      index[rh]=[node]
  if ifverbose():
    warn("matchIndex=%s" % index)
  return index

HASH_MAGIC=511
class RollingHash:
  ''' Compute a rolling hash over 4 bytes of data.
      TODO: this is a lousy algorithm!
  '''
  def __init__(self):
    self.value=0
    self.__window=[0,0,0,0]
    self.__woff=0       # offset to next storage place

  def addChar(self,c):
    rh=self.value
    w=self.__window
    woff=self.__woff
    oc=ord(c)
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
      self.addChar(c)

_mainMatchIndex=makeMatchIndex(matches)

class Blocker:
  ''' Class to accept data from a stream and break it into blocks.
      The blocks are written to the output queue (self.Q).
      If outQ is supplied and not None, it is used as the output queue.
      If matches is supplied and not None it is used as the block edge
      vocabulary, otherwise the default '_mainMatchIndex' vocabulary is used.
  '''
  def __init__(self,matches=None,outQ=None):
    if matches is None:
      self.matchIndex=_mainMatchIndex
    else:
      self.matchIndex=makeMatchIndex(matches)

    if outQ is None:
      outQ=PreQueue(size=256)
      
    self.closed=False
    self.pending=[]     # list of uncollated input blocks
    self.pendlen=0
    self.Q=outQ
    self.rhash=RollingHash()

  def __iter__(self):
    ''' Iterator to yield blocks from the stream.
    '''
    for item in self.Q:
      if item is None:
        break
      yield item

  def put(self,s):
    ''' Add a string to the stream.
        Blocks are put onto the output Queue as boundaries are recognised.
    '''
    rh=self.rhash
    I=self.matchIndex
    off=0
    matchOff=None
    for c in s:
      off+=1
      h=rh.addChar(c)
      if self.pendlen < MIN_BLOCKSIZE:
        continue

      if self.pendlen >= MAX_BLOCKSIZE:
        matchOff=off
      else:
        if h in I:
          # hashcode match on vocabulary hash
          for ms, moff, subI in I[h]:
            if off < len(ms):
              # TODO: grab chars from pending blocks
              pass
            else:
              if s[off-len(ms):off] == ms:
                # matched
                ##print "match on %s at [%s...]" % (unctrl(ms), unctrl(s[off-len(ms):off+20]))
                matchOff=off-len(ms)+moff
                break
        if matchOff is None \
        and off%8 == 0 \
        and h == HASH_MAGIC:
          ##print "match in magic(%d) at [%s...]" % (rh.value, unctrl(s[off:off+20]))
          matchOff=off

      if matchOff is not None:
        # dispatch this block, start afresh with tail of string
        self.pending.append(s[:matchOff])
        self.flush()
        rh=RollingHash()
        s=s[matchOff:]
        off=0
        matchOff=None

    if len(s) > 0:
      self.pending.append(s)
      self.pendlen+=len(s)

    # save the hash object - may be new
    self.rhash=rh

  def flush(self):
    if len(self.pending) > 0:
      self.Q.put("".join(self.pending))
      self.pending=[]
      self.pendlen=0

  def close(self):
    if self.closed:
      cmderr("warning: already closed Blocker %s" % self)
    else:
      self.closed=True
      self.flush()
      self.Q.put(None)

def blocks2bref(S,Q):
  ''' A function that reads all the blocks off a data block queue
      and returns a BlockRef to the top of the storage tree.
      'S' is a Store.
      'Q' is a iterable returning data blocks, with an unget() method to
        return an item to the front of the queue.
      Typical use:
        E=Blocker()
        ... spawn a thread that put()s data onto E then close()s E ...
        bref=blockSink(S, E.Q)
      or:
        E=Blocker()
        ... spawn a thread that calls bref=blockSink(S, E.Q), store bref
        put() data onto E then close()s E
        join() the other thread
        collect bref
  '''
  return the(_blockRefSink(S,Q,indirect=False,outermost=True))

def _blockRefSink(S,Q,indirect,outermost):
  ''' A generator that gets data blocks off an iterable queue and yields
      BlockRefs. A call with outermost == True will yield a single BlockRef to
      an indirect blockref reaching all the leaf blocks consumed,
      a direct blockref to the sole leaf block,
      or a direct blockref to the empty block.
      'S' is a Store.
      'Q' is a iterable returning data blocks, with an unget() method to
        return an item to the front of the queue.
      'indirect' means that this instance reads indirect BlockRefs from a
        lower order instance if this function, otherwise we read data blocks.
      'outermost' means we are the first, highest order instance of the
        function; we read blocks or BlockRefs and if we overflow our
        BlockList we spawn a fresh generator of our own order to read from
        and read from it, thus raising our own order.
  '''
  from cs.venti.blocks import BlockList, BlockRef
  blocks=BlockList(S)
  while True:
    try:
      item=Q.next()
    except StopIteration:
      break
    if indirect:
      # we're reading BlockRefs from the queue
      assert item is not None
      bref=item
    else:
      # we're reading data blocks from the queue
      if item is None:
        break
      ##ucitem=unctrl(item)
      ##if len(ucitem) > 40: ucitem=ucitem[:40]+'...'
      ##print "sinkBlocks: data block=[%s]:%d" % (ucitem, len(item))
      bref=BlockRef(S.store(item),False,len(item))

    if len(blocks) >= MAX_SUBBLOCKS:
      # The current block list is already full.
      # Convert it into an indirect BlockRef
      # and make a fresh BlockList for the current bref.
      nbref=BlockRef(S.store(blocks.pack()),True,blocks.span())
      blocks=BlockList(S)
      if not outermost:
        # An inner assembler.
        # Dispatch the packed up BlockList to our caller.
        yield nbref
      else:
        # The outermost assembler.
        # Spawn a subiterator to continue assembling blocks
        # of the same level of indirection as ourself.
        # Push the block back onto the queue for reprocessing.
        # We will queue the fresh indirect block instead.
        Q.unget(item)
        verbose("spawn subsink...")
        Q=_blockRefSink(S,Q,indirect,False)
        indirect=True
        bref=nbref
    # Add the current BlockRef into the BlockList.
    blocks.append(bref)

  if len(blocks) == 0:
    bref=BlockRef(S.store(""),False,0)
  elif len(blocks) == 1:
    bref=blocks[0]
  else:
    bref=BlockRef(S.store(blocks.pack()),True,blocks.span())
  yield bref
