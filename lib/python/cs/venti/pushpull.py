#!/usr/bin/python
#
# Processes for pushing or pulling blocks between Stores.
#   - Cameron Simpson <cs@zip.com.au> 13dec2015
# 

from functools import partial
from cs.asynchron import OnDemandFunction

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

def missing_hashcodes(S1, S2):
  ''' Scan Stores `S1` and `S2` and yield hashcodes in `S2` but not in `S1`.
      This relies on both Stores supporting the .hashcodes method;
      dumb unordered Stores do not.
  '''
  rq_size = 1024
  hashcodes2 = list(S2.hashcodes(length=rq_size))
  while hashcodes2:
    # note end of S2 window so that we can fetch the next window
    last_hashcode2 = hashcodes2[-1]
    # test each S2 hashcode for presence in S1
    for ndx, hashcode in enumerate(hascodes2):
      if hashcode > last_hashcode1:
        # past the end of S1 hashcode window
        # fetch new window starting at current hashcode
        hashcodes1 = list(S1.hashcodes(hashcode=hashcode, length=rq_size))
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
    # fetch next batch of hashcodes from S2
    hashcodes2 = list(S2.hashcodes(hashcode=last_hashcode2, length=rq_size, after=True))
  # no more hashcodes in S1 - gather everything else in S2
  while True:
    hashcode = None
    for hashcode in hashcodes2:
      yield hashcode
    if hashcode is None:
      break
    # fetch next bunch of hashcodes
    hashcodes2 = S2.hashcodes(hashcode=hashcode, length=rq_size, after=True)
