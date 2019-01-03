#!/usr/bin/python
#
# Ad hoc assortment of numeric functions.
#   - Cameron Simpson <cs@cskk.id.au> 10mar2015
#

''' A few ad hoc numeric alogrithms: `factors` and `primes`.
'''

DISTINFO = {
    'description': "some numeric functions; currently primes() and factors()",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Development Status :: 6 - Mature",
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [],
}

def primes():
  ''' Generator yielding the primes in order starting at 2.
  '''
  yield 2
  known = [2]
  n = 3
  while True:
    for k in known:
      if k * k > n:
        yield n
        known.append(n)
        break
    n += 2

def factors(n):
  ''' Generator yielding the prime factors of `n` in order from lowest to highest.
  '''
  for p in primes():
    if p * p > n:
      if n > 1:
        yield n
      break
    while n % p == 0:
      yield p
      n //= p
