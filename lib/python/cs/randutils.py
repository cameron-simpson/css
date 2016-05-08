#!/usr/bin/python

import random
from cs.py3 import bytes

def rand0(maxn):
  ''' Generate a pseudorandom interger from 0 to `maxn`-1.
  '''
  return random.randint(0, maxn-1)

def randbool():
  ''' Return a pseudorandom Boolean value.
  '''
  return rand0(2) == 0

def randblock(size):
  ''' Generate a pseudorandom chunk of bytes of the specified size.
  '''
  return bytes( rand0(255) for _ in range(size) )
