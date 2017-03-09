#!/usr/bin/python
#
# Parsers for data streams, emitting data and offsets.
# These sit in front of the core rolling hash blockifier.
#   - Cameron Simpson <cs@zip.com.au> 05mar2017
#

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
