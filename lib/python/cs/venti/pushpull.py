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

def fetch_hashcodes(S1, S2, hashcodes):
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
