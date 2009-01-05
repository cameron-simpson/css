#!/usr/bin/python -tt
#

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
  print "open", cachefile
  db=dbhash.open(cachefile,"r")
  for key, value in dbseq(db):
    hostlen=len(value)-12
    when, numeric, foo, s = struct.unpack("III%ds"%hostlen, value)
    s=s.rstrip('\x00')
    yield key, when, numeric, s
  db.close()

if __name__ == '__main__':
  for key, when, numeric, value in cacheEntries():
    print key, when, `value`
