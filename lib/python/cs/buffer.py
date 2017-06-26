#!/usr/bin/python
#
# Functions associated with bytes, bytearrays, memoryviews and buffers in general.
# Also CornuCopyBuffer for managing a buffer and an input source.
#   - Cameron Simpson <cs@zip.com.au> 18mar2017
#

import os

DISTINFO = {
    'description': "CornuCopyBuffer, an automatically refilling buffer intended to support parsing of data streams",
    'keywords': ["python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
        "Development Status :: 5 - Production/Stable",
        ],
    'install_requires': [],
}

class CornuCopyBuffer(object):
  ''' An automatically refilling buffer intended to support parsing of data streams.
  '''

  def __init__(self, input_data, buf=None, offset=0, copy_offsets=None, copy_chunks=None):
    ''' Prepare the buffer.
        `input_data`: an iterator yielding data chunks
        `buf`: if not None, the initial state of the parse buffer
        `offset`: logical offset of the start of the buffer, default 0
        `copy_offsets`: if not None, a callable for parsers to report pertinent offsets via the buffer's .report_offset method
        `copy_chunks`: if not None, every fetched data chunk is copied to this callable
    '''
    if buf is None:
      buf = b''
    self.buf = buf
    self.offset = offset
    if copy_chunks is not None:
      input_data = CopyingIterator(input_data, copy_chunks)
    self.input_data = input_data
    self.copy_offsets = copy_offsets

  def __len__(self):
    ''' The length is the length of the internal buffer: data available without a fetch.
    '''
    return len(self.buf)

  def __bool__(self):
    return len(self) > 0
  __nonzero__ = __bool__

  def __getitem__(self, index):
    ''' Fetch a byte form the internal buffer.
    '''
    return self.buf[index]

  def __iter__(self):
    return self

  def __next__(self):
    ''' Fetch a data chunk from the buffer.
    '''
    chunk = self.buf
    if chunk:
      self.buf = b''
    else:
      chunk = next(self.input_data)
    self.offset += len(chunk)
    return chunk

  def report_offset(self, offset):
    ''' Report a pertinent offset.
    '''
    copy_offsets = self.copy_offsets
    if copy_offsets is not None:
      copy_offsets(offset)

  def extend(self, min_size, short_ok=False):
    ''' Extend the buffer to at least `min_size` bytes.
        If there are insufficient data available then a ValueError
        will be raised unless `short_ok` is true (default false)
        in which case the updated buffer will be short.
    '''
    if min_size < 1:
      raise ValueError("min_size must be >= 1, got %r" % (min_size,))
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
        if next_chunk:
          # nonempty chunk, stash it
          bufs.append(next_chunk)
          length += len(next_chunk)
        elif short_ok:
          # this supports reading from a tail
          # which returns an empty chunk at the current EOF
          # but can continue iteration
          break
      if not bufs:
        newbuf = b''
      elif len(bufs) == 1:
        newbuf = bufs[0]
      else:
        newbuf = b''.join(bufs)
      self.buf = memoryview(newbuf)

  def tail_extend(self, size):
    ''' Extend method for parsers reading "tail"-like chunk streams,
        typically raw reads from a growing file. These may read 0 bytes
        at EOF, but a future read may read more bytes of the file grows.
        Such an iterator can be obtained from
        cs.fileutils.read_from(..,tail_mode=True).
    '''
    while size < len(self):
      self.extend(size, short_ok=True)

  def take(self, size, short_ok=False):
    ''' Return the next `size` bytes.
        Other arguments are as for extend().
    '''
    self.extend(size, short_ok=short_ok)
    buf = self.buf
    taken = buf[:size]
    self.buf = buf[size:]
    self.offset += size
    return taken

  def read(self, size, one_fetch=False):
    ''' Compatibility method to allow using the buffer like a file.
        `size`: the desired data size
        `one_fetch`: do a single data fetch, default False
        In `one_fetch` mode the read behaves
    '''
    if one_fetch and size >= len(self):
      return next(self)
    return self.take(size)

  def tell(self):
    ''' Compatibility method to allow using the buffer like a file.
    '''
    return self.offset

  def seek(self, offset, whence=None, short_ok=False):
    ''' Compatibility method to allow using the buffer like a file.
        This returns the resulting absolute offset.
        Parameters are as for io.seek except as noted below:
        `whence`: (default os.SEEK_SET). This method only supports
          os.SEEK_SET and os.SEEK_CUR, and does not support seeking to a
          lower offset than the current buffer offset.
        `short_ok`: (default False). If true, the seek may not reach
          the target if there are insufficent `input_data` - the
          position will be the end of the `input_data`, and the
          `input_data` will have been consumed; the caller must check
          the returned offset to check that it is as expected. If
          false, a ValueError will be raised; however, note that the
          `input_data` will still have been consumed.
    '''
    if whence is None:
      whence = os.SEEK_SET
    elif whence == os.SEEK_SET:
      pass
    elif whence == os.SEEK_CUR:
      offset += self.offset
    else:
      raise ValueError("seek: unsupported whence value %s, must be os.SEEK_SET or os.SEEK_CUR"
                       % (whence,))
    if offset < self.offset:
      raise ValueError("seek: target offset %s < buffer offset %s; may not seek backwards"
                       % (offset, self.offset))
    if offset > self.offset:
      self.skipto(offset, short_ok=short_ok)
    return self.offset

  def skipto(self, new_offset, copy_skip=None, short_ok=False):
    ''' Advance to position `new_offset`. Return the new offset.
        `new_offset`: the target offset.
        `copy_skip`: callable to receive skipped data.
        `short_ok`: default False; f true then skipto may return before
          `new_offset` if there are insufficient `input_data`.
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
          `new_offset` if there are insufficient `input_data`.
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
      new_offset = offset + toskip
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

def CopyingIterator(object):
  ''' Wrapper for an iterator that copies every item retrieved to a callable.
  '''
  def __init__(self, I, copy_to):
    ''' Initialise with the iterator `I` and the callable `copy_to`.
    '''
    self.I = I
    self.copy_to = copy_to
  def __iter__(self):
    return self
  def __next__(self):
    item = next(self.I)
    self.copy_to(item)
    return item

def chunky(bfr_func):
  ''' Decorator for a function acceptig a leading CornuCopyBuffer parameter. Returns a function accepting a leading data `chunks` parameter and optional `offset` and 'copy_offsets` keywords parameters.

      @chunky
      def func(bfr, ...):
  '''
  def chunks_func(chunks, *a, offset=0, copy_offsets=None, **kw):
    bfr = CornuCopyBuffer(chunks, offset=offset, copy_offsets=copy_offsets)
    return bfr_func(bfr, *a, **kw)
  return chunks_func
