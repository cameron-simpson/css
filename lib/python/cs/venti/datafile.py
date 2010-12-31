#!/usr/bin/python -tt

from thread import allocate_lock
from zlib import compress, decompress
import unittest
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

class TestAll(unittest.TestCase):

  def setUp(self):
    import os
    import random
    import tempfile
    tfd, pathname = tempfile.mkstemp(prefix="cs.venti.datafile.test", suffix=".vtd", dir='.')
    os.close(tfd)
    self.pathname = pathname
    self.data = DataFile(pathname)
    random.seed()

  def tearDown(self):
    import os
    self.data.close()
    os.remove(self.pathname)

  # TODO: tests:
  #   scan datafile

  def _genblock(self, maxsize=16383):
    import os
    import random
    return os.urandom(random.randint(0, maxsize))

  def test00store1(self):
    ''' Save a single block.
    '''
    self.data.saveData(self._genblock())

  def test01fetch1(self):
    ''' Save and the retrieve a single block.
    '''
    self.data.saveData(self._genblock())
    self.data.close()
    self.data.readData(0)

  def test02randomblocks(self):
    ''' Save 100 random blocks, close, retrieve in random order.
    '''
    import random
    blocks = {}
    for _ in range(100):
      data = self._genblock()
      offset = self.data.saveData(data)
      blocks[offset] = data
    self.data.close()
    offsets = blocks.keys()
    random.shuffle(offsets)
    for offset in offsets:
      data = self.data.readData(offset)
      self.assertTrue(data == blocks[offset])

if __name__ == '__main__':
  unittest.main()
