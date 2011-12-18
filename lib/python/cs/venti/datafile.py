#!/usr/bin/python -tt
#
# The basic data store for venti blocks.
#       - Cameron Simpson <cs@zip.com.au>
#

import sys
from thread import allocate_lock
from zlib import compress, decompress
from cs.serialise import toBS, fromBSfp

F_COMPRESSED = 0x01

class DataFlags(int):

  def __repr__(self):
    return "<DataFlags %d>" % (self,)

  def __str__(self):
    if self == 0:
      return '_'
    flags = self
    s = ''
    if flags & F_COMPRESSED:
      s += 'Z'
      flags &= ~F_COMPRESSED
    assert flags == 0
    return s

class DataFile(object):
  ''' A cs.venti data file, storing data chunks in compressed form.
  '''

  def __init__(self, pathname):
    self.pathname = pathname
    self._fp = None
    self._lock = allocate_lock()

  @property
  def fp(self):
    with self._lock:
      if self._fp is None:
        self._fp = open(self.pathname, "a+b")
    return self._fp

  def scanData(self, uncompress=False):
    ''' Scan the data file and yield (offset, flags, zdata) tuples.
        If `uncompress` is true, decompress the data and strip that flag value.
    '''
    fp = self.fp
    with self._lock:
      fp.seek(0)
      while True:
        offset = fp.tell()
        flags, data = self._readRawDataHere(fp)
        if flags is None:
          break
        if uncompress:
          if flags & F_COMPRESSED:
            data = decompress(data)
          flags &= ~F_COMPRESSED
        yield offset, flags, data

  def readData(self, offset):
    ''' Read data bytes from the supplied offset.
    '''
    fp = self.fp
    with self._lock:
      fp.seek(offset)
      flags, data = self._readRawDataHere(fp)
    assert flags is not None, "no data read from offset %d" % (offset,)
    if flags & F_COMPRESSED:
      data = decompress(data)
    return data

  def _readRawDataHere(self, fp):
    ''' Retrieve the data bytes stored at `offset`.
    '''
    flags = fromBSfp(fp)
    if flags is None:
      return None, None
    assert (flags & ~F_COMPRESSED) == 0, "flags other than F_COMPRESSED: 0x%02x" % ((flags & ~F_COMPRESSED),)
    flags = DataFlags(flags)
    dsize = fromBSfp(fp)
    if dsize == 0:
      data = ''
    else:
      assert dsize > 0, "expected dsize > 0, got dsize=%s" % (dsize,)
      data = fp.read(dsize)
    assert len(data) == dsize
    return flags, data

  def saveData(self, data, noCompress=False):
    ''' Append a chunk of data to the file, return the store offset.
    '''
    flags = 0
    if not noCompress:
      zdata = compress(data)
      if len(zdata) < len(data):
        data = zdata
        flags |= F_COMPRESSED
    fp = self.fp
    with self._lock:
      fp.seek(0, 2)
      offset = fp.tell()
      fp.write(toBS(flags))
      fp.write(toBS(len(data)))
      fp.write(data)
    return offset

  def flush(self):
    if self._fp:
      self._fp.flush()

  def close(self):
    with self._lock:
      if self._fp:
        self._fp.close()
        self._fp = None

if __name__ == '__main__':
  import cs.venti.datafile_tests
  cs.venti.datafile_tests.selftest(sys.argv)
