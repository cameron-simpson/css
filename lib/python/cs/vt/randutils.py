#!/usr/bin/python

from random import randint

def rand0(maxn):
  ''' Generate a pseudorandom interger from 0 to `maxn`-1.
  '''
  return randint(0, maxn-1)

def randbool():
  ''' Return a pseudorandom Boolean value.
  '''
  return randint(0,1) == 0

def randblock(size):
  ''' Generate a pseudorandom chunk of bytes of the specified size.
  '''
  return bytes( randint(0,255) for _ in range(size) )
