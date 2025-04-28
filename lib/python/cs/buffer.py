#!/usr/bin/python
#
# Functions associated with bytes, bytearrays, memoryviews and buffers in general.
# Also CornuCopyBuffer for managing a buffer and an input source.
#   - Cameron Simpson <cs@cskk.id.au> 18mar2017
#
# pylint: disable=too-many-lines
#

''' Facilities to do with buffers, particularly CornuCopyBuffer,
    an automatically refilling buffer to support parsing of data streams.
'''

from contextlib import contextmanager
import os
from os import fstat, pread, SEEK_SET, SEEK_CUR, SEEK_END
import mmap
from stat import S_ISREG
import sys
from threading import Lock, Thread
from typing import List

from cs.deco import Promotable
from cs.gimmicks import Buffer, r

__version__ = '20250428'

DISTINFO = {
    'keywords': ["python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
        "Development Status :: 5 - Production/Stable",
    ],
    'requires_python':
    '>=3.3',  # for os.pread
    'install_requires': [
        'cs.deco',
        'cs.gimmicks',
    ],
}

DEFAULT_READSIZE = 131072

MEMORYVIEW_THRESHOLD = DEFAULT_READSIZE  # tweak if this gets larger

# pylint: disable=too-many-public-methods,too-many-instance-attributes
class CornuCopyBuffer(Promotable):
  ''' An automatically refilling buffer intended to support parsing
      of data streams.

      Its primary purpose is to aid binary parsers
      which do not themselves need to handle sources specially;
      `CornuCopyBuffer`s are trivially made from `bytes`,
      iterables of `bytes` and file-like objects.
      See `cs.binary` for convenient parsing classes
      which work with `CornuCopyBuffer`s.

      A `CornuCopyBuffer` is iterable, yielding data in whatever
      sizes come from its `input_data` source, preceeded by any
      content in the internal buffer.

      A `CornuCopyBuffer` also supports the file methods `.read`,
      `.tell` and `.seek` supporting drop in use of the buffer in
      many file contexts. Backward seeks are not supported. `.seek`
      will take advantage of the `input_data`'s `.seek` method if it
      has one, otherwise it will use consume the `input_data`
      as required.

      Attributes:
      * `buf`: the first of any buffered leading chunks
        buffer of unparsed data from the input, available
        for direct inspection by parsers;
        normally however parsers will use `.extend` and `.take`.
      * `offset`: the logical offset of the buffer; this excludes
        buffered data and unconsumed input data

      *Note*: the initialiser may supply a cleanup function;
      although this will be called via the buffer's `.__del__` method
      a prudent user of a buffer should call the `.close()` method
      when finished with the buffer to ensure prompt cleanup;
      the `contextlib.closing` context manager provides an easy way
      to do this in common cases.

      The primary methods supporting parsing of data streams are
      `.extend()` and `take()`.
      Calling `.extend(min_size)` arranges that the internal buffer
      contains at least `min_size` bytes.
      Calling `.take(size)` fetches exactly `size` bytes from the
      internal buffer and the input source if necessary and returns
      them, adjusting the internal buffer.

      len(`CornuCopyBuffer`) returns the length of any buffered data.

      bool(`CornuCopyBuffer`) tests whether len() > 0.

      Indexing a `CornuCopyBuffer` accesses the buffered data only,
      returning an individual byte's value (an `int`).
  '''

  # pylint: disable=too-many-arguments
  def __init__(
      self,
      input_data,
      buf=None,
      offset=0,
      seekable=None,
      copy_offsets=None,
      copy_chunks=None,
      close=None,
      progress=None,
      final_offset=None,
  ):
    ''' Prepare the buffer.

        Parameters:
        * `input_data`: an iterable of data chunks (`bytes`-like instances);
          if your data source is a file see the `.from_file` factory;
          if your data source is a file descriptor see the `.from_fd`
          factory.
        * `buf`: if not `None`, the initial state of the parse buffer
        * `offset`: logical offset of the start of the buffer, default `0`
        * `seekable`: whether `input_data` has a working `.seek` method;
          the default is `None` meaning that it will be attempted on
          the first skip or seek
        * `copy_offsets`: if not `None`, a callable for parsers to
          report pertinent offsets via the buffer's `.report_offset`
          method
        * `copy_chunks`: if not `None`, every fetched data chunk is
          copied to this callable

        The `input_data` is an iterable whose iterator may have
        some optional additional properties:
        * `seek`: if present, this is a seek method after the fashion
          of `file.seek`; the buffer's `seek`, `skip` and `skipto`
          methods will take advantage of this if available.
        * `offset`: the current byte offset of the iterator; this
          is used during the buffer initialisation to compute
          `input_data_displacement`, the difference between the
          buffer's logical offset and the input data iterable's logical offset;
          if unavailable during initialisation this is presumed to
          be `0`.
        * `end_offset`: the end offset of the iterator if known.
        * `close`: an optional callable
          that may be provided for resource cleanup
          when the user of the buffer calls its `.close()` method.
        * `progress`: an optional `cs.Progress.progress` instance
          to which to report data consumed from `input_data`;
          any object supporting `+=` is acceptable
        * `final_offset`: optional `int` specifying the largest
          offset expected to be reached, intended for uses such as
          callers presenting a pregress indication; this is, for
          example, provided by `CornuCopyBuffer.from_fd` for regular
          files using the `stat.st_size` field
    '''
    self.bufs = []
    if buf is None or not buf:
      self.buflen = 0
    else:
      self.bufs.append(buf)
      self.buflen = len(buf)
    self.offset = offset
    self.seekable = seekable
    input_data = iter(input_data)
    if copy_chunks is not None:
      input_data = CopyingIterator(input_data, copy_chunks)
    self.input_data = input_data
    self.copy_offsets = copy_offsets
    # Try to compute the displacement between the input_data byte
    # offset and the buffer's logical offset.
    # NOTE: if the input_data iterator does not have a .offset
    # attribute then we assume the iterator byte offset is 0, purely
    # to reduce the burden on iterator implementors.
    input_offset = getattr(input_data, 'offset', 0)
    self.input_offset_displacement = input_offset - offset
    self._close = close
    self.progress = progress
    self.final_offset = final_offset

  def selfcheck(self, msg=''):
    ''' Integrity check for the buffer, useful during debugging.
    '''
    msgpfx = f'{self.__class__.__name__}.selfcheck'
    if msg:
      msgpfx += ': ' + msg
    msgpfx += f', buflen={self.buflen}, bufs={[len(buf) for buf in self.bufs]}'
    assert self.buflen == sum(
        len(buf) for buf in self.bufs
    ), msgpfx + ": self.buflen != sum of .bufs"
    assert all(
        len(buf) > 0 for buf in self.bufs
    ), msgpfx + ": not all .bufs are nonempty"

  @property
  def buf(self):
    ''' The first buffer, or `b''` if nothing is buffered.
    '''
    try:
      return self.bufs[0]
    except IndexError:
      return b''

  def close(self):
    ''' Close the buffer.
        This discards the internal buffer of "read but not consumed" data
        and calls the `close` callable supplied when the buffer was
        initialised, if any.
    '''
    self.bufs = None
    if self._close:
      self._close()
      self._close = None

  def __del__(self):
    ''' Release resources when the object is deleted.
    '''
    self.close()

  @staticmethod
  def _stat_final_offset(f):
    ''' Return the `final_offset` value from a file descriptor or file,
        or `None` if it cannot be determined from `os.fstat()`.
    '''
    if isinstance(f, int):
      st = fstat(f)
    else:
      try:
        fd = f.fileno()
      except AttributeError:
        return None
      if fd is None:
        return None
      st = fstat(fd)
    if not S_ISREG(st.st_mode):
      return None
    return st.st_size

  @classmethod
  def from_fd(cls, fd, readsize=None, offset=None, final_offset=None, **kw):
    ''' Return a new `CornuCopyBuffer` attached to an open file descriptor.

        Internally this constructs a `SeekableFDIterator` for regular
        files or an `FDIterator` for other files, which provides the
        iteration that `CornuCopyBuffer` consumes, but also seek
        support if the underlying file descriptor is seekable.

        Parameters:
        * `fd`: the operating system file descriptor
        * `readsize`: an optional preferred read size
        * `offset`: a starting position for the data; the file
          descriptor will seek to this offset, and the buffer will
          start with this offset
        Other keyword arguments are passed to the buffer constructor.
    '''
    st = fstat(fd)
    if S_ISREG(st.st_mode):
      if final_offset is None:
        final_offset = st.st_size
      it = SeekableFDIterator(fd, readsize=readsize, offset=offset)
    else:
      it = FDIterator(fd, readsize=readsize, offset=offset)
    return cls(
        it,
        close=it.close,
        offset=it.offset,
        final_offset=final_offset,
        **kw,
    )

  def as_fd(self, maxlength=Ellipsis):
    ''' Create a pipe and dispatch a `Thread` to copy
        up to `maxlength` bytes from `bfr` into it.
        Return the file descriptor of the read end of the pipe.

        The default `maxlength` is `Ellipsis`, meaning to copy all data.

        Note that the thread preemptively consumes from the buffer.

        This is useful for passing buffer data to subprocesses.
    '''
    rfd, wfd = os.pipe()

    def copy_buffer():
      ''' Copy data from the buffer to `wfd`,
          closing `wfd` when finished.
      '''
      try:
        for bs in self.iter(maxlength):
          while bs:
            try:
              nbs = os.write(wfd, bs)
            except OSError:
              # rebuffer uncopied data and reraise
              self.push(bs)
              raise
            bs = bs[nbs:]
      finally:
        os.close(wfd)

    Thread(
        name=f'{self}.copy_to_fd:{wfd=}:{rfd=}',
        target=copy_buffer,
    ).start()
    return rfd

  @classmethod
  def from_mmap(cls, fd, readsize=None, offset=None, **kw):
    ''' Return a new `CornuCopyBuffer` attached to an mmap of an open
        file descriptor.

        Internally this constructs a `SeekableMMapIterator`, which
        provides the iteration that `CornuCopyBuffer` consumes, but
        also seek support.

        Parameters:
        * `fd`: the operating system file descriptor
        * `readsize`: an optional preferred read size
        * `offset`: a starting position for the data; the file
          descriptor will seek to this offset, and the buffer will
          start with this offset
        Other keyword arguments are passed to the buffer constructor.
    '''
    it = SeekableMMapIterator(fd, readsize=readsize, offset=offset)
    return cls(
        it,
        offset=it.offset,
        close=it.close,
        final_offset=it.end_offset,
        **kw,
    )

  @classmethod
  def from_file(cls, f, readsize=None, offset=None, final_offset=None, **kw):
    ''' Return a new `CornuCopyBuffer` attached to an open file.

        Internally this constructs a `SeekableFileIterator`, which
        provides the iteration that `CornuCopyBuffer` consumes
        and also seek support if the underlying file is seekable.

        Parameters:
        * `f`: the file like object
        * `readsize`: an optional preferred read size
        * `offset`: a starting position for the data; the file
          will seek to this offset, and the buffer will start with this
          offset
        Other keyword arguments are passed to the buffer constructor.
    '''
    try:
      ftell = f.tell
    except AttributeError:
      is_seekable = False
      foffset = None
    else:
      try:
        foffset = ftell()
      except OSError:
        is_seekable = False
        foffset = None
      else:
        is_seekable = True
    if offset is None:
      offset = foffset
    if final_offset is None:
      final_offset = cls._stat_final_offset(f)
    it = (
        SeekableFileIterator(f, readsize=readsize, offset=offset)
        if is_seekable else FileIterator(f, readsize=readsize, offset=offset)
    )
    return cls(it, offset=it.offset, final_offset=final_offset, **kw)

  @classmethod
  def from_filename(cls, filename: str, offset=None, final_offset=None, **kw):
    ''' Open the file named `filename` and return a new `CornuCopyBuffer`.

        If `offset` is provided, skip to that position in the file.
        A negative offset skips to a position that far from the end of the file
        as determined by its `Stat.st_size`.

        Other keyword arguments are passed to the buffer constructor.
    '''
    f = open(filename, 'rb')  # pylint: disable=consider-using-with
    if final_offset is None:
      final_offset = cls._stat_final_offset(f)
    bfr = cls.from_file(f, close=f.close, final_offset=final_offset, **kw)
    if offset is not None:
      if offset < 0:
        S = os.fstat(f.fileno())
        offset2 = S.st_size + offset
        if offset2 < 0:
          raise ValueError(
              "offset %s is too far from the end of the file (st_size=%s)" %
              (offset, S.st_size),
          )
      bfr.skipto(offset)
    return bfr

  @classmethod
  def from_cli_filespec(cls, filespec: str, **kw):
    ''' Return a `CornuCopyBuffer` fed from the supplied command
        line file specification `filespec`.

        If `filespec` is `"-"` return a buffer using `sys.stdin`,
        otherwise treat it as a filename.

        Note: the use of `sys.stdin` relies on `sys.stdin.fileno()`
        because we need to do binary reads and `sys.stdin` is
        normally in text mode.
    '''
    if filespec == '-':
      return cls.from_fd(sys.stdin.fileno(), **kw)
    return cls.from_filename(filespec, **kw)

  @classmethod
  def from_bytes(cls, bs, offset=0, length=None, **kw):
    ''' Return a `CornuCopyBuffer` fed from the supplied bytes `bs`
        starting at `offset` and ending after `length`.

        This is handy for callers parsing using buffers but handed bytes.

        Parameters:
        * `bs`: the bytes
        * `offset`: a starting position for the data; the input
          data will start this far into the bytes
        * `length`: the maximium number of bytes to use; the input
          data will be cropped this far past the starting point;
          default: the number of bytes in `bs` after `offset`
        Other keyword arguments are passed to the buffer constructor.
    '''
    if offset < 0:
      raise ValueError(f'{offset=} should be >= 0')
    if offset > len(bs):
      raise ValueError(f'{offset=} beyond end of bs ({len(bs)} bytes)')
    if length is None:
      length = len(bs) - offset
    # sanity check supplied length
    elif length < 1:
      raise ValueError(f'{length=} < 1')
    end_offset = offset + length
    if end_offset > len(bs):
      raise ValueError(f'{offset=}+{length=} > {len(bs)=}')
    bs = memoryview(bs)
    if offset > 0 or end_offset < len(bs):
      bs = bs[offset:end_offset]
    return cls([bs], offset=offset, final_offset=end_offset, **kw)

  def __str__(self):
    return f'{self.__class__.__name__}(offset:{self.offset}:{self.final_offset},buf:{self.buflen}'

  def __len__(self):
    ''' The length is the length of the internal buffer: data available without a fetch.
    '''
    return self.buflen

  def __bool__(self):
    return len(self) > 0

  __nonzero__ = __bool__

  def __getitem__(self, index):
    ''' Fetch from the internal buffer.
        This does not consume data from the internal buffer.
        Note that this is an expensive way to access the buffer,
        particularly if `index` is a slice.

        If `index` is a `slice`, slice the join of the internal subbuffers.
        This is quite expensive
        and it is probably better to `take` or `takev`
        some data from the buffer.

        Otherwise `index` should be an `int` and the corresponding
        buffered byte is returned.

        This is usually not a very useful method;
        its primary use case is to probe the buffer to make a parsing decision
        instead of taking a byte off and (possibly) pushing it back.
    '''
    if isinstance(index, slice):
      # slice the joined up bufs - expensive
      return b''.join(self.bufs)[index]
    index0 = index
    if index < 0:
      index = self.buflen - index
      if index < 0:
        raise IndexError(f'index {index0} out of range ({self.buflen=})')
    if index >= self.buflen:
      raise IndexError(f'index {index0} out of range ({self.buflen=})')
    buf_offset = 0
    for buf in self.bufs:
      if index < buf_offset + len(buf):
        return buf[index - buf_offset]
      buf_offset += len(buf)
    raise RuntimeError(
        "%s.__getitem__(%s): failed to locate byte in bufs %r" %
        (self, index0, [len(buf) for buf in self.bufs]),
    )

  def __iter__(self):
    return self

  def __next__(self):
    ''' Fetch a data chunk from the buffer.
    '''
    if self.bufs:
      chunk = self.bufs.pop(0)
      self.buflen -= len(chunk)
    else:
      chunk = next(self.input_data)
      if self.progress is not None:
        self.progress += len(chunk)
    self.offset += len(chunk)
    return chunk

  next = __next__

  def iter(self, maxlength):
    ''' Yield chunks from the buffer
        up to `maxlength` in total
        or until EOF if `maxlength` is `Ellipsis`.
    '''
    if maxlength is not Ellipsis and maxlength < 1:
      raise ValueError(f'{maxlength=} mst be Ellipsis or >=1')
    while maxlength is Ellipsis or maxlength > 0:
      try:
        bs = next(self)
      except StopIteration:
        break
      if maxlength is not Ellipsis:
        if maxlength < len(bs):
          self.push(bs[maxlength:])
          bs = bs[:maxlength]
        maxlength -= len(bs)
      yield bs

  def push(self, bs):
    ''' Push the chunk `bs` onto the front of the buffered data.
        Rewinds the logical `.offset` by the length of `bs`.
    '''
    blen = len(bs)
    if blen > 0:
      self.bufs.insert(0, bs)
      self.buflen += blen
      self.offset -= blen

  @property
  def end_offset(self):
    ''' Return the end offset of the input data (in buffer ordinates)
        if known, otherwise `None`.

        Note that this depends on the computation of the
        `input_offset_displacement` which takes place at the buffer
        initialisation, which in turn relies on the `input_data.offset`
        attribute, which at initialisation is presumed to be 0 if missing.
    '''
    input_data = self.input_data
    try:
      input_end_offset = input_data.end_offset
    except AttributeError:
      return None
    return input_end_offset - self.input_offset_displacement

  def at_eof(self):
    ''' Test whether the buffer is at end of input.

        *Warning*: this will fetch from the `input_data` if the buffer
        is empty and so it may block.
    '''
    if self.bufs:
      return False
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

        If the `input_data` iterator has a `hint` method, this is
        passed to it.
    '''
    try:
      self.input_data.hint(size)
    except AttributeError:
      pass

  def extend(self, min_size, short_ok=False):
    ''' Extend the buffer to at least `min_size` bytes.

        If `min_size` is `Ellipsis`, extend the buffer to consume all the input.
        This should really only be used with bounded buffers
        in order to avoid unconstrained memory consumption.

        If there are insufficient data available then an `EOFError`
        will be raised unless `short_ok` is true (default `False`)
        in which case the updated buffer will be short.
    '''
    if min_size is Ellipsis:
      pass
    elif min_size < 1:
      raise ValueError(f'{min_size=} must be >= 1')
    while min_size is Ellipsis or min_size > self.buflen:
      if min_size is not Ellipsis:
        self.hint(min_size - self.buflen)
      try:
        next_chunk = next(self.input_data)
      except StopIteration:
        # no more input_data
        if min_size is Ellipsis or short_ok:
          return
        # pylint: disable=raise-missing-from
        raise EOFError(
            "insufficient input data, wanted %d bytes but only found %d" %
            (min_size, self.buflen),
        )
      if next_chunk:
        self.bufs.append(next_chunk)
        self.buflen += len(next_chunk)
        if self.progress is not None:
          self.progress += len(next_chunk)
    ##assert self.buflen >= min_size
    ##assert self.buflen == sum(len(buf) for buf in self.bufs)

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

  def takev(self, size, short_ok=False) -> List[Buffer]:
    ''' Return the next `size` bytes as a list of chunks
        (because the internal buffering is also a list of chunks).
        Other arguments are as for `.extend()`.

        See `.take()` to get a flat chunk instead of a list.
    '''
    if size == 0:
      return []
    if size is Ellipsis or size > self.buflen:
      # extend the buffered data
      self.extend(size, short_ok=short_ok)
      # post: the buffer is as big as it is going to get for this call
    if size is Ellipsis or size >= self.buflen:
      # take all the fetched data
      # which should be all the data because of the .extend() above
      taken = self.bufs
      self.bufs = []
    else:
      # size < self.buflen
      # take the leading data from the buffer
      taken = []
      bufs = self.bufs
      while size > 0:
        buf0 = bufs[0]
        if len(buf0) <= size:
          buf = buf0
          bufs.pop(0)
        else:
          # len(buf0) > size: crop from buf0
          assert len(buf0) > size
          buf = buf0[:size]
          bufs[0] = buf0[size:]
        taken.append(buf)
        size -= len(buf)
    # advance offset by the size of the taken data
    taken_size = sum(len(buf) for buf in taken)
    self.buflen -= taken_size
    self.offset += taken_size
    return taken

  def take(self, size, short_ok=False):
    ''' Return the next `size` bytes.
        Other arguments are as for `.extend()`.

        This is a thin wrapper for the `.takev` method.
    '''
    taken = self.takev(size, short_ok=short_ok)
    if not taken:
      return b''
    if len(taken) == 1:
      return bytes(taken[0])
    return b''.join(taken)

  def readline(self):
    r'''Return a binary "line" from `self`, where a line is defined by
        its ending `b'\n'` delimiter.
        The final line from a buffer might not have a trailing newline;
        `b''` is returned at EOF.

        Example:

            >>> bfr = CornuCopyBuffer([b'abc', b'def\nhij'])
            >>> bfr.readline()
            b'abcdef\n'
            >>> bfr.readline()
            b'hij'
            >>> bfr.readline()
            b''
            >>> bfr.readline()
            b''
    '''
    pending = []
    for bs in self:
      nlpos = bs.find(b'\n')
      if nlpos >= 0:
        pending.append(bs[:nlpos + 1])
        self.push(bs[nlpos + 1:])
        break
      pending.append(bs)
    return b''.join(pending)

  def peek(self, size, short_ok=False):
    ''' Examine the leading bytes of the buffer without consuming them,
        a `take` followed by a `push`.
        Returns the bytes.
    '''
    bs = self.take(size, short_ok=short_ok)
    self.push(bs)
    return bs

  def read(self, size, one_fetch=False):
    ''' Compatibility method to allow using the buffer like a file.

        Parameters:
        * `size`: the desired data size
        * `one_fetch`: do a single data fetch, default `False`

        In `one_fetch` mode the read behaves like a POSIX file read,
        returning up to to `size` bytes from a single I/O operation.
    '''
    if size < 1:
      raise ValueError(f'{size=} < 1')
    if size <= self.buflen:
      return self.take(size)
    # size > self.buflen
    if not one_fetch:
      self.extend(size, short_ok=True)
    taken = self.takev(min(size, self.buflen))
    size -= sum(len(buf) for buf in taken)
    if size > 0:
      # want more data
      if one_fetch:
        try:
          buf = next(self)
        except StopIteration:
          pass
        else:
          if size < len(buf):
            # push back the tail of the buffer
            self.push(buf[size:])
            buf = buf[:size]
          taken.append(buf)
    if not taken:
      return b''
    if len(taken) == 1:
      return taken[0]
    return b''.join(taken)

  def read1(self, size):
    ''' Shorthand method for `self.read(size,one_fetch=True)`.
    '''
    return self.read(size, one_fetch=True)

  def byte0(self):
    ''' Consume the leading byte and return it as an `int` (`0`..`255`).
    '''
    byte0, = self.take(1)
    return byte0

  def tell(self):
    ''' Compatibility method to allow using the buffer like a file.
    '''
    return self.offset

  def seek(self, offset, whence=None, short_ok=False):
    ''' Compatibility method to allow using the buffer like a file.
        This returns the resulting absolute offset.

        Parameters are as for `io.seek` except as noted below:
        * `whence`: (default `os.SEEK_SET`). This method only supports
          `os.SEEK_SET` and `os.SEEK_CUR`, and does not support seeking to a
          lower offset than the current buffer offset.
        * `short_ok`: (default `False`). If true, the seek may not reach
          the target if there are insufficent `input_data` - the
          position will be the end of the `input_data`, and the
          `input_data` will have been consumed; the caller must check
          the returned offset to check that it is as expected. If
          false, a `ValueError` will be raised; however, note that the
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
          f'seek: unsupported {whence=}, must be os.SEEK_SET or os.SEEK_CUR'
      )
    if offset < self.offset:
      raise ValueError(
          f'seek: target {offset=} < {self.offset=}; may not seek backwards'
      )
    if offset > self.offset:
      self.skipto(offset, short_ok=short_ok)
    return self.offset

  def skipto(self, new_offset, copy_skip=None, short_ok=False):
    ''' Advance to position `new_offset`. Return the new offset.

        Parameters:
        * `new_offset`: the target offset.
        * `copy_skip`: callable to receive skipped data.
        * `short_ok`: default `False`; if true then skipto may return before
          `new_offset` if there are insufficient `input_data`.

        Return values:
        * `buf`: the new state of `buf`
        * `offset`: the final offset; this may be short if `short_ok`.
    '''
    offset = self.offset
    if new_offset < offset:
      raise ValueError(f'skipto: {new_offset=} < {offset=}')
    return self.skip(
        new_offset - offset,
        copy_skip=copy_skip,
        short_ok=short_ok,
    )

  def skip(self, toskip, copy_skip=None, short_ok=False):
    ''' Advance position by `skip_to`. Return the new offset.

        Parameters:
        * `toskip`: the distance to advance
        * `copy_skip`: callable to receive skipped data.
        * `short_ok`: default `False`; if true then skip may return before
          `skipto` bytes if there are insufficient `input_data`.
    '''
    # consume buffered bytes in buf before the new offset
    bufskip = min(toskip, self.buflen)
    if bufskip > 0:
      for buf in self.takev(bufskip):
        if copy_skip:
          copy_skip(buf)
        toskip -= len(buf)
    assert toskip >= 0
    if toskip == 0:
      return
    # check that we consumed all the buffered data
    assert not self.bufs
    assert self.buflen == 0
    # advance the rest of the way
    seekable = False if copy_skip else self.seekable
    if seekable is None or seekable:
      # should we do a seek?
      try:
        input_seek = self.input_data.seek
      except AttributeError:
        if seekable is not None:
          print(
              f'{self}.skip: warning: {seekable=} but no input_data.seek method,'
              " resetting seekable to False",
              file=sys.stderr,
          )
        self.seekable = False
      else:
        # input_data has a seek method, try to use it
        new_offset = self.offset + toskip
        input_offset = new_offset + self.input_offset_displacement
        try:
          input_seek(input_offset)
        except OSError as e:
          print(
              f'{self}.skip: warning: input_data.seek({input_offset}): {e}'
              ', resetting self.seekable to False',
              file=sys.stderr,
          )
          self.seekable = False
        else:
          # successful seek, update offset and return
          self.offset = new_offset
          return
    # no seek, consume sufficient chunks
    self.hint(toskip)
    for buf in self.takev(toskip, short_ok=short_ok):
      toskip -= len(buf)
    assert toskip == 0

  @contextmanager
  def subbuffer(self, end_offset):
    ''' Context manager wrapper for `.bounded`
        which calls the `.flush` method automatically
        on exiting the context.

        Example:

            # avoid buffer overrun
            with bfr.subbuffer(bfr.offset+128) as subbfr:
                id3v1 = ID3V1Frame.parse(subbfr)
                # ensure the whole buffer was consumed
                assert subbfr.at_eof()
    '''
    subbfr = self.bounded(end_offset)
    try:
      yield subbfr
    finally:
      subbfr.flush()

  def bounded(self, end_offset) -> "CornuCopyBuffer":
    ''' Return a new `CornuCopyBuffer` operating on a bounded view
        of this buffer.

        This supports parsing of the buffer contents without risk
        of consuming past a certain point, such as the known end
        of a packet structure.

        Parameters:
        * `end_offset`: the ending offset of the new buffer.
          Note that this is an absolute offset, not a length.

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
            >>> bfr.take(2)
            b'ab'
            >>> bfr.offset
            2
            >>> subbfr = bfr.bounded(5)
            >>> subbfr.offset
            2
            >>> for bs in subbfr:
            ...   print(bs)
            ...
            b'c'
            b'de'
            >>> subbfr.offset
            5
            >>> subbfr.take(2)
            Traceback (most recent call last):
                ...
            EOFError: insufficient input data, wanted 2 bytes but only found 0
            >>> subbfr.flush()
            >>> bfr.offset
            5
            >>> bfr.take(2)
            b'fg'

        *WARNING*: if the bounded buffer is not completely consumed
        then it is critical to call the new `CornuCopyBuffer`'s `.flush`
        method to push any unconsumed buffer back into this buffer.
        Recommended practice is to always call `.flush` when finished
        with the new buffer.
        The `CornuCopyBuffer.subbuffer` method returns a context manager
        which does this automatically.

        Also, because the new buffer may buffer some of the unconsumed
        data from this buffer, use of the original buffer should
        be suspended.
    '''
    bfr2 = CornuCopyBuffer(
        _BoundedBufferIterator(self, end_offset),
        offset=self.offset,
        final_offset=end_offset,
    )

    def flush():
      ''' Flush the internal buffer of `bfr2` back into `self`'s
          internal buffer, adjusting the latter's `.offset` accordingly.
      '''
      for buf in reversed(bfr2.bufs):
        self.push(buf)

    bfr2.flush = flush  # pylint: disable=attribute-defined-outside-init
    return bfr2

  @classmethod
  def promote(cls, obj):
    ''' Promote `obj` to a `CornuCopyBuffer`,
        used by the `@cs.deco.promote` decorator.

        Promotes:
        * `int`: assumed to be a file descriptor of a file open for binary read
        * `str`: assumed to be a filesystem pathname
        * `bytes` and `bytes`like objects (`Buffer`s): binary data
        * has a `.read1` or `.read` method: assume a file open for binary read
        * iterable: assumed to be an iterable of `bytes`like objects
    '''
    if isinstance(obj, cls):
      return obj
    if isinstance(obj, int):
      return cls.from_fd(obj)
    if isinstance(obj, str):
      return cls.from_filename(obj)
    if isinstance(obj, Buffer):
      return cls.from_bytes(obj)
    if hasattr(obj, 'read1') or hasattr(obj, 'read'):
      return cls.from_file(obj)
    try:
      iter(obj)
    except TypeError:
      pass
    else:
      # assume this iterates byteslike objects
      return cls(obj)
    raise TypeError(f'{cls}.promote: cannot promote {r(obj)}')

class _BoundedBufferIterator:
  ''' An iterator over the data from a CornuCopyBuffer with an end
      offset bound.
  '''

  def __init__(self, bfr, end_offset):
    if end_offset < bfr.offset:
      raise ValueError(f'{end_offset=} < {bfr.offset=}')
    self.bfr = bfr
    self.end_offset = end_offset

  @property
  def offset(self):
    ''' The current iterator offset.
    '''
    return self.bfr.offset

  def __iter__(self):
    return self

  def __next__(self):
    bfr = self.bfr
    limit = self.end_offset - bfr.offset
    if limit <= 0:
      if limit < 0:
        raise RuntimeError(f'{limit=} < 0')
      raise StopIteration("limit reached")
    # post: limit > 0
    buf = next(bfr)
    # post: bfr's internal buffer now empty, can be modified
    length = len(buf)
    if length <= limit:
      return buf
    # return just the head, pushing the tail back into bfr
    head = buf[:limit]
    bfr.push(buf[limit:])
    return head

  next = __next__

  def hint(self, size):
    ''' Pass hints through to the underlying buffer.
    '''
    self.bfr.hint(size)

  def seek(self, offset, whence=SEEK_SET):
    ''' Do a seek on the underlying buffer, obeying the bounds.
    '''
    if whence == SEEK_SET:
      position = offset
    elif whence == SEEK_CUR:
      position = offset + self.bfr.offset
    elif whence == SEEK_END:
      position = self.end_offset + offset
    if not self.offset <= offset <= self.end_offset:
      raise ValueError(
          f'invalid seek {position=} < {self.offset=} or > {self.end_offset=}'
      )
    return self.bfr.seek(offset, SEEK_SET)

class CopyingIterator:
  ''' Wrapper for an iterator that copies every item retrieved to a callable.
  '''

  def __init__(self, it, copy_to):
    ''' Initialise with the iterator `it` and the callable `copy_to`.
    '''
    self.it = it
    self.copy_to = copy_to

  def __iter__(self):
    return self

  def __next__(self):
    item = next(self.it)
    self.copy_to(item)
    return item

  def __getattr__(self, attr):
    # proxy other attributes from the base iterator
    return getattr(self.it, attr)

class _FetchIterator:
  ''' A base class for iterators over objects with a read-like `_fetch()` method.
  '''

  def __init__(self, offset=0, readsize=None, align=False):
    ''' Initialise the `SeekableIterator`.

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
      raise ValueError(f'{readsize=} must be >=1')
    if offset < 0:
      raise ValueError(f'{offset=} must be >=0')
    self.offset = offset
    self.readsize = readsize
    self.align = align
    self.next_hint = None
    self._lock = Lock()

  def _fetch(self, readsize):
    raise NotImplementedError(f'no _fetch method in class {self.__class__}')

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

# pylint: disable=too-few-public-methods
class SeekableIteratorMixin:
  ''' Mixin supplying a logical with a `seek` method.
  '''

  def seek(self, new_offset, mode=SEEK_SET):
    ''' Move the logical offset.
    '''
    with self._lock:
      if mode == SEEK_SET:
        pass
      elif mode == SEEK_CUR:
        new_offset += self.offset
      elif mode == SEEK_END:
        try:
          end_offset = self.end_offset
        except AttributeError as e:
          # pylint: disable=raise-missing-from
          raise ValueError(f'mode=SEEK_END unsupported: {e}')
        new_offset += end_offset
      else:
        raise ValueError(f'unknown {mode=}')
      self.offset = new_offset
      return new_offset

class FDIterator(_FetchIterator):
  ''' An iterator over the data of a file descriptor.
  '''

  def __init__(self, fd: int, offset=None, readsize=None, align=True):
    ''' Initialise the iterator.

        Parameters:
        * `fd`: file descriptor
        * `offset`: the initial logical offset, kept up to date by
          iteration; the default is the current file position.
        * `readsize`: a preferred read size; if omitted then
          `DEFAULT_READSIZE` will be stored
        * `align`: whether to align reads by default: if true then
          the iterator will do a short read to bring the `offset`
          into alignment with `readsize`; the default is `True`
    '''
    if offset is None:
      offset = 0
    super().__init__(offset=offset, readsize=readsize, align=align)
    # dup the fd so that it cannot be closed out from under us
    # (well, via the original fd - anyone can call os.close())
    self.fd = os.dup(fd)

  def __del__(self):
    self.close()

  def close(self):
    ''' Close `self.fd` if it is nt yet `None`.
    '''
    with self._lock:
      if self.fd is not None:
        os.close(self.fd)
        self.fd = None

  def _fetch(self, readsize):
    with self._lock:
      return os.read(self.fd, readsize)

class SeekableFDIterator(FDIterator, SeekableIteratorMixin):
  ''' An iterator over the data of a seekable file descriptor.
  '''

  def __init__(self, fd, offset=None, **kw):
    if offset is None:
      offset = os.lseek(fd, 0, SEEK_CUR)
    super().__init__(fd, offset=offset, **kw)

  def _fetch(self, readsize):
    ''' Fetch data using `os.pread`.
    '''
    return pread(self.fd, readsize, self.offset)

  @property
  def end_offset(self):
    ''' The end offset of the file.
    '''
    return os.fstat(self.fd).st_size

class FileIterator(_FetchIterator, SeekableIteratorMixin):
  ''' An iterator over the data of a file object.
  '''

  def __init__(self, fp, offset=None, readsize=None, align=False):
    ''' Initialise the iterator.

        Parameters:
        * `fp`: file object
        * `offset`: the initial logical offset, kept up to date by
          iteration; the default is 0.
        * `readsize`: a preferred read size; if omitted then
          `DEFAULT_READSIZE` will be stored
        * `align`: whether to align reads by default: if true then
          the iterator will do a short read to bring the `offset`
          into alignment with `readsize`; the default is `False`
    '''
    if offset is None:
      offset = 0
    _FetchIterator.__init__(
        self,
        offset=offset,
        readsize=readsize,
        align=align,
    )
    self.fp = fp
    # try to use the frugal read method if available
    try:
      read1 = fp.read1
    except AttributeError:
      read1 = fp.read
    self.read1 = read1

  def close(self):
    ''' Detach from the file. Does *not* call `fp.close()`.
    '''
    self.fp = None

  def _fetch(self, readsize):
    return self.read1(readsize)

class SeekableFileIterator(FileIterator, SeekableIteratorMixin):
  ''' An iterator over the data of a seekable file object.

      *Note*: the iterator closes the file on __del__ or if its
      .close method is called.
  '''

  def __init__(self, fp, offset=None, **kw):
    ''' Initialise the iterator.

        Parameters:
        * `fp`: file object
        * `offset`: the initial logical offset, kept up to date by
          iteration; the default is the current file position.
        * `readsize`: a preferred read size; if omitted then
          `DEFAULT_READSIZE` will be stored
        * `align`: whether to align reads by default: if true then
          the iterator will do a short read to bring the `offset`
          into alignment with `readsize`; the default is `False`
    '''
    if offset is None:
      offset = fp.tell()
    FileIterator.__init__(self, fp=fp, offset=offset, **kw)

  def seek(self, new_offset, mode=SEEK_SET):
    ''' Move the logical file pointer.

        WARNING: moves the underlying file's pointer.
    '''
    new_offset = self.fp.seek(new_offset, mode)
    return super().seek(new_offset, SEEK_SET)

class SeekableMMapIterator(_FetchIterator, SeekableIteratorMixin):
  ''' An iterator over the data of a mappable file descriptor.
  '''

  def __init__(self, fd: int, offset=None, readsize=None, align=True):
    ''' Initialise the iterator.

        Parameters:
        * `offset`: the initial logical offset, kept up to date by
          iteration; the default is the current file position.
        * `readsize`: a preferred read size; if omitted then
          `DEFAULT_READSIZE` will be stored
        * `align`: whether to align reads by default: if true then
          the iterator will do a short read to bring the `offset`
          into alignment with `readsize`; the default is `True`
    '''
    if offset is None:
      offset = os.lseek(fd, 0, SEEK_CUR)
    _FetchIterator.__init__(
        self,
        offset=offset,
        readsize=readsize,
        align=align,
    )
    self.fd = fd
    self.base_offset = 0
    self.mmap = mmap.mmap(
        self.fd,
        0,
        flags=mmap.MAP_PRIVATE,
        prot=mmap.PROT_READ,
    )
    self.mv = memoryview(self.mmap)

  def close(self):
    ''' Close the mmap and detach.
    '''
    if self.mmap is not None:
      try:
        self.mmap.close()
      except BufferError:
        pass
      else:
        self.mmap = None

  @property
  def end_offset(self):
    ''' The end offset of the mmap memoryview.
    '''
    return self.base_offset + len(self.mv)

  def _fetch(self, readsize):
    if readsize < 1:
      raise ValueError(f'{readsize=}')
    return self.mv[self.offset:self.offset + readsize]
