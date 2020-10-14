#!/usr/bin/env python3

''' BackBlaze B2 support.
'''

from collections import namedtuple
from contextlib import contextmanager
import io
import os
from os.path import join as joinpath
from tempfile import NamedTemporaryFile
from b2sdk.exception import FileNotPresent as B2FileNotPresent
from b2sdk.v1 import (
    B2Api,
    InMemoryAccountInfo,
    AbstractProgressListener,
    AbstractUploadSource,
    AbstractDownloadDestination,
)
from icontract import require
from typeguard import typechecked
from cs.buffer import CornuCopyBuffer
from cs.lex import hexify
from cs.obj import SingletonMixin, as_dict
from cs.pfx import pfx_method
from cs.progress import progressbar, auto_progressbar
from cs.queues import IterableQueue
from cs.threads import locked
from cs.units import BINARY_BYTES_SCALE
from . import Cloud

class B2Credentials(namedtuple('B2Credentials', 'keyId apiKey')):
  ''' Credentials for a BackBlaze B2 cloud service.
  '''

  @classmethod
  @typechecked
  def from_str(cls, credpart: str):
    ''' Construct a `B2Crednetials` from an keyId:apiKey string.
    '''
    keyId, apiKey = credpart.split(':')
    if not keyId:
      raise ValueError("empty keyId")
    if not apiKey:
      raise ValueError("empty apiKey")
    return cls(keyId=keyId, apiKey=apiKey)

class B2Cloud(SingletonMixin, Cloud):
  ''' A BackBlaze B2 cloud handle.
  '''

  PREFIX = 'b2'
  credentials_from_str = B2Credentials.from_str

  @staticmethod
  @require(lambda credentials: hasattr(credentials, 'keyId'))
  @require(lambda credentials: hasattr(credentials, 'apiKey'))
  def _singleton_key(credentials):
    return credentials.keyId, credentials.apiKey

  @require(lambda credentials: hasattr(credentials, 'keyId'))
  @require(lambda credentials: hasattr(credentials, 'apiKey'))
  def __init__(self, credentials):
    if hasattr(self, 'credentials'):
      return
    super().__init__(credentials)

  def __str__(self):
    return f"{self.PREFIX}://{self.credentials.keyId}:*/"

  @property
  @locked
  def api(self):
    ''' The B2API, authorized from `self.credentials`.
    '''
    api = B2Api(InMemoryAccountInfo())
    api.authorize_account(
        "production", self.credentials.keyId, self.credentials.apiKey
    )
    return api

  __repr__ = __str__

  def bucketpath(self, bucket_name, *, credentials=None):
    ''' Return the path for the supplied `bucket_name`.
        Include the `credentials` if supplied.
    '''
    return (
        f'{self.PREFIX}://{self.credentials.keyId}:{self.credentials.apiKey}@{bucket_name}'
        if credentials else f'{self.PREFIX}://{bucket_name}'
    )

  @classmethod
  def parse_sitepart(cls, sitepart):
    ''' Parse the site part of a b2path, return `(credentials,bucket_name)`.
        If there is no `keyId:appKey@` component
        then they will be obtained from the `$B2KEYID` and `$B2APIKEY`
        environment variables.
    '''
    try:
      credpart, bucket_name = sitepart.split('@')
    except ValueError:
      bucket_name = sitepart
      keyId = os.environ.get('B2KEYID')
      if not keyId:
        raise ValueError("no credpart and no $B2KEYID envvar")
      apiKey = os.environ.get('B2APIKEY')
      if not apiKey:
        raise ValueError("no credpart and no $B2APIKEY envvar")
      # TODO: also accept $B2KEYID being the path to a b2 compatible sqlite file?
      credpart = f"{keyId}:{apiKey}"
    credentials = cls.credentials_from_str(credpart)
    return credentials, bucket_name

  def stat(self, *, bucket_name: str, path: str):
    ''' Stat `path` within the bucket named `bucket_name`.
    '''
    bucket = self.api.get_bucket_by_name(bucket_name)
    versions = bucket.list_file_versions(path, fetch_count=1)
    try:
      version, = versions
    except ValueError:
      return None
    return version.as_dict()

  @auto_progressbar(report_print=True)
  def _b2_upload_file(
      self,
      f,
      *,
      bucket_name: str,
      path: str,
      progress=None,
      length=None,
      **b2_kw,
  ):
    ''' Upload a seekable file-like data source `f`
        to `path` within `bucket_name`.
        Return the resulting B2 `FileInfo`.
    '''
    bucket = self.api.get_bucket_by_name(bucket_name)
    progress_listener = None if progress is None else B2ProgressShim(progress)
    return bucket.upload(
        B2UploadFileShim(f, length=length, progress=progress),
        file_name=path,
        progress_listener=progress_listener,
        **b2_kw,
    )

  # pylint: disable=too-many-arguments
  @pfx_method
  @typechecked
  def upload_buffer(
      self,
      bfr: CornuCopyBuffer,
      *,
      bucket_name: str,
      path: str,
      file_info=None,
      content_type=None,
      length=None,
      progress=None,
  ):
    ''' Upload bytes from `bfr` to `path` within `bucket_name`.
        Return a `dict` containing the B2 `FileInfo` object attribute values.

        Parameters:
        * `bfr`: the source buffer
        * `bucket_name`: the bucket name
        * `path`: the subpath within the bucket
        * `file_info`: an optional mapping of extra information about the file
        * `content_type`: an optional MIME content type value
        * `progress`: an optional `cs.progress.Progress` instance
        * `length`: an option indication of the length of the buffer

        Annoyingly, the B2 stuff expects to seek on the buffer.
        Therefore we write a scratch file for the upload.
    '''
    with NamedTemporaryFile(dir='.') as T:
      for bs in progressbar(
          bfr, label=(joinpath(self.bucketpath(bucket_name), path) +
                      " scratch file"), total=length, itemlenfunc=len,
          units_scale=BINARY_BYTES_SCALE):
        T.write(bs)
      T.flush()
      return self.upload_filename(
          T.name,
          bucket_name=bucket_name,
          path=path,
          file_info=file_info,
          content_type=content_type,
          length=length,
          progress=progress,
      )

  def upload_file(
      self,
      f,
      *,
      bucket_name: str,
      path: str,
      file_info=None,
      content_type=None,
      progress=None,
      length=None
  ):
    ''' Upload the data from the file `f` to `path` within `bucket_name`.
        Return a `dict` containing the B2 `FileInfo` object attribute values.

        Note that the b2api expects to be able to seek when given
        a file, so this copies to a scratch file if given an
        unseekable file.

        Parameters:
        * `f`: the seekable file
        * `bucket_name`: the bucket name
        * `path`: the subpath within the bucket
        * `file_info`: an optional mapping of extra information about the file
        * `content_type`: an optional MIME content type value
        * `progress`: an optional `cs.progress.Progress` instance
        * `length`: an option indication of the length of the buffer
    '''
    # test the file for seekability
    try:
      # CornuCopyBuffers look a lot like files, but not enough for the b2api.
      if isinstance(f, CornuCopyBuffer):
        raise io.UnsupportedOperation(
            "CornuCopyBuffer does not support backwards seeks"
        )
      position = f.tell()
      f.seek(0)
      f.seek(position)
    except io.UnsupportedOperation:
      # upload via a scratch file
      bfr = f if isinstance(f,
                            CornuCopyBuffer) else CornuCopyBuffer.from_file(f)
      return self.upload_buffer(
          bfr,
          bucket_name=bucket_name,
          path=path,
          file_info=file_info,
          content_type=content_type,
          progress=progress,
          length=length,
      )
    else:
      file_info = self._b2_upload_file(
          f,
          progress_name=(
              joinpath(self.bucketpath(bucket_name), path) + " upload"
          ),
          progress_total=length,
          bucket_name=bucket_name,
          path=path,
          file_info=file_info,
          content_type=content_type,
          progress=progress,
          length=length,
      )
      return as_dict(file_info)

  # pylint: disable=too-many-arguments
  @auto_progressbar(report_print=True)
  @typechecked
  def download_buffer(
      self,
      *,
      bucket_name: str,
      path: str,
      progress=None,
  ) -> (CornuCopyBuffer, dict):
    ''' Download from `path` within `bucket_name`,
        returning `(buffer,file_info)`
        being a `CornuCopyBuffer` presenting the data bytes
        and the file info uploaded with the file.

        Parameters:
        * `bucket_name`: the bucket name
        * `path`: the subpath within the bucket
        * `progress`: an optional `cs.progress.Progress` instance
    '''
    bucket = self.api.get_bucket_by_name(bucket_name)
    progress_listener = None if progress is None else B2ProgressShim(progress)
    download_dest = B2DownloadBufferShim()
    file_info = bucket.download_file_by_name(
        path, download_dest, progress_listener
    )
    print("***")
    try:
      file_info = bucket.download_file_by_name(
          path, download_dest, progress_listener
      )
    except B2FileNotPresent as e:
      raise FileNotFoundError(self.pathfor(bucket_name, path)) from e
    return download_dest.bfr, file_info

class B2UploadFileWrapper:
  ''' A Wrapper for a file-like object which updates a `Progress`.
  '''

  def __init__(self, f, *, progress):
    self.f = f
    self.progress = progress

  def read(self, size):
    ''' Read from the file and advance the progress meter.
    '''
    bs = self.f.read(size)
    if self.progress:
      self.progress += len(bs)
    return bs

  def seek(self, position, whence):
    ''' Adjust the position of the file.
    '''
    return self.f.seek(position, whence)

  def tell(self):
    ''' Report position from the file.
    '''
    return self.f.tell()

class B2UploadFileShim(AbstractUploadSource):
  ''' Shim to present a `CornuCopyBuffer` as an `AbstractUploadSource` for B2.
  '''

  def __init__(self, f, *, length=None, sha1bytes=None, progress=None):
    super().__init__()
    self.f = f
    self.length = length
    self.progress = progress
    self.sha1bytes = sha1bytes

  @contextmanager
  def open(self):
    ''' Just hand the buffer back, it supports reads.
    '''
    if self.length:
      self.progress.total += self.length
    yield B2UploadFileWrapper(self.f, progress=self.progress)

  def get_content_sha1(self):
    if self.sha1bytes:
      return hexify(self.sha1bytes)
    ##raise NotImplementedError("get_content_sha1 (no sha1bytes attribute)")
    return None

  def is_upload(self):
    return True

  def is_copy(self):
    return False

  def is_sha1_known(self):
    return self.sha1bytes is not None

  def get_content_length(self):
    return self.length

class B2DownloadBufferShimFileShim:
  ''' Shim to present a write-to-file interface for an `IterableQueue`.
  '''

  def __init__(self, Q):
    self.Q = Q

  def write(self, bs):
    ''' A write puts `bytes` onto the queue.
    '''
    self.Q.put(bs)

  def close(self):
    ''' A close closes the queue.
    '''
    self.Q.close()

# pylint: disable=too-few-public-methods
class B2DownloadBufferShim(AbstractDownloadDestination):
  ''' Shim to present a writeable object which feeds a buffer.
  '''

  def __init__(self):
    self.Q = IterableQueue(1024)
    self.bfr = CornuCopyBuffer(self.Q)

  # pylint: disable=too-many-arguments
  @contextmanager
  def make_file_context(
      self,
      file_id,
      file_name,
      content_length,
      content_type,
      content_sha1,
      file_info,
      mod_time_millis,
      range_=None
  ):
    shim = B2DownloadBufferShimFileShim(self.Q)
    try:
      yield shim
    finally:
      shim.close()

class B2ProgressShim(AbstractProgressListener):
  ''' Shim to present a `Progress` as an `AbstractProgressListener` to B2.
  '''

  def __init__(self, progress):
    super().__init__()
    self.progress = progress
    self.latest_byte_count = 0

  def set_total_bytes(self, total_byte_count):
    ''' Advance the total upload by `total_byte_count`
        because the progress may be reused for multiple uploads.
    '''
    self.progress.total = (self.progress.total or 0) + total_byte_count

  def bytes_completed(self, byte_count):
    ''' Advance the progress position.
    '''
    advance = byte_count - self.latest_byte_count
    self.progress.position += advance
    self.latest_byte_count = byte_count

  def close(self):
    pass
