#!/usr/bin/env python3

from cs.deco import promote
from cs.urlutils import URL

from cs.debug import trace, X, r, s

@promote
def hrefs(U: URL):
  ''' Generator yielding the HREFs from a `URL`.
  '''
  yield from U.hrefs(absolute=True)

@promote
def srcs(U: URL):
  ''' Generator yielding the SRCs from a `URL`.
  '''
  yield from U.srcs(absolute=True)
