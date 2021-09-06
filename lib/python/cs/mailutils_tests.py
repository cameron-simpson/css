#!/usr/bin/python
#
# Unit tests for cs.mailutils.
#       - Cameron Simpson <cs@cskk.id.au>
#

from __future__ import print_function
import os
from os.path import dirname, join as joinpath
import sys
import time
import unittest
from cs.mailutils import Maildir

def D(msg, *a):
  if a:
    msg = msg % a
  print(msg, file=sys.stderr)

testdatadir = joinpath(dirname(__file__), 'testdata', 'cs.mailutils')
testmaildir = joinpath(testdatadir, 'maildir')

class TestMaildir(unittest.TestCase):
  ''' Tests for `cs.mailutils.Maildir`.
  '''

  @unittest.skipUnless(os.path.exists(testmaildir), 'no test Maildir ' + testmaildir)
  def test00basic(self):
    t0 = time.time()
    M = Maildir(testmaildir)
    t1 = time.time()
    ##D("Maildir(%s): %gs" % (testmaildir, t1-t0,))
    keys = list(M.keys())
    t2 = time.time()
    ##D("Maildir(%s).keys(): %gs, %d keys" % (testmaildir, t2-t1, len(keys)))
    for key in keys[:100]:
      msg = M[key]
    t3 = time.time()
    ##D("Maildir(%s): scan 100 msgs %gs" % (testmaildir, t3-t2,))
    for key in keys[100:200]:
      hdr = M.get_headers(key)
    t4 = time.time()
    ##D("Maildir(%s): scan 100 hdrs %gs" % (testmaildir, t4-t3,))

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
