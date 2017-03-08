#!/usr/bin/python
#
# Parsers for data streams, emitting data and offsets.
# These sit in front of the core rolling hash blockifier.
#   - Cameron Simpson <cs@zip.com.au> 05mar2017
#

def rolling_hash_parser(data_chunks, vocab=None, min_block=None, max_block=None):
  ''' Collect data strings from the iterable data_chunks and yield data Blocks with desirable boundaries.
      Note: this parser always yields the offset of the end of its
      data, making it suitably for cleaning up the last data from
      some parser which doesn't always emit its final offset.
  '''
  # also accept a bare bytes value
  if isinstance(data_chunks, bytes):
    data_chunks = (data_chunks,)
  if vocab is None:
    vocab = DFLT_VOCAB
  if min_block is None:
    min_block = MIN_BLOCKSIZE
  elif min_block < 8:
    raise ValueError("rejecting min_block < 8: %s", min_block)
  if max_block is None:
    max_block = MIN_BLOCKSIZE
  elif max_block >= 1024*1024:
    raise ValueError("rejecting max_block >= 1024*1024: %s", max_block)
  chunkQ = IterableQueue()
  yield chunkQ
  hash_value = 0    # initial rolling hash value
  offset = 0        # scan position
  last_offset = 0   # last offset yielded to consumer
  end_offset = 0    # offset of the end of the latest received chunk
  for data in data_chunks:
    chunkQ.put(data)
    end_offset += len(data)
    for data_offset, b in enumerate(data):
      # advance the rolling hash function
      hash_value = ( ( ( hash_value & 0x001fffff ) << 7
                     )
                   | ( ( b & 0x7f )^( (b & 0x80)>>7 )
                     )
                   )
      offset += 1
      if hash_value % 4093 == 1:
        yield offset
        last_offset = offset
      else:
        # test against the current vocabulary
        is_edge, edge_offset, subVocab = vocab.test_for_edge(data, data_offset)
        if is_edge:
          if edge_offset < 0 or data_offset + edge_offset >= len(data):
            raise RuntimeError("len(data)=%d and edge_offset=%s; data=%r"
                               % (len(data), edge_offset, data))
          # boundary offset is the current offset adjusted for the edge_offset
          suboffset = offset + edge_offset
          if suboffset > last_offset and suboffset <= end_offset:
            # only emit this offset if it is in range:
            # not before the previously emitted offset
            # and not after the end of the data chunk we have
            # provided to the parser handler
            yield suboffset
            last_offset = suboffset
          if subVocab is not None:
            # switch to new vocabulary
            vocab = subVocab
  if last_offset < end_offset:
    yield end_offset
  chunkQ.close()

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

DFLT_VOCAB = Vocabulary({
                b"\ndef ": 1,         # python top level function
                b"\n  def ": 1,       # python class method, 2 space indent
                b"\n    def ": 1,     # python class method, 4 space indent
                b"\n\tdef ": 1,       # python class method, TAB indent
                b"\nclass ": 1,       # python top level class
                b"\nfunc ": 1,        # Go function
                b"\nfunction ": 1,    # JavaScript or shell function
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
