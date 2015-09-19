#!/usr/bin/python

import random
from cs.py3 import bytes

def rand0(maxn):
  ''' Generate a pseudorandom interger from 0 to `maxn`.
  '''
  return random.randint(0, maxn)

def randblock(size):
  ''' Generate a pseudorandom chunk of bytes of the specified size.
  '''
  return bytes( rand0(255) for x in range(size) )
