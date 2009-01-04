#!/usr/bin/python -tt
#

import dbhash

dnscachefile='/var/lib/webalizer/dns_cache.db'

def cacheEntries(cachefile=dnscachefile):
  db=dbhash.open(cachefile,"r")
  for key, value in db.items():
    yield key, value
  db.close()

if __name__ == '__main__':
  for key, value in cacheEntries():
    print key, value
