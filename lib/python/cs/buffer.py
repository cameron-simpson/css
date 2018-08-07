#!/usr/bin/python
#
# Functions associated with bytes, bytearrays, memoryviews and buffers in general.
# Also CornuCopyBuffer for managing a buffer and an input source.
#   - Cameron Simpson <cs@cskk.id.au> 18mar2017
#

''' Facilities to do with buffers, particularly CornuCopyBuffer,
    an automatically refilling buffer to support parsing of data streams.
'''

import os
from os import SEEK_SET, SEEK_CUR, SEEK_END
import mmap
from cs.py3 import pread

DISTINFO = {
    'keywords': ["python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
        "Development Status :: 5 - Production/Stable",
    ],
    'install_requires': ['cs.py3'],
}

DEFAULT_READSIZE = 131072

MEMORYVIEW_THRESHOLD = DEFAULT_READSIZE     # tweak if this gets larger

class CornuCopyBuffer(object):
  ''' An automatically refilling buffer intended to support parsing
      of data streams.

      Attributes:
      * `buf`: a buffer of unparsed data from the input, available
        for direct inspection by parsers
      * `offset`: the logical offset of the buffer; this excludes
        unconsumed input data and `.buf`

      The primary methods supporting parsing of data streams are
      extend() and take(). Calling `.extend(min_size)` arranges
      that `.buf` contains at least `min_size` bytes.  Calling `.take(size)`
      fetches exactly `size` bytes from `.buf` and the input source if
      necessary and returns them, adjusting `.buf`.

      len(CornuCopyBuffer) returns the length of `.buf`.

      bool(CornuCopyBuffer) tests whether len() > 0.

      Indexing a CornuCopyBuffer accesses `.buf`.

      A CornuCopyBuffer is also iterable, yielding data in whatever
      sizes come from its `input_data` source, preceeded by the
      current `.buf` if not empty.

      A CornuCopyBuffer also supports the file methods `.read`,
      `.tell` and `.seek` supporting drop in use of the buffer in
      many file contexts. Backward seeks are not supported. `.seek`
      will take advantage of the `input_data`'s .seek method if it
      has one, otherwise it will use reads.
  '''

  def __init__(
      self, input_data,
      buf=None, offset=0,
      copy_offsets=None, copy_chunks=None
  ):
    ''' Prepare the buffer.

        Parameters:
        * `input_data`: an iterable of data chunks (bytes instances);
          if your data source is a file see the .from_file factory;
          if your data source is a file descriptor see the .from_fd
          factory.
        * `buf`: if not None, the initial state of the parse buffer
        * `offset`: logical offset of the start of the buffer, default 0
        * `copy_offsets`: if not None, a callable for parsers to
          report pertinent offsets via the buffer's .report_offset
          method
        * `copy_chunks`: if not None, every fetched data chunk is
          copied to this callable
    '''
    if buf is None:
      buf = b''
    self.buf = buf
    self.offset = offset
    if copy_chunks is not None:
      input_data = CopyingIterator(input_data, copy_chunks)
    self.input_data = iter(input_data)
    self.copy_offsets = copy_offsets

  @classmethod
  def from_fd(cls, fd, readsize=None, offset=None, **kw):
    ''' Return a new CornuCopyBuffer attached to an open file descriptor.

        Internally this constructs a SeekableFDIterator, which
        provides the iteration that CornuCopyBuffer consumes, but
        also seek support of the underlying file descriptor is
        seekable.

        Parameters:
        * `fd`: the operation system file descriptor
        * `readsize`: an optional preferred read size
        * `offset`: a starting position for the data; the file
          descriptor will seek to this offset, and the buffer will
          start with this offset
        Other keyword arguments are passed to the buffer constructor.
    '''
    it = SeekableFDIterator(fd, readsize=readsize, offset=offset)
    return cls(it, offset=it.offset, **kw)

  @classmethod
  def from_file(cls, fp, readsize=None, offset=None, **kw):
    ''' Return a new CornuCopyBuffer attached to an open file.

        Internally this constructs a SeekableFileIterator, which
        provides the iteration that CornuCopyBuffer consumes, but
        also seek support of the underlying file is seekable.

        Parameters:
        * `fp`: the file like object
        * `readsize`: an optional preferred read size
        * `offset`: a starting position for the data; the file
          will seek to this offset, and the buffer will start with this
          offset
        Other keyword arguments are passed to the buffer constructor.
    '''
    it = SeekableFileIterator(fp, readsize=readsize, offset=offset)
    return cls(it, offset=it.offset, **kw)

  @classmethod
  def from_bytes(cls, bs, offset=0, length=None, **kw):
    ''' Return a CornuCopyBuffer fed from the supplied bytes `bs`.

        This is handy for callers parsing using buffers but handed bytes.

        Parameters:
        * `bs`: the bytes
        * `offset`: a starting position for the data; the input
          data will start this far into the bytes
        * `length`: the maximium number of bytes to use; the input
          data will be cropped this far past the starting point
        Other keyword arguments are passed to the buffer constructor.
    '''
    if offset < 0:
      raise ValueError("offset(%d) should be >= 0" % (offset,))
    if offset >= len(bs):
      raise ValueError(
          "offset(%d) beyond end of bs (%d bytes)"
          % (offset, len(bs)))
    if length is None:
      length = len(bs) - offset
    else:
      # sanity check supplied length
      if length < 1:
        raise ValueError("length(%d) < 1" % (length,))
    end_offset = offset + length
    if end_offset > len(bs):
      raise ValueError(
          "offset(%d)+length(%d) > len(bs):%d"
          % (offset, length, len(bs)))
    if offset > 0 or end_offset < len(bs):
      bs = memoryview(bs)[offset:end_offset]
    return cls([bs], **kw)

  def __str__(self):
    return "%s(offset:%d,buf:%d)" % (type(self).__name__, self.offset, len(self.buf))

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

  next = __next__

  def at_eof(self):
    ''' Test whether the buffer is at end of input.

        *Warning*: this will fetch from the `input_data` if the buffer
        is empty and so it may block.
    '''
    self.extend(1, short_ok=True)
    return len(self) == 0

  def report_offset(self, offset):
    ''' Report a pertinent offset.
    '''
    copy_offsets = self.copy_offsets
    if copy_offsets is not None:
      copy_offsets(offset)

  def hint(self, size):
    ''' Hint that the caller is seeking at least `size` bytes.

        If the input_data iterator has a `hint` method, this is
        passed to it.
    '''
    try:
      self.input_data.hint(size)
    except AttributeError:
      pass

  def extend(self, min_size, short_ok=False):
    ''' Extend the buffer to at least `min_size` bytes.

        If there are insufficient data available then an EOFError
        will be raised unless `short_ok` is true (default false)
        in which case the updated buffer will be short.
    '''
    if min_size < 1:
      raise ValueError("min_size(%r) must be >= 1" % (min_size,))
    length = len(self.buf)
    if length < min_size:
      self.hint(min_size - length)
      bufs = [self.buf]
      chunks = self.input_data
      while length < min_size:
        try:
          next_chunk = next(chunks)
        except StopIteration:
          if short_ok:
            break
          raise EOFError(
              "insufficient input data, wanted %d bytes but only found %d"
              % (min_size, length)
          )
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
      else:
        if len(bufs) == 1:
          newbuf = bufs[0]
        else:
          newbuf = b''.join(bufs)
        if len(newbuf) > MEMORYVIEW_THRESHOLD and isinstance(newbuf, bytes):
          newbuf = memoryview(newbuf)
      self.buf = newbuf

  def tail_extend(self, size):
    ''' Extend method for parsers reading "tail"-like chunk streams,
        typically raw reads from a growing file.

        This may read 0 bytes at EOF, but a future read may read
        more bytes if the file grows.
        Such an iterator can be obtained from
        ``cs.fileutils.read_from(..,tail_mode=True)``.
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
    size = len(taken)   # adjust for possible short fetch
    self.buf = buf[size:]
    self.offset += size
    return taken

  def read(self, size, one_fetch=False):
    ''' Compatibility method to allow using the buffer like a file.

        Parameters:
        * `size`: the desired data size
        * `one_fetch`: do a single data fetch, default False

        In `one_fetch` mode the read behaves like a POSIX file read,
        returning up to to `size` bytes from a single I/O operation.
    '''
    if size < 1:
      raise ValueError("size < 1: %r" % (size,))
    if one_fetch and size >= len(self):
      try:
        return next(self)
      except StopIteration:
        return b''
    try:
      return self.take(size)
    except ValueError as e:
      raise EOFError("insufficient data available: %s" % (e,))

  def tell(self):
    ''' Compatibility method to allow using the buffer like a file.
    '''
    return self.offset

  def seek(self, offset, whence=None, short_ok=False):
    ''' Compatibility method to allow using the buffer like a file.
        This returns the resulting absolute offset.

        Parameters are as for io.seek except as noted below:
        * `whence`: (default os.SEEK_SET). This method only supports
          os.SEEK_SET and os.SEEK_CUR, and does not support seeking to a
          lower offset than the current buffer offset.
        * `short_ok`: (default False). If true, the seek may not reach
          the target if there are insufficent `input_data` - the
          position will be the end of the `input_data`, and the
          `input_data` will have been consumed; the caller must check
          the returned offset to check that it is as expected. If
          false, a ValueError will be raised; however, note that the
          `input_data` will still have been consumed.
    '''
    if whence is None:
      whence = SEEK_SET
    elif whence == SEEK_SET:
      pass
    elif whence == SEEK_CUR:
      offset += self.offset
    else:
      raise ValueError(
          "seek: unsupported whence value %s, must be os.SEEK_SET or os.SEEK_CUR"
          % (whence,))
    if offset < self.offset:
      raise ValueError(
          "seek: target offset %s < buffer offset %s; may not seek backwards"
          % (offset, self.offset))
    if offset > self.offset:
      self.skipto(offset, short_ok=short_ok)
    return self.offset

  def skipto(self, new_offset, copy_skip=None, short_ok=False):
    ''' Advance to position `new_offset`. Return the new offset.

        Parameters:
        * `new_offset`: the target offset.
        * `copy_skip`: callable to receive skipped data.
        * `short_ok`: default False; f true then skipto may return before
          `new_offset` if there are insufficient `input_data`.

        Return values:
        * `buf`: the new state of `buf`
        * `offset`: the final offset; this may be short if `short_ok`.
    '''
    offset = self.offset
    if new_offset < offset:
      raise ValueError("skipto: new_offset:%d < offset:%d" % (new_offset, offset))
    return self.skip(new_offset - offset, copy_skip=copy_skip, short_ok=short_ok)

  def skip(self, toskip, copy_skip=None, short_ok=False):
    ''' Advance position by `skip_to`. Return the new offset.

        Parameters:
        * `toskip`: the distance to advance
        * `copy_skip`: callable to receive skipped data.
        * `short_ok`: default False; if true then skip may return before
          `skipto` bytes if there are insufficient `input_data`.

        Return values:
        * `buf`: the new state of `buf`
        * `offset`: the final offset; this may be short if `short_ok`.
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
          except StopIteration:
            if short_ok:
              break
            raise EOFError(
                "insufficient chunks: skipto:%d but only reached %d"
                % (new_offset, offset)
            )
          # TODO: an empty chunk from input_data indicates "not
          #   yet" from a nonblocking tailing file - some kind of
          #   delay needs to occur to avoid a spin.
          bufskip = min(len(buf), toskip)
          if bufskip > 0:
            if copy_skip:
              copy_skip(buf[:bufskip])
            buf = buf[bufskip:]
            toskip -= bufskip
            offset += bufskip
    self.buf = buf
    self.offset = offset

  def bounded(self, end_offset):
    ''' Return a new CornuCopyBuffer operating on a bounded view
        of this buffer.

        `end_offset`: the ending offset of the new buffer. Note
        that this is an absolute offset, not a length.

        This supports parsing of the buffer contents without risk
        of consuming past a certain point, such as the known end
        of a packet structure.

        The new buffer starts with the same offset as `self` and
        use of the new buffer affects `self`. After a flush both
        buffers will again have the same offset and the data consumed
        via the new buffer will also have been consumed from `self`.

        Here is an example.
        * Make a buffer `bfr` with 9 bytes of data in 3 chunks.
        * Consume 2 bytes, advancing the offset to 2.
        * Make a new bounded buffer `subbfr` extending to offset
          5. Its inital offset is also 2.
        * Iterate over it, yielding the remaining single byte chunk
          from ``b'abc'`` and then the first 2 bytes of ``b'def'``.
          The new buffer's offset is now 5.
        * Try to take 2 more bytes from the new buffer - this fails.
        * Flush the new buffer, synchronising with the original.
          The original's offset is now also 5.
        * Take 2 bytes from the original buffer, which succeeds.

        Example:

            >>> bfr = CornuCopyBuffer([b'abc', b'def', b'ghi'])
            >>> bfr.offset
            0
            >>> len(bfr.take(2))
            2
            >>> bfr.offset
            2
            >>> subbfr = bfr.bounded(5)
            >>> subbfr.offset
            2
            >>> for bs in subbfr:
            ...   print(len(bs))
            ...
            1
            2
            >>> subbfr.offset
            5
            >>> subbfr.take(2)
            Traceback (most recent call last):
                ...
            EOFError: insufficient input data, wanted 2 bytes but only found 0
            >>> subbfr.flush()
            >>> bfr.offset
            5
            >>> len(bfr.take(2))
            2

        *WARNING*: if the bounded buffer is not completely consumed
        then it is critical to call the new CornuCopyBuffer's `.flush`
        method to push any unconsumed buffer back into this buffer.
        Recommended practice is to always call `.flush` when finished
        with the new buffer.

        Also, because the new buffer may buffer some of the unconsumed
        data from this buffer, use of the original buffer should
        be suspended.
    '''
    bfr2 = CornuCopyBuffer(
        _BoundedBufferIterator(self, end_offset),
        offset=self.offset)
    def flush():
      ''' Flush the contents of bfr2.buf back into self.buf, adjusting
          the latter's offset accordingly.
      '''
      buf = bfr2.buf
      if buf:
        self.buf = buf + self.buf
        self.offset -= len(buf)
        bfr2.buf = b''
    bfr2.flush = flush
    return bfr2

class _BoundedBufferIterator(object):
  ''' An iterator over the data from a CornuCopyBuffer with an end
      offset bound.
  '''

  def __init__(self, bfr, end_offset):
    if end_offset < bfr.offset:
      raise ValueError(
          "end_offset(%d) < bfr.offset(%d)"
          % (end_offset, bfr.offset))
    self.bfr = bfr
    self.end_offset = end_offset

  def __iter__(self):
    return self

  def __next__(self):
    # WARNING: not thread safe at all!
    bfr = self.bfr
    limit = self.end_offset - bfr.offset
    if limit <= 0:
      raise StopIteration
    # post: limit > 0
    buf = next(bfr)
    # post: bfr.buf now emtpy, can be modified
    length = len(buf)
    if length <= limit:
      return buf
    head = buf[:limit]
    tail = buf[limit:]
    bfr.buf = tail
    bfr.offset -= len(tail)
    return head

  next = __next__

class CopyingIterator(object):
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
  ''' Decorator for a function accepting a leading CornuCopyBuffer
      parameter.
      Returns a function accepting a leading data chunks parameter
      (bytes instances) and optional `offset` and 'copy_offsets`
      keywords parameters.

      Example::

        @chunky
        def func(bfr, ...):
  '''
  def chunks_func(chunks, *a, **kw):
    ''' Function accepting chunk iterator.
    '''
    offset = kw.pop('offset', 0)
    copy_offsets = kw.pop('copy_offsets', None)
    bfr = CornuCopyBuffer(chunks, offset=offset, copy_offsets=copy_offsets)
    return bfr_func(bfr, *a, **kw)
  return chunks_func

class SeekableIterator(object):
  ''' A base class for iterators over seekable things.
  '''

  def __init__(self, offset=0, readsize=None, align=False):
    ''' Initialise the SeekableIterator.

        Parameters:
        * `offset`: the initial logical offset, kept up to date by
          iteration; default 0.
        * `readsize`: a preferred read/fetch size for iterators
          where that may be meaningful; if omitted then `DEFAULT_READSIZE`
          will be stored
        * `align`: whther to align reads/fetches by default: an
          iterator may choose to align fetches with multiples of
          `readsize`, doing a short fetch to bring the `offset`
          into alignment; the default is False
    '''
    if readsize is None:
      readsize = DEFAULT_READSIZE
    elif readsize < 1:
      raise ValueError("readsize must be >=1, got: %r" % (readsize,))
    if offset < 0:
      raise ValueError("offset must be >=0, got: %r" % (offset,))
    self.offset = offset
    self.readsize = readsize
    self.align = align
    self.next_hint = None

  def __del__(self):
    self.close()

  def close(self):
    ''' Close the iterator; required by subclasses.
    '''
    raise NotImplementedError("missing close method")

  def _fetch(self, readsize):
    raise NotImplementedError("no _fetch method in class %s" % (type(self),))

  def hint(self, size):
    ''' Hint that the next iteration is involved in obtaining at
        least `size` bytes.

        Some sources may take this into account when fetching their
        next data chunk. Users should keep in mind that the source
        may need to allocate at least this much memory if it chooses
        to satisfy the hint in full.
    '''
    self.next_hint = size

  def __iter__(self):
    return self

  def __next__(self):
    ''' Obtain more data from the iterator, honouring readsize, align and hint.
    '''
    readsize = self.readsize
    hint = self.next_hint
    if hint is None:
      if self.align:
        # trim the read to reach the next alignment point
        readsize -= self.offset % self.readsize
    else:
      # pad the read to the size of the hint
      readsize = max(readsize, hint)
    data = self._fetch(readsize)
    if not data:
      raise StopIteration("EOF, empty data received from fetch/read")
    length = len(data)
    self.offset += length
    if hint is not None:
      if hint > length:
        # trim the hint down
        hint -= length
      else:
        # hint consumed, clear it
        hint = None
      self.next_hint = hint
    return data

class SeekableFDIterator(SeekableIterator):
  ''' An iterator over the data of a seekable file descriptor.

      *Note*: the iterator works with an os.dup() of the file
      descriptor so that it can close it with impunity; this requires
      the caller to close their descriptor.
  '''

  def __init__(self, fd, offset=None, readsize=None, align=True):
    ''' Initialise the iterator.

        Parameters:
        * `fd`: file descriptor
        * `offset`: the initial logical offset, kept up to date by
          iteration; the default is the current file position.
        * `readsize`: a preferred read size; if omitted then
          `DEFAULT_READSIZE` will be stored
        * `align`: whther to align reads by default: if true then
          the iterator will do a short read to bring the `offset`
          into alignment with `readsize`; the default is True
    '''
    if offset is None:
      offset = os.lseek(fd, 0, SEEK_CUR)
    SeekableIterator.__init__(
        self,
        offset=offset, readsize=readsize, align=align)
    # dup the fd so that we can close it with impunity
    self.fd = os.dup(fd)

  def close(self):
    ''' Close the file descriptor.
    '''
    if self.fd is not None:
      os.close(self.fd)
      self.fd = None

  def _fetch(self, readsize):
    return pread(self.fd, readsize, self.offset)

  def seek(self, new_offset, mode=SEEK_SET):
    ''' Move the logical file pointer.
    '''
    if mode == SEEK_SET:
      pass
    elif mode == SEEK_CUR:
      new_offset += self.offset
    elif mode == SEEK_END:
      new_offset += os.fstat(self.fd).st_size
    self.offset = new_offset
    return new_offset

class SeekableFileIterator(SeekableIterator):
  ''' An iterator over the data of a file object.

      *Note*: the iterator closes the file on __del__ or if its
      .close method is called.
  '''

  def __init__(self, fp, offset=None, readsize=None, align=False):
    ''' Initialise the iterator.

        Parameters:
        * `fp`: file object
        * `offset`: the initial logical offset, kept up to date by
          iteration; the default is the current file position.
        * `readsize`: a preferred read size; if omitted then
          `DEFAULT_READSIZE` will be stored
        * `align`: whther to align reads by default: if true then
          the iterator will do a short read to bring the `offset`
          into alignment with `readsize`; the default is False
    '''
    if offset is None:
      offset = fp.tell()
    SeekableIterator.__init__(
        self,
        offset=offset, readsize=readsize, align=align)
    self.fp = fp
    # try to use the frugal read method if available
    try:
      read1 = fp.read1
    except AttributeError:
      read1 = fp.read
    self.read1 = read1

  def close(self):
    ''' Detach from the file and close it.
    '''
    if self.fp is not None:
      self.fp.close()
      self.fp = None

  def _fetch(self, readsize):
    return self.read1(readsize)

  def seek(self, new_offset, mode=SEEK_SET):
    ''' Move the logical file pointer.

        WARNING: moves the underlying file's pointer.
    '''
    new_offset = self.fp.seek(new_offset, mode)
    self.offset = new_offset
    return new_offset

class SeekableMMapIterator(SeekableIterator):
  ''' An iterator over the data of a mappable file descriptor.

      *Note*: the iterator works with an mmap of an os.dup() of the
      file descriptor so that it can close it with impunity; this
      requires the caller to close their descriptor.
  '''

  def __init__(self, fd, offset=None, readsize=None, align=True):
    ''' Initialise the iterator.

        Parameters:
        * `offset`: the initial logical offset, kept up to date by
          iteration; the default is the current file position.
        * `readsize`: a preferred read size; if omitted then
          `DEFAULT_READSIZE` will be stored
        * `align`: whther to align reads by default: if true then
          the iterator will do a short read to bring the `offset`
          into alignment with `readsize`; the default is True
    '''
    if offset is None:
      offset = os.lseek(fd, 0, SEEK_CUR)
    SeekableIterator.__init__(
        self,
        offset=offset, readsize=readsize, align=align)
    self.fd = os.dup(fd)
    self.mmap = mmap.mmap(self.fd, 0, flags=mmap.MAP_PRIVATE, prot=mmap.PROT_READ)
    self.mv = memoryview(mmap)

  def close(self):
    ''' Detach from the file descriptor and mmap and close.
    '''
    if self.fd is not None:
      self.mmap.close()
      self.mmap = None
      os.close(self.fd)
      self.fd = None

  def _fetch(self, readsize):
    submv = self.mv[self.offset:self.offset + readsize]
    self.offset += len(submv)
    return submv

  def seek(self, new_offset, mode=SEEK_SET):
    ''' Move the logical file pointer.
    '''
    if mode == SEEK_SET:
      pass
    elif mode == SEEK_CUR:
      new_offset += self.offset
    elif mode == SEEK_END:
      new_offset += os.fstat(self.fd).st_size
    self.offset = new_offset
    return new_offset
