#!/usr/bin/python
#
# Self tests for cs.threads.
#       - Cameron Simpson <cs@zip.com.au>
#

from __future__ import print_function
import sys
import time
import unittest
if sys.hexversion < 0x03000000:
  from Queue import Queue
else:
  from queue import Queue
from cs.queues import TimerQueue
from cs.later import Later
##from cs.logutils import D

def D(msg, *a):
  if a:
    msg = msg % a
  with open('/dev/tty', 'a') as tty:
    print(msg, file=tty)

def selftest(argv):
  unittest.main(__name__, None, argv)

if __name__ == '__main__':
  selftest(sys.argv)
