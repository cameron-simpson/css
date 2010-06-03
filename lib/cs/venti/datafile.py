#!/usr/bin/python -tt

from zlib import compress, decompress
from thread import allocate_lock
from cs.serialise import toBS, fromBSfp

F_COMPRESSED = 0x01

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

  def scanData(self):
    ''' Scan the data file and yield (offset, data) tuples.
    '''
    fp = self.fp
    with self._lock:
      fp.seek(0)
      while True:
        offset = fp.tell()
        flags, data = self._readRawDataHere(fp)
        if flags & F_COMPRESSED:
          data = decompress(data)
        assert (flags & ~F_COMPRESSED) == 0
        yield offset, data

  def readData(self, offset):
    ''' Read data bytes from the supplied offset.
    '''
    fp = self.fp
    with self._lock:
      fp.seek(offset)
      flags, data = self._readRawDataHere(fp)
    if flags & F_COMPRESSED:
      data = decompress(data)
    assert (flags & ~F_COMPRESSED) == 0
    return data

  def _readRawDataHere(self, fp):
    ''' Retrieve the data bytes stored at `offset`.
    '''
    flags = fromBSfp(fp)
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
      fp.seek(0,2)
      offset = fp.tell()
      fp.write(toBS(flags))
      fp.write(toBS(len(data)))
      fp.write(data)
    return offset

  def flush(self):
    if self._fp:
      self._fp.flush()
