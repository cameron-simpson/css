#!/usr/bin/pythn -tt
#
# Knowledge about analog, the web log anaylser.
#       - Cameron Simpson <cs@zip.com.au> 04jan2009
#

from threading import Lock
from os import O_RDONLY, O_CREAT, O_EXCL
import os
import sys

dnscache='/var/spool/analog/dnscache'
dnscachelock='/var/spool/analog/dnslock'

def mklockfile(lockfile):
  ''' Create the analog lockfile.
      Returns the fd of the lock file or None on error.
  '''
  try:
    fd=os.open(lockfile,O_RDONLY|O_CREAT|O_EXCL)
  except IOError:
    return None
  return fd

def rmlockfile(lockfile,fd):
  ''' Remove the analog lock file and close the associate file descriptor.
  '''
  os.close(fd)
  os.unlink(lockfile)

def cacheEntries(cachefile=dnscache,lockfile=dnscachelock):
  ''' A generator that yields the contents of analog's DNS cache file as a
      sequence of:
        (unixtime, ipaddr, name)
  '''
  fp=open(cachefile)
  for line in fp:
    mins, ipaddr, name = line[:-1].split(" ",2)
    if name == '*':
      name = None
    yield mins*60, ipaddr, name
  fp.close()

def addCacheEntries(entries,cachefile=dnscache,lockfile=dnscachelock):
  ''' Append entries to the end of the analog DNS cache file.
      Entries is a sequence of (unixtime, ipaddr, list-of-names).
  '''
  fd=mklockfile(lockfile)
  if fd is None:
    return False
  fp=open(cachefile,"a")
  for when, ipaddr, name in entries:
    fp.write("%d %s %s\n" % (when/60, ipaddr, name))
  fp.close()
  rmlockfile(lockfile,fd)
  return True
