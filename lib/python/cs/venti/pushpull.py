#!/usr/bin/python
#
# Processes for pushing or pulling blocks between Stores.
#   - Cameron Simpson <cs@zip.com.au> 13dec2015
# 

from functools import partial
from cs.asynchron import OnDemandFunction
from cs.logutils import X

def pull_hashcode(S1, S2, hashcode):
  ''' Fetch the data for `hashcode` from `S2` and store in `S1`; return the data.
  '''
  data = S2.fetch(hashcode)
  h1 = S1.add(data)
  if h1 != hashcode:
    raise ValueError("%s.add(%s.fetch(%s)) gave different hashcode: %s"
                     % (S1, S2, hashcode, h1))
  return data

def pull_hashcodes(S1, S2, hashcodes):
  ''' Generator which fetches the data for the supplied `hashcodes` from `S2` if not in `S1`, updating `S1`.
      The generator yields (hashcode, get_data) where hashcode is
      each hashcode and get_data is a callable with returns the
      data.
  '''
  # mapping from hashcode to Result
  fetching = {}
  LFs = []
  for hashcode in hashcodes:
    if hashcode in fetching:
      yield hashcode, fetching[hashcode]
    elif hashcode in S1:
      yield hashcode, OnDemandFunction(S1.fetch, hashcode)
    else:
      # initiate pull of hashcode via S2's scheduler
      # TODO: purge hashcode from fetching after pull in race free fashion
      # to that we can run this indefinitely
      LF = fetching[hashcode] = S2._defer(pull_hashcode, S1, S2, hashcode)
      yield hashcode, LF

def missing_hashcodes(S1, S2, window_size=None):
  ''' Scan Stores `S1` and `S2` and yield hashcodes in `S2` but not in `S1`.
      This relies on both Stores supporting the .hashcodes method;
      dumb unordered Stores do not.
      `window_size`: number of hashcodes to fetch at a time for comparison,
                     default 1024.
  '''
  if window_size is None:
    window_size = 1024
  hashcodes1 = None
  hashcodes2 = list(S2.hashcodes(length=window_size))
  while hashcodes2 and (hashcodes1 is None or hashcodes1):
    # note end of S2 window so that we can fetch the next window
    last_hashcode2 = hashcodes2[-1]
    # test each S2 hashcode for presence in S1
    for ndx, hashcode in enumerate(hashcodes2):
      if hashcodes1 is None or hashcode > last_hashcode1:
        # past the end of S1 hashcode window
        # fetch new window starting at current hashcode
        hashcodes1 = list(S1.hashcodes(start_hashcode=hashcode, length=window_size))
        if not hashcodes1:
          # no more S1 hashcodes, cease scan
          # keep the unscanned hashcodes (including current)
          # to yield in the post-scan loop, where we don't bother consulting S1
          hashcodes2 = hashcodes2[ndx:]
          break
        # note highest hashcode as top of window
        last_hashcode1 = hashcodes1[-1]
        # convert to set for fast membership lookup
        hashcodes1 = set(hashcodes1)
      if hashcode not in hashcodes1:
        yield hashcode
    if not hashcodes1:
      break
    # fetch next batch of hashcodes from S2
    hashcodes2 = list(S2.hashcodes(start_hashcode=last_hashcode2, length=window_size,
                                   after=True))
  # no more hashcodes in S1 - gather everything else in S2
  while True:
    hashcode = None
    for hashcode in hashcodes2:
      yield hashcode
    if hashcode is None:
      break
    # fetch next bunch of hashcodes
    hashcodes2 = S2.hashcodes(start_hashcode=hashcode, length=window_size, after=True)

def missing_hashcodes_by_checksum(S1, S2, window_size=None):
  ''' Scan Stores `S1` and `S2` and yield hashcodes in `S2` but not in `S1`.
      This relies on both Stores supporting the .hashcodes and
      .hash_of_hashcodes methods; dumb unordered Stores do not.
      `window_size`: intial number of hashcodes to fetch at a time for comparison,
                     default 1024.
  '''
  if window_size is None:
    window_size = 1024
  X("missing_hashcodes_by_checksum: window_size=%d", window_size)
  # latest hashcode already compared
  start_hashcode = None
  while True:
    # collect checksum of hashcodes after start_hashcode from S1 and S2
    hash1, h_final1 = S1.hash_of_hashcodes(length=window_size, start_hashcode=start_hashcode, after=True)
    if h_final1 is None:
      # end of S1 hashcodes - return all following S2 hashcodes
      X("end of S1 after hashcode %s; yield all following from S2", start_hashcode)
      break
    hash2, h_final2 = S2.hash_of_hashcodes(length=window_size, start_hashcode=start_hashcode, after=True)
    if h_final2 is None:
      # end of S2 hashcodes - done - return from function
      X("end of S2 after hashcode %s; nothing more to yield", start_hashcode)
      return
    if hash1 == hash2:
      if h_final1 != h_final2:
        raise RuntimeError('hashes match but h_final1=%s != h_final2=%s'
                           % (h_final1, h_final2))
      # this chunk matches, fetch the next
      X("chunk after %s matches, advance to chunk after %s", start_hashcode, h_final1)
      start_hashcode = h_final1
      continue
    X("chunk after %s mismatched", start_hashcode)
    # mismatch, try smaller window
    if window_size >= 32:
      # shrink window until match found or window too small to bother
      owindow_size = window_size
      window_size //= 2
      X("reduce window size from %d to %d", owindow_size, window_size)
      continue
    X("chunk after %s mismatched; compare actual hashcodes", start_hashcode)
    # fetch the actual hashcodes
    hashcodes1 = set(S1.hashcodes(start_hashcode=start_hashcode, length=window_size, after=True))
    if not hashcodes1:
      X("end of S1 after hashcode %s; yield all following from S2", start_hashcode)
      # maybe some entries removed? - anyway, no more S1 so return all following S2 hashcodes
      break
    hashcodes2 = list(S2.hashcodes(start_hashcode=start_hashcode, length=window_size, after=True))
    if not hashcodes2:
      X("end of S2 after hashcode %s; nothing more to yield", start_hashcode)
      # maybe some entires removed? - anyway, no more S2 so return
      return
    for hashcode in hashcodes2:
      if hashcode not in hashcodes1:
        X("YIELD %s (not in %r)", hashcode, sorted(hashcodes1))
        yield hashcode
      else:
        X("not yield %s", hashcode)
    # resume scan from here
    start_hashcode = hashcodes2[-1]
    X("advance S2 scan to %s", start_hashcode)
  # collect all following S2 hashcodes
  X("finished with S1, just scan tail of S2")
  while True:
    X("tail scan S2 after %s", start_hashcode)
    hashcodes2 = list(S2.hashcodes(start_hashcode=start_hashcode, length=window_size, after=True))
    if not hashcodes2:
      X("no S2 hashcodes after %s, done", start_hashcode)
      break
    for hashcode in hashcodes2:
      X("tail scan: yield %s", hashcode)
      yield hashcode
    start_hashcode = hashcodes2[-1]

if __name__ == '__main__':
  from cs.debug import selftest
  selftest('cs.venti.pushpull_tests')
