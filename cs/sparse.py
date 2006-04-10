from cs.misc import debug, warn

class SparseSeq:
  def __init__(self,seq):
    self.__seq=seq
    self.__cache=[]
    self.__lastchunk=None

  def __len__(self):
    return len(self.__seq)
  def __repr__(self):
    return `self.__seq`

  def __getitem__(self,ndx):
    debug("__getitem__", `ndx`)
    if type(ndx) is slice:
      (start,end,stride)=ndx.indices(self.__len__())

      # pull in a big chunk if slice is dense enough
      if stride >= -2 and stride <= 2:
	if stride < 0: self.preload(end,start)
	else:          self.preload(start,end)

      return [self.__getitem__(i) for i in range(start,end,stride)]

    # hope this index is in the most recent chunk
    chunk=self.__lastchunk
    if not chunk or ndx < chunk[0] or ndx >= chunk[1]:
      chunk=self.getChunk(ndx)
      self.__lastchunk=chunk

    offset=ndx-chunk[0]
    debug("  ndx =", ndx, "chunk =", `chunk`, "offset =", offset)
    return chunk[2][offset]

  def getChunk(self,ndx):
    debug("getChunk", ndx)
    p=self.findChunkIndex(ndx)
    if p >= len(self.__cache) or ndx < self.__cache[p][0]:
      # not cached - fetch and relocate
      self.preload(ndx)
      p=self.findChunkIndex(ndx)

    return self.__cache[p]

  def findChunkIndex(self,ndx):
    debug("findChunkIndex", ndx)
    cache=self.__cache
    debug("  cache =", `cache`)

    # pre: lch <= loc(ndx)
    #      rch > loc(ndx)
    lch=0
    rch=len(cache)-1
    debug("  lch =", lch, "rch =", rch)
    while lch <= rch:
      p=int((lch+rch)/2)
      debug("    loop: lch =", lch, "p =", p, "rch =", rch)
      chunk=cache[p]
      if chunk[0] > ndx:
	# chunk strictly above sought ndx, bring rch down
	rch=p-1
      elif chunk[1] <= ndx:
	# chunk strictly below sought ndx, bring lch up (p was > lch)
	lch=p+1
      else:
	# chunk includes the index
	debug("    loop: found ndx at p =", p)
	return p

    debug("  ndx not found, returning lch =", lch)
    return lch

  def preload(self,low,high=None):
    if high is None: high=low+1

    debug("preload[",low,':',high,"]")
    p=self.findChunkIndex(low)
    cache=self.__cache
    debug("  while", low, "<", high, "p =", p)
    while low < high:
      if p >= len(cache):
	debug("  append[", low, ":", high, "]")
	cache.append((low,high,list(self.__seq[low:high])))
	low=high
      else:
	chunk=cache[p]
        if low < chunk[0]:
	  chhigh=chunk[0]
	  debug("  insert[", low, ":", chhigh, "]")
	  cache.insert(p,(low,chhigh,list(self.__seq[low:chhigh])))
	  # skip in-range chunk
	  low=chunk[1]
	  p+=2
	else:
	  debug("skip existing chunk")
	  low=chunk[1]
	  p+=1

    debug("preload done")

