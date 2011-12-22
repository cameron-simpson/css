#!/usr/bin/python
#
# Unit tests for cs.mailutils.
#       - Cameron Simpson <cs@zip.com.au>
#

import os
import sys
import time
import unittest
from cs.mailutils import Maildir

class TestMaildir(unittest.TestCase):

  def test00basic(self):
    t0 = time.time()
    M = Maildir(os.path.join(os.environ['HOME'], 'ZZM'))
    t1 = time.time()
    ##print >>sys.stderr, "Maildir(ZZM): %gs" % (t1-t0,)
    keys = M.keys()
    t2 = time.time()
    ##print >>sys.stderr, "Maildir(ZZM).keys(): %gs, %d keys" % (t2-t1,len(keys))
    for key in keys[:100]:
      msg = M[key]
    t3 = time.time()
    ##print >>sys.stderr, "Maildir(ZZM): scan 100 msgs %gs" % (t3-t2,)
    for key in keys[100:200]:
      hdr = M.get_headers(key)
    t4 = time.time()
    ##print >>sys.stderr, "Maildir(ZZM): scan 100 hdrs %gs" % (t4-t3,)

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
