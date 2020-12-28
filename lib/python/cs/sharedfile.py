#!/usr/bin/python
#
# Facilities for shared access to files.
#   - Cameron Simpson <cs@cskk.id.au>
#

''' Facilities for shared access to files.
'''

from contextlib import contextmanager
import csv
import errno
import os
from os import dup, fdopen, SEEK_END, O_APPEND, O_RDONLY, O_RDWR, O_WRONLY
from os.path import abspath, dirname
import sys
from tempfile import mkstemp
from threading import RLock
import time
from cs.filestate import FileState
from cs.lex import as_lines
from cs.logutils import warning
from cs.pfx import Pfx
from cs.range import Range
from cs.timeutils import TimeoutError

__version__ = '20201228-post'

DISTINFO = {
    'description':
    "facilities for shared access to files",
    'keywords': ["python2", "python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.filestate',
        'cs.lex',
        'cs.logutils',
        'cs.pfx',
        'cs.range',
        'cs.timeutils',
    ],
}

DEFAULT_POLL_INTERVAL = 1.0
DEFAULT_READSIZE = 8192
DEFAULT_TAIL_PAUSE = 0.25

@contextmanager
def lockfile(path, ext=None, poll_interval=None, timeout=None):
  ''' A context manager which takes and holds a lock file.

      Parameters:
      * `path`: the base associated with the lock file.
      * `ext`:
        the extension to the base used to construct the lock file name.
        Default: `".lock"`
      * `timeout`: maximum time to wait before failing,
        default None (wait forever).
      * `poll_interval`: polling frequency when timeout is not 0.
  '''
  if poll_interval is None:
    poll_interval = DEFAULT_POLL_INTERVAL
  if ext is None:
    ext = '.lock'
  if timeout is not None and timeout < 0:
    raise ValueError("timeout should be None or >= 0, not %r" % (timeout,))
  start = None
  lockpath = path + ext
  while True:
    try:
      lockfd = os.open(lockpath, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0)
    except OSError as e:
      if e.errno != errno.EEXIST:
        raise
      if timeout is not None and timeout <= 0:
        # immediate failure
        raise TimeoutError(
            "cs.fileutils.lockfile: pid %d timed out on lockfile %r" %
            (os.getpid(), lockpath), timeout
        )
      now = time.time()
      # post: timeout is None or timeout > 0
      if start is None:
        # first try - set up counters
        start = now
        complaint_last = start
        complaint_interval = 2 * max(DEFAULT_POLL_INTERVAL, poll_interval)
      else:
        if now - complaint_last >= complaint_interval:
          warning(
              "cs.fileutils.lockfile: pid %d waited %ds for %r", os.getpid(),
              now - start, lockpath
          )
          complaint_last = now
          complaint_interval *= 2
      # post: start is set
      if timeout is None:
        sleep_for = poll_interval
      else:
        sleep_for = min(poll_interval, start + timeout - now)
      # test for timeout
      if sleep_for <= 0:
        raise TimeoutError(
            "cs.fileutils.lockfile: pid %d timed out on lockfile %r" %
            (os.getpid(), lockpath), timeout
        )
      time.sleep(sleep_for)
      continue
    else:
      break
  os.close(lockfd)
  try:
    yield lockpath
  finally:
    os.remove(lockpath)

class SharedAppendFile(object):
  ''' A base class to share a modifiable file between multiple users.

      The use case was driven from the shared CSV files used by
      `cs.nodedb.csvdb.Backend_CSVFile`, where multiple users can
      read from a common CSV file, and coordinate updates with a
      lock file.

      This presents the following interfaces:
      * `__iter__`: yields data chunks from the underlying file up
        to EOF; it blocks no more than reading from the file does.
        Note that multiple iterators share the same read pointer.

      * `open`: a context manager returning a writable file for writing
        updates to the file; it blocks reads from this instance
        (though not, of course, by other users of the file) and
        arranges that users of `__iter__` do not receive their own
        written data, thus arranging that `__iter__` returns only
        foreign file updates.

      Subclasses would normally override `__iter__` to parse the
      received data into their natural records.
  '''

  def __init__(
      self,
      pathname,
      read_only=False,
      write_only=False,
      binary=False,
      newline=None,
      lock_ext=None,
      lock_timeout=None,
      poll_interval=None
  ):
    ''' Initialise this SharedAppendFile.

        Parameters:
        * `pathname`: the pathname of the file to open.
        * `read_only`: set to true if we will not write updates.
        * `write_only`: set to true if we will not read updates.
        * `binary`: if the file is to be opened in binary mode, otherwise text mode.
        * 'newline`: passed to `open()`
        * `lock_ext`: lock file extension.
        * `lock_timeout`: maxmimum time to wait for obtaining the lock file.
        * `poll_interval`: poll time when taking a lock file,
          default `DEFAULT_POLL_INTERVAL`
    '''
    with Pfx("SharedAppendFile(%r): __init__", pathname):
      if poll_interval is None:
        poll_interval = DEFAULT_POLL_INTERVAL
      self.pathname = abspath(pathname)
      self.binary = binary
      self.newline = newline
      self.read_only = read_only
      self.write_only = write_only
      self.lock_ext = lock_ext
      self.lock_timeout = lock_timeout
      self.poll_interval = poll_interval
      if self.read_only:
        if self.write_only:
          raise ValueError("only one of read_only and write_only may be true")
        o_flags = O_RDONLY
      elif self.write_only:
        o_flags = O_WRONLY | O_APPEND
      else:
        o_flags = O_RDWR | O_APPEND
      self._fd = os.open(self.pathname, o_flags)
      self._rfp = None
      self._read_offset = 0
      self._read_skip = Range()
      self._readlock = RLock()
      if not self.write_only:
        self._readopen()
      self.closed = False

  def __str__(self):
    return "SharedAppendFile(%r)" % (self.pathname,)

  def close(self):
    ''' Close the SharedAppendFile: close input queue, wait for monitor to terminate.
    '''
    if self.closed:
      warning("multiple close of %s", self)
    self.closed = True

  def _readopen(self):
    ''' Open the file for read.
    '''
    assert not self.write_only
    mode = 'rb' if self.binary else 'r'
    fd = dup(self._fd)
    if self.binary:
      buffering = 0
    else:
      buffering = -1
    self._rfp = fdopen(fd, mode, buffering=buffering, newline=self.newline)
    self.open_state = self.filestate

  def _readclose(self):
    ''' Close the reader.
    '''
    assert not self.write_only
    rfp = self._rfp
    self._rfp = None
    rfp.close()
    self.closed = True

  def __iter__(self):
    ''' Iterate over the file, yielding data chunks until EOF.

        This skips data written to the file by this instance so that
        the data chunks returned are always foreign updates.
        Note that all iterators share the same file offset pointer.

        Usage:

            for chunk in f:
                ... process chunk ...
    '''
    assert not self.write_only
    while True:
      with self._readlock:
        # advance over any skip areas
        offset = self._read_offset
        skip = self._read_skip
        while skip and skip.start <= offset:
          start0, end0 = skip.span0
          if offset < end0:
            offset = end0
          skip.discard(start0, end0)
        read_size = DEFAULT_READSIZE
        if skip:
          read_size = min(read_size, skip.span0.start - offset)
          assert read_size > 0
        # gather data
        self._rfp.seek(offset)
        bs = self._rfp.read(read_size)
        self._read_offset = self._rfp.tell()
      if not bs:
        break
      yield bs

  def _lockfile(self):
    ''' Obtain an exclusive write lock on the CSV file.
        This arranges that multiple instances can coordinate writes.

        Usage:

            with self._lockfile():
                ... write data ...
    '''
    return lockfile(
        self.pathname,
        ext=self.lock_ext,
        poll_interval=self.poll_interval,
        timeout=self.lock_timeout
    )

  @contextmanager
  def open(self):
    ''' Open the file for append write, returing a writable file.
        Iterators are blocked for the duration of the context manager.
    '''
    if self.read_only:
      raise RuntimeError("attempt to write to read only SharedAppendFile")
    with self._lockfile():
      with self._readlock:
        mode = 'ab' if self.binary else 'a'
        fd = dup(self._fd)
        with fdopen(fd, mode, newline=self.newline) as wfp:
          wfp.seek(0, SEEK_END)
          start = wfp.tell()
          yield wfp
          end = wfp.tell()
        if end > start:
          self._read_skip.add(start, end)
        if not self.write_only:
          self._readopen()

  def tail(self):
    ''' A generator returning data chunks from the file indefinitely.

        This supports writing monitors for file updates.
        Note that this, like other iterators, shares the same file offset pointer.
        Also note that it calls the class' iterator, so that if a
        subsclass returns higher level records from its iterator,
        those records will also be returned from tail.

        Usage:

            for chunk in f:
                ... process chunk ...
    '''
    while True:
      for item in self:
        yield item
      if self.closed:
        return
      time.sleep(DEFAULT_TAIL_PAUSE)

  @property
  def filestate(self):
    ''' The current FileState of the backing file.
    '''
    fd = self._fd
    if fd is None:
      return None
    return FileState(fd)

  # TODO: need to notice filestate changes in other areas
  # TODO: support in place rewrite?
  @contextmanager
  def rewrite(self):
    ''' Context manager for rewriting the file.

        This writes data to a new file which is then renamed onto the original.
        After the switch, the read pointer is set to the end of the new file.

        Usage:

            with f.rewrite() as wfp:
                ... write data to wfp ...
    '''
    with self._readlock:
      with self.open() as _:
        tmpfp = mkstemp(dir=dirname(self.pathname), text=self.binary)
        try:
          yield tmpfp
        finally:
          if not self.write_only:
            self._read_offset = tmpfp.tell()
          tmpfp.close()
          os.rename(tmpfp, self.pathname)

class SharedAppendLines(SharedAppendFile):
  ''' A line oriented subclass of `SharedAppendFile`.
  '''

  def __init__(self, *a, **kw):
    if 'binary' in kw:
      raise ValueError('may not specify binary=')
    kw['binary'] = False
    super().__init__(*a, **kw)

  def __iter__(self):
    for line in as_lines(super().__iter__()):
      yield line

class SharedCSVFile(SharedAppendLines):
  ''' Shared access to a CSV file in UTF-8 encoding.
  '''

  def __init__(self, pathname, dialect='excel', fmtparams=None, **kw):
    if fmtparams is None:
      fmtparams = {}
    super().__init__(pathname, newline='', **kw)
    self.dialect = dialect
    self.fmtparams = fmtparams

  def __iter__(self):
    ''' Yield csv rows.
    '''
    for row in csv.reader((line for line in super().__iter__()),
                          dialect=self.dialect, **self.fmtparams):
      yield row

  @contextmanager
  def writer(self):
    ''' Context manager for appending to a CSV file.
    '''
    with self.open() as wfp:
      yield csv.writer(wfp, dialect=self.dialect, **self.fmtparams)

class SharedWriteable(object):
  ''' Wrapper for a writable file with supported mutex based cooperation.

      This is mostly a proxy for the wrapped file
      exceptthat all `.write` calls are serialised
      and when used as a context manager
      other writers are blocked.

      This is to support shared use of an output stream
      where certain outputs should be contiguous,
      such as a standard error stream used to maintain a status line
      or multiline messages.
  '''

  def __init__(self, f):
    self.f = f
    self._lock = RLock()

  def __enter__(self):
    ''' Take the lock and return.
    '''
    self._lock.acquire()
    return self

  def __exit__(self, *_):
    ''' Release the lock and proceed.
    '''
    self._lock.release()
    return False

  def __getattr__(self, attr):
    ''' This object is mostly a proxy for the wrapped file.
    '''
    with Pfx("%s.%s from self.f<%s>.%s", type(self).__name__, attr,
             type(self.f).__name__, attr):
      return getattr(self.f, attr)

  def write(self, s):
    ''' Obtain the lock and then run the wrapped `.write` method.
    '''
    with self._lock:
      return self.f.write(s)

if __name__ == '__main__':
  import cs.sharedfile_tests
  cs.sharedfile_tests.selftest(sys.argv)
