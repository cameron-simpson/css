#!/usr/bin/python
#
# Functions associated with bytes, bytearrays, memoryviews and buffers in general.
# Also functions for managing a buffer and an input source.
#   - Cameron Simpson <cs@zip.com.au> 18mar2017
#

class CornuCopyBuffer(object):
  ''' An automatically refilling buffer intended to support parsing of data streams.
  '''

  def __init__(self, input_data, buf=None, offset=0):
    if buf is None:
      buf = b''
    self.buf = buf
    self.offset = offset
    self.input_data = input_data

  def __len__(self):
    return len(self.buf)

  def __bool__(self):
    return len(self) > 0
  __nonzero__ = __bool__

  def __getitem__(self, index):
    return self.buf[index]

  def extend(self, min_size, copy_chunks=None, short_ok=False):
    ''' Extend the buffer to at least `min_size` bytes.
        If there are insufficient data available then a ValueError
        will be raised unless `short_ok` is true (default false)
        in which case the updated buffer will be short.
        If `copy_chunks` is not None, pass every new data chunk to `copy_chunks`.
    '''
    length = len(self.buf)
    if length < min_size:
      bufs = [self.buf]
      chunks = self.input_data
      while length < min_size:
        try:
          next_chunk = next(chunks)
        except StopIteration as e:
          if short_ok:
            break
          raise ValueError("insufficient chunks, wanted %d but only found %d"
                           % (min_size, length)) from e
        if copy_chunks:
          copy_chunks(next_chunk)
        bufs.append(next_chunk)
        length += len(next_chunk)
      self.buf = memoryview(b''.join(bufs))

  def take(self, size, copy_chunks=None, short_ok=False):
    ''' Return the next `size` bytes.
        Other arguments are as for extend().
    '''
    self.extend(size, copy_chunks=copy_chunks, short_ok=short_ok)
    buf = self.buf
    taken = buf[:size]
    self.buf = buf[size:]
    self.offset += size
    return taken

  def skipto(self, new_offset, copy_skip=None, short_ok=False):
    ''' Advance to position `new_offset`. Return the new offset.
        `new_offset`: the target offset.
        `copy_skip`: callable to receive skipped data.
        `short_ok`: default False; f true then skipto may return before
          `new_offset` if there are insufficient chunks.
        Return values:
        `buf`: the new state of `buf`
        `offset`: the final offset; this may be short if `short_ok`.
    '''
    offset = self.offset
    if new_offset < offset:
      raise ValueError("skipto: new_offset:%d < offset:%d" % (new_offset, offset))
    return self.skip(new_offset - offset, copy_skip=copy_skip, short_ok=short_ok)

  def skip(self, toskip, copy_skip=None, short_ok=False):
    ''' Advance position by `skip_to`. Return the new offset.
        `skipto`: the distance to advance
        `copy_skip`: callable to receive skipped data.
        `short_ok`: default False; f true then skipto may return before
          `new_offset` if there are insufficient chunks.
        Return values:
        `buf`: the new state of `buf`
        `offset`: the final offset; this may be short if `short_ok`.
    '''
    # consume any bytes in buf before new_offset
    buf = self.buf
    offset = self.offset
    bufskip = min(len(buf), toskip)
    if bufskip > 0:
      if copy_skip:
        copy_skip(buf[:bufskip])
      buf = buf[bufskip:]
      toskip -= bufskip
      offset += bufskip
    if toskip > 0:
      # advance the rest of the way
      seek = None
      chunks = self.input_data
      if copy_skip is None:
        try:
          seek = chunks.seek
        except AttributeError:
          pass
      if seek:
        # seek directly to new_offset
        seek(new_offset)
        offset = new_offset
      else:
        # no seek, consume chunks until new_offset
        while toskip > 0:
          try:
            buf = next(chunks)
          except StopIteration as e:
            if short_ok:
              break
            raise ValueError("insufficient chunks: skipto:%d but only reached %d"
                             % (new_offset, offset)) from e
          bufskip = min(len(buf), toskip)
          if bufskip > 0:
            if copy_skip:
              copy_skip(buf[:bufskip])
            buf = buf[bufskip:]
            toskip -= bufskip
            offset += bufskip
    self.buf = buf
    self.offset = offset
