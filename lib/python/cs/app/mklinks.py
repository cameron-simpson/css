#!/usr/bin/env python
#
# Recode of mklinks in Python, partly for the exercise and partly to
# improve the algorithm.
#       - Cameron Simpson <cs@zip.com.au> 21may2006
#

import sys
import os
import os.path
import filecmp
from stat import S_ISREG
from collections import namedtuple
import filecmp
from tempfile import NamedTemporaryFile
from types import StringTypes
if sys.hexversion >= 0x02050000:
  from hashlib import md5
else:
  from md5 import md5
from cs.logutils import setup_logging, Pfx, error, warn, info, debug

# amount of file to read and checksum before trying whole file compare
HASH_PREFIX_SIZE = 8192

def main(argv, stdin=None):
  argv = list(argv)
  if stdin is None:
    stdin = sys.stdin

  cmd = os.path.basename(argv.pop(0))
  setup_logging(cmd)

  doit = True

  if argv and argv[0] == '-n':
    doit = False
    argv.pop(0)

  if not argv:
    argv = ['-']

  xit = 0

  FDB = FileInfoDB()
  for arg in argv:
    if arg == '-':
      if not hasattr(stdin, 'isatty') or not stdin.isatty():
        error("refusing to read filenames from a terminal")
        return 1
      lineno = 0
      for line in stdin:
        lineno += 1
        if not line.endswith('\n'):
          error("stdin:%d: unexpected EOF", lineno)
          return 1
        path = line[:-1]
        xit |= process(path, FDB, doit)
    else:
      xit |= process(arg, FDB, doit)
  return xit

def process(path, FDB, doit):
  xit = 0
  with Pfx("process(%s)", path):
    if os.path.isdir(path):
      for dirpath, dirnames, filenames in os.walk(path):
        for filename in sorted(filenames):
          xit |= process(os.path.join(dirpath, filename), FDB, doit)
        dirnames[:] = sorted(dirnames)
    else:
      fi = FDB[path]
      if fi is None:
        xit = 1
      else:
        if fi.isfile:
          fi.resolve(doit=doit)
        else:
          ##info("skip, not a regular file")
          pass
  return xit

IKey = namedtuple('IKey', 'ino dev')

class FileInfoDB(dict):

  def __init__(self):
    dict.__init__(self)
    # set of FileInfos with a given ikey
    self._fis_by_ikey = {}
    # set of primary FileInfos of a given size
    self._primes_by_size = {}
    # primary FileInfo by ikey
    # either the primary FileInfo for that ikey,
    # or the FileInfo to which this ikey should be hardlinked
    self._prime_by_ikey = {}

  def __getitem__(self, path):
    if path in self:
      fi = dict.__getitem__(self, path)
    else:
      fi = FileInfo(path, self)
      try:
        fi.lstat
      except OSError as e:
        error("%s: %s" % (path, e))
        return None
      self.learn(fi)
    return fi

  def learn(self, fi):
    dict.__setitem__(self, fi.path, fi)
    ikey = fi.ikey
    _by_ikey = self._fis_by_ikey
    if ikey not in _by_ikey:
      _by_ikey[ikey] = set((fi,))
    else:
      _by_ikey[ikey].add(fi)

  def relearn(self, fi):
    oikey = fi.ikey
    osize = fi.size
    if oikey in self._prime_by_ikey:
      del self._prime_by_ikey[oikey]
    self._primes_by_size[osize].discard(fi)
    dict.__delitem__(self, fi.path)
    self._fis_by_ikey[oikey].remove(fi)
    fi.reset()
    self.learn(fi)
    assert fi.size == osize

  def __setitem__(self, k, v):
    raise NotImplementedError("populated by __getitem__")

  def __delitem__(self, k, v):
    raise NotImplementedError("populated by __getitem__")

  def find_primary(self, fi):
    ''' Locate the primary for `fi`.
        If there is a designated primary for `fi.ikey`, return it.
        If not, look for an identical file. If found, designate it
        as the primary for that ikey and return it. Otherwise
        designate `fi` as the primary and return it.
    '''
    ikey = fi.ikey
    try:
      prime = self._prime_by_ikey[ikey]
      ##info("prime[ikey=%r] = %s", ikey, prime.path)
      return prime
    except KeyError:
      pass
    prime = None
    samesize = self._primes_by_size.setdefault(fi.size, set())
    for other in samesize:
      oikey = other.ikey
      assert oikey != ikey
      if ikey.dev != oikey.dev:
        # on another device - can't hardlink - skip it
        continue
      if fi == other:
        prime = other
        break
    if not prime:
      prime = fi
      self._primes_by_size[fi.size].add(fi)
    self._prime_by_ikey[ikey] = prime
    return prime

class FileInfo(object):

  def __init__(self, path, db):
    self.path = path
    self.db = db
    self.reset()

  def reset(self):
    self._lstat = None
    self._prefix_hash = None

  def __hash__(self):
    return id(self)

  def __eq__(self, other):
    ''' Compare this FileInfo with another file for equality of file contents.
    '''
    if isinstance(other, StringTypes):
      other = self.db[other]
    if self.samefile(other):
      return True
    if self.size != other.size:
      return False
    if self.prefix_hash != other.prefix_hash:
      return False
    return filecmp.cmp(self.path, other.path)

  def samefile(self, other):
    if isinstance(other, StringTypes):
      other = self.db[other]
    return self is other or self.ikey == other.ikey

  @property
  def lstat(self):
    _lstat = self._lstat
    if _lstat is None:
      _lstat = os.lstat(self.path)
      self._lstat = _lstat
    return _lstat

  @property
  def isfile(self):
    return S_ISREG(self.lstat.st_mode)

  @property
  def ikey(self):
    S = self.lstat
    return IKey(S.st_ino, S.st_dev)

  @property
  def size(self):
    return self.lstat.st_size

  @property
  def prefix_hash(self):
    _hash = self._prefix_hash
    if _hash is None:
      with open(self.path, "rb") as fp:
        _hash = md5(fp.read(HASH_PREFIX_SIZE)).digest()
      self._prefix_hash = _hash
    return _hash

  @property
  def primary(self):
    ''' Return the primary file that matches this one, possibly self.
        It will be:
          - the primary of an other inode to which this should link
          - the primary of this inode, possibly self
    '''
    return self.db.find_primary(self)

  def resolve(self, doit):
    ''' Become one with our primary.
        If we are our primary or our primary has the same inode, do nothing.
        If another is our primary, hardlink and update maps.
        If the hardlink fails, become our own primary.
    '''
    assert self.isfile
    with Pfx("resolve(%s)", self.path):
      prime = self.primary
      if prime is self or prime.ikey == self.ikey:
        return
      assert self.ikey.dev == prime.ikey.dev
      rpath = os.path.realpath(self.path)
      rdir = os.path.dirname(rpath)
      print("%s => %s" % (self.path, prime.path))
      if doit:
        with NamedTemporaryFile(dir=rdir) as tfp:
          with Pfx("unlink(%s)", tfp.name):
            os.unlink(tfp.name)
          with Pfx("rename(%s, %s)", rpath, tfp.name):
            os.rename(rpath, tfp.name)
          with Pfx("link(%s, %s)", prime.path, rpath):
            os.link(prime.path, rpath)
      self.db.relearn(self)

# TODO: subsume: promote self to primary, eg if link limit hit

if __name__ == '__main__':
  sys.exit(main(sys.argv))
