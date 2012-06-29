#!/usr/bin/python -tt

from __future__ import print_function
import dbhash
import struct

dnscachefile='/var/lib/webalizer/dns_cache.db'

def dbseq(db):
  key=db.first()
  yield key
  dblen=len(db)
  for i in xrange(1, dblen):
    yield db.next()

def cacheEntries(cachefile=dnscachefile):
  db=dbhash.open(cachefile,"r")
  for key, value in dbseq(db):
    hostlen=len(value)-12
    when, numeric, foo, name = struct.unpack("III%ds"%hostlen, value)
    name=name.rstrip('\x00')
    if not numeric:
      yield key, when, name
  db.close()

def addCacheEntries(entries,cachefile=dnscachefile):
  db=dbhash.open(cachefile,"w")
  for key, when, name in entries:
    pass
  db.close()

if __name__ == '__main__':
  for key, when, name in cacheEntries():
    print(key, when, name)
