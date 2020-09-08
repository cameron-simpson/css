#!/usr/bin/python
#

''' Processes for pushing or pulling blocks between Stores.
'''

import sys
from icontract import require
from cs.deco import fmtdoc
from cs.result import OnDemandFunction

DEFAULT_WINDOW_SIZE = 1024

@require(lambda S1, S2: S1.hashclass is S2.hashclass)
def pull_hashcode(S1, S2, hashcode):
  ''' Fetch the data for `hashcode` from `S2` and store in `S1`; return the data.
  '''
  data = S2.fetch(hashcode)
  h1 = S1.add(data)
  if h1 != hashcode:
    raise ValueError(
        "%s.add(%s.fetch(%s)) gave different hashcode: %s" %
        (S1, S2, hashcode, h1)
    )
  return data

@require(lambda S1, S2: S1.hashclass is S2.hashclass)
def pull_hashcodes(S1, S2, hashcodes):
  ''' Generator which fetches the data for the supplied `hashcodes`
      from `S2` if not in `S1`, updating `S1`.
      The generator yields `(hashcode,get_data)` where `hashcode` is
      each hashcode and `get_data` is a callable with returns the
      data.
  '''
  # mapping from hashcode to Result
  fetching = {}
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

@fmtdoc
@require(lambda S1, S2: S1.hashclass is S2.hashclass)
def missing_hashcodes(S1, S2, window_size=None):
  ''' Scan Stores `S1` and `S2` and yield hashcodes in `S2` but not in `S1`.

      Parameters:
      * `window_size`: number of hashcodes to fetch at a time for comparison,
        default from `DEFAULT_WINDOW_SIZE` (`{DEFAULT_WINDOW_SIZE}`).

      This relies on both Stores supporting the `.hashcodes` method;
      dumb unordered Stores do not.
  '''
  if window_size is None:
    window_size = DEFAULT_WINDOW_SIZE
  hashcodes1 = None
  last_hashcode1 = None
  hashcodes2 = list(S2.hashcodes(length=window_size))
  while hashcodes2 and (hashcodes1 is None or hashcodes1):
    # note end of S2 window so that we can fetch the next window
    last_hashcode2 = hashcodes2[-1]
    # test each S2 hashcode for presence in S1
    for ndx, hashcode in enumerate(hashcodes2):
      if hashcodes1 is None or hashcode > last_hashcode1:
        # past the end of S1 hashcode window
        # fetch new window starting at current hashcode
        hashcodes1 = list(
            S1.hashcodes(start_hashcode=hashcode, length=window_size)
        )
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
    hashcodes2 = list(
        S2.hashcodes(
            start_hashcode=last_hashcode2, length=window_size, after=True
        )
    )
  # no more hashcodes in S1 - gather everything else in S2
  while True:
    hashcode = None
    for hashcode in hashcodes2:
      yield hashcode
    if hashcode is None:
      break
    # fetch next bunch of hashcodes
    hashcodes2 = S2.hashcodes(
        start_hashcode=hashcode, length=window_size, after=True
    )

# pylint: disable=too-many-branches
@fmtdoc
@require(lambda S1, S2: S1.hashclass is S2.hashclass)
def missing_hashcodes_by_checksum(S1, S2, window_size=None):
  ''' Scan Stores `S1` and `S2` and yield hashcodes in `S2` but not in `S1`.
      This relies on both Stores supporting the .hashcodes and
      .hash_of_hashcodes methods; dumb unordered Stores do not.

      Parameters:
      * `window_size`: intial number of hashcodes to fetch at a time for comparison,
        default from `DEFAULT_WINDOW_SIZE` (`{DEFAULT_WINDOW_SIZE}`)
  '''
  if window_size is None:
    window_size = DEFAULT_WINDOW_SIZE
  # latest hashcode already compared
  start_hashcode = None
  after = False
  while True:
    # collect checksum of hashcodes after start_hashcode from S1 and S2
    hash1, h_final1 = S1.hash_of_hashcodes(
        length=window_size, start_hashcode=start_hashcode, after=after
    )
    if h_final1 is None:
      # end of S1 hashcodes - return all following S2 hashcodes
      break
    hash2, h_final2 = S2.hash_of_hashcodes(
        length=window_size, start_hashcode=start_hashcode, after=after
    )
    if h_final2 is None:
      # end of S2 hashcodes - done - return from function
      return
    if hash1 == hash2:
      if h_final1 != h_final2:
        raise RuntimeError(
            'hashes match but h_final1=%s != h_final2=%s' %
            (h_final1, h_final2)
        )
      # this chunk matches, fetch the next
      start_hashcode = h_final1
      after = True
      continue
    # mismatch, try smaller window
    if window_size >= 32:
      # shrink window until match found or window too small to bother
      window_size //= 2
      continue
    # fetch the actual hashcodes
    hashcodes2 = list(
        S2.hashcodes(
            start_hashcode=start_hashcode, length=window_size, after=after
        )
    )
    if not hashcodes2:
      # maybe some entires removed? - anyway, no more S2 so return
      return
    hashcodes1 = set(
        S1.hashcodes(
            start_hashcode=start_hashcode, length=window_size, after=after
        )
    )
    if not hashcodes1:
      # maybe some entries removed?
      # anyway, no more S1 so return all following S2 hashcodes
      break
    # in case things changed since earlier checksum
    h_final1 = max(hashcodes1)
    for ndx, hashcode in enumerate(hashcodes2):
      # note that if we fetch more hashcodes then we may get am empty window
      # just keep running with that if so - do not check h_final1
      if hashcodes1 and h_final1 < hashcode:
        # hashcodes1 does not cover this point in hashcodes2, fetch more
        hashcodes1 = set(
            S1.hashcodes(
                start_hashcode=hashcode, length=len(hashcodes2) - ndx
            )
        )
        if hashcodes1:
          h_final1 = max(hashcodes1)
        else:
          h_final1 = None
      if hashcode not in hashcodes1:
        yield hashcode
    # resume scan from here
    start_hashcode = hashcodes2[-1]
    after = True
  # collect all following S2 hashcodes
  while True:
    hashcodes2 = list(
        S2.hashcodes(
            start_hashcode=start_hashcode, length=window_size, after=after
        )
    )
    if not hashcodes2:
      break
    for hashcode in hashcodes2:
      yield hashcode
    start_hashcode = hashcodes2[-1]
    after = True

if __name__ == '__main__':
  from .pushpull_tests import selftest
  selftest(sys.argv)
