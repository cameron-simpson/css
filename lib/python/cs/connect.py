#!/usr/bin/python
#
# Facilities for connections.
#       - Cameron Simpson <cs@zip.com.au> 06oct2012
#

from collections import namedtuple
from tempfile import TemporaryFile
from threading import Lock, Thread
from cs.logutils import warning, error, info, D, setup_logging
from cs.threads import Channel
from cs.misc import O, tmpdir

class _BufferFile(O):
  ''' A buffer to store data from 
  '''

  def __init__(self, offset):
    self._file = TemporaryFile(dir=tmpdir())
    self.offset = offset
    self.size = 0
    self._lock = Lock()

  def append(self, data):
    with self._lock:
      self._file.seek(self.size)
      self._file.write(data)
      self.size += len(data)

  def read(self, where, size):
    ''' Read up to `size` bytes starting at `where`.
        DO not forget that the _BufferFile starts at self.offset.
    '''
    with self._lock:
      if where < self.offset or size < 0 or where + size > self.size:
        raise ValueError("%s.read(where=%s, size=%s): out of range" % (self, where, size))
      where -= self.offset
      self._file.seek(where)
      data = b''
      while size > 0:
        r = self._file.read(size)
        if len(r) == 0:
          break
        data += r
        size -= len(r)
      return data

  def close(self):
    self._file.close()
    self._file = None

class DataQueue(O):
  ''' An object to hold queued data, with a notion of what has been sent.
  '''

  def __init__(self):
    self.closed = False
    self.low = 0       # data delivered
    self.high = 0      # data queued
    self.buffers = []  # array of temporary files holding unsent queued data
    self._buffull = 1024 * 1024
    self._bufmax = 2 * self._buffull
    self._lock = Lock()
    self._writeQ = Channel()
    self._write_lock = Lock()
    self._write_handler = Thread(target=self._writer, name="_writer")
    self._write_handler.daemon = True
    self._write_handler.start()
    O.__init__(self)

  @property
  def size(self):
    return self.high - self.low

  @property
  def writable(self):
    return self.size < self._bufmax

  def _new_buffer(self):
    ''' Allocate a new buffer file and store it on the end of self.buffers.
    '''
    BF = _BufferFile(self.high)
    self.buffers.append(BF)
    return BF

  def _buffer_data(self, data):
    if not self.buffers:
      BF = self._new_buffer()
    else:
      BF = self.buffers[-1]
      if BF.size + len(data) > self._buffull:
        BF = self._new_buffer()
    BF.append(data)
    self.high += len(data)

  def close(self):
    if self.closed:
      warning("%s.close(): already closed", self)
    else:
      self.closed = True
    if self.low < self.high:
      warning("%s.close(): UNUSED DATA: self.low(%s) < self.high(%s)", self, self.low, self.high)
    for BF in self.buffers:
      if BF.offset < BF.size:
        warning("%s.close(): discarding non-empty buffer: %s", self, BF)
      BF.close()
    self.buffers = None

  def upto(self, low):
    ''' Advance the record of what has been sent, releasing buffers as needed.
        This will release self._write_lock if this makes the queue writable.
    '''
    with self._lock:
      if low < self.low:
        raise ValueError("upto(low=%s): lower than self.low(%s), ignored" % (low, self.low))
      if low > self.high:
        raise ValueError("upto(low=%s): greater than self.high(%s), ignored" % (low, self.high))
      # flush buffer files whose data have been sent
      while self.buffers:
        BF = self.buffers[0]
        if BF.offset + BF.size > low:
          break
        BF.close()
        self.buffers.pop(0)
      # adjust low water mark
      was_writable = self.writable
      self.low = low
      if not was_writable:
        if self.writable:
          self._write_lock.release()

  def write(self, data):
    ''' Queue data to be sent.
        Will block when the _writer daemon thread is blocked.
    '''
    if self.closed:
      raise ValueError("%s.write(): closed" % (self,))
    self._writeQ.put(data)

  def _writer(self):
    ''' Daemon thread to accept data for writing.
	For flow control we use a Channel to accept .write() requests,
	and only .get() the Channel inside the writeLock; after
	accepting data we only release the write_lock if we are under
	our high water mark (self._bufmax).
    '''
    writeQ = self._writeQ
    write_lock = self._write_lock
    while not self.closed:
      write_lock.acquire()
      if self.closed:
        break
      data = writeQ.get()
      if self.closed:
        break
      with self._lock:
        self._buffer_data(data)
        if self.writable:
          # allow more writes
          write_lock.release()

  def read(self, size):
    ''' Obtain up to `size` bytes from the current low water mark.
        Return the available bytes.
    '''
    if size < 0:
      raise ValueError("%s.read(size=%s): size less than 0", self, size)
    # short circuit if the DataQueue is empty
    if self.size == 0:
      return b''
    BF = self.buffers[0]
    used = self.low - BF.offset
    assert used >= 0
    size = min(size, BF.size - used)
    return BF.read(self.low, size)

if __name__ == '__main__':
  import sys
  import cs.connect_tests
  setup_logging(sys.argv[0])
  cs.connect_tests.selftest(sys.argv)
