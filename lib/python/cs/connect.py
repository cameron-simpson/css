#!/usr/bin/python
#
# Facilities for connections.
#       - Cameron Simpson <cs@zip.com.au> 06oct2012
#

from collections import namedtuple
from tempfile import TemporaryFile
from cs.logutils import warning, error, D
from cs.misc import O

_BufferFile = namedtuple('_BufferFile', ('file', 'offset', 'len'))

class DataQueue(O):
  ''' An object to hold queued data, with a notion of what has been sent.
  '''

  def __init__(self):
    self.closed = False
    self.low = 0       # data delivered
    self.high = 0      # data queued
    self.buffers = []  # array of temporary files holding unsent queued data
    O.__init__(self)

  def _newbuffer(self):
    ''' Allocate a new buffer file and store it on the end of self.buffers.
    '''
    BF = _BufferFile(TemporaryFile(dir=tmpdir()), self.high, 0)
    self.buffers.append(BF)
    return BF

  def close(self):
    if self.closed:
      warning("%s.close(): already closed", self)
    else:
      self.closed = True
    if self.low < self.high:
      warning("%s.close(): self.low(%s) < self.high(%s)", self, self.low, self.high)
    for BF in self.buffers:
      if BF.offset < BF.len:
        warning("%s.close(): non-empty buffer: %s", self, BF)
      BF.file.close()
    self.buffers = None

  def sent(self, low):
    if low <= self.low:
      raise ValueError("sent(low=%s): lower than self.low(%s), ignored" % (low, self.low))
    if low > self.high:
      raise ValueError("sent(low=%s): greater than self.high(%s), ignored" % (low, self.high))

    # flush buffer files whose data have been sent
    while self.buffers:
      BF = self.buffers[0]
      if BF.offset + BF.len > low:
        break
      BF.close()
      self.buffers.pop(0)

    # adjust low water mark
    self.low = low

if __name__ == '__main__':
  import sys
  import cs.connect_tests
  cs.connect_tests.selftest(sys.argv)
