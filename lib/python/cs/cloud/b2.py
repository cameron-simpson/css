#!/usr/bin/env python3

''' BackBlaze B2 support.
'''

from collections import namedtuple
from contextlib import contextmanager
from mmap import mmap, PROT_READ
import os
from os.path import join as joinpath
from b2sdk.exception import FileNotPresent as B2FileNotPresent
from b2sdk.v1 import (
    B2Api,
    InMemoryAccountInfo,
    AbstractProgressListener,
    AbstractDownloadDestination,
)
from icontract import require
from typeguard import typechecked
from cs.buffer import CornuCopyBuffer
from cs.fileutils import NamedTemporaryCopy
from cs.logutils import warning
from cs.obj import SingletonMixin
from cs.pfx import pfx_method
from cs.queues import IterableQueue
from cs.threads import locked, locked_property
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

  DEFAULT_MAX_CONNECTIONS = 24

  credentials_from_str = B2Credentials.from_str

  @staticmethod
  @require(lambda credentials: hasattr(credentials, 'keyId'))
  @require(lambda credentials: hasattr(credentials, 'apiKey'))
  def _singleton_key(credentials, max_connections=None):
    return credentials.keyId, credentials.apiKey

  @require(lambda credentials: hasattr(credentials, 'keyId'))
  @require(lambda credentials: hasattr(credentials, 'apiKey'))
  def __init__(self, credentials, max_connections=None):
    if hasattr(self, 'credentials'):
      return
    super().__init__(credentials, max_connections=max_connections)
    self._buckets_by_name = {}

  def __str__(self):
    return f"{self.PREFIX}://{self.credentials.keyId}:*/"

  __repr__ = __str__

  @locked_property
  def api(self):
    ''' The B2API, authorized from `self.credentials`.
    '''
    api = B2Api(InMemoryAccountInfo())
    # monkey patch the API instance to serialise reauthorization attempts
    b2authorize_account = api.authorize_account

    def locked_authorize_account(*a):
      ''' Serialised version of the API authorize_account method.
      '''
      with self._lock:
        return b2authorize_account(*a)

    api.authorize_account = locked_authorize_account
    with self._conn_sem:
      api.authorize_account(
          "production", self.credentials.keyId, self.credentials.apiKey
      )
    return api

  @locked(initial_timeout=0.0)
  def bucket_by_name(self, bucket_name: str):
    ''' Caching function to return a B2 `Bucket` instance for `bucket_name`.
    '''
    try:
      bucket = self._buckets_by_name[bucket_name]
    except KeyError:
      with self._conn_sem:
        bucket = self._buckets_by_name[
            bucket_name] = self.api.get_bucket_by_name(bucket_name)
    return bucket

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
    bucket = self.bucket_by_name(bucket_name)
    with self._conn_sem:
      versions = bucket.list_file_versions(path, fetch_count=1)
      try:
        version, = versions
      except ValueError:
        return None
    return version.as_dict()

  def _b2_upload_bytes(
      self,
      bs,
      *,
      bucket_name: str,
      path: str,
      upload_progress=None,
      file_info=None,
      **b2_kw,
  ):
    ''' Upload the bytes `bs` to `path` within `bucket_name`.
        Return the resulting B2 `FileVersion`.
    '''
    bucket = self.bucket_by_name(bucket_name)
    progress_listener = None if upload_progress is None else B2ProgressShim(
        upload_progress
    )
    with self._conn_sem:
      return bucket.upload_bytes(
          bs,
          file_name=path,
          progress_listener=progress_listener,
          file_infos=file_info,
          **b2_kw,
      )

  def _b2_upload_filename(
      self,
      filename,
      *,
      bucket_name: str,
      path: str,
      upload_progress=None,
      file_info=None,
      **b2_kw,
  ):
    ''' Upload a local file named `filename`
        to `path` within `bucket_name`.
        Return the resulting B2 `FileVersion`.

        This is required for "large" files, a vaguely defined term.
        So we use it unconditionally if we're given a filename.
    '''
    bucket = self.bucket_by_name(bucket_name)
    progress_listener = None if upload_progress is None else B2ProgressShim(
        upload_progress
    )
    with self._conn_sem:
      return bucket.upload_local_file(
          local_file=filename,
          file_name=path,
          progress_listener=progress_listener,
          file_infos=file_info,
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
      upload_progress=None,
  ):
    ''' Upload bytes from `bfr` to `path` within `bucket_name`.
        Return a `dict` containing the B2 `FileVersion` attribute values.

        Parameters:
        * `bfr`: the source buffer
        * `bucket_name`: the bucket name
        * `path`: the subpath within the bucket
        * `file_info`: an optional mapping of extra information about the file
        * `content_type`: an optional MIME content type value
        * `upload_progress`: an optional `cs.progress.Progress` instance
          to which to report upload data

        Annoyingly, the B2 stuff expects to seek on the buffer.
        Therefore we write a scratch file for the upload.
    '''
    with NamedTemporaryCopy(
        bfr,
        progress=65536,
        progress_label=(joinpath(self.bucketpath(bucket_name), path) +
                        " scratch file"),
        dir=self.tmpdir_for(bucket_name=bucket_name, path=path),
        prefix='upload_buffer__' + path.replace(os.sep, '_') + '__',
    ) as T:
      return self.upload_filename(
          T.name,
          bucket_name=bucket_name,
          path=path,
          file_info=file_info,
          content_type=content_type,
          upload_progress=upload_progress,
      )

  @pfx_method
  def upload_bytes(
      self,
      bs,
      *,
      bucket_name: str,
      path: str,
      file_info=None,
      content_type=None,
      upload_progress=None,
  ):
    ''' Upload the data from the bytes `bs` to `path` within `bucket_name`.
        Return a `dict` containing the B2 `FileVersion` attribute values.

        Parameters:
        * `bs`: the bytes-like object
        * `bucket_name`: the bucket name
        * `path`: the subpath within the bucket
        * `file_info`: an optional mapping of extra information about the file
        * `content_type`: an optional MIME content type value
        * `upload_progress`: an optional `cs.progress.Progress` instance
          to which to report upload data
    '''
    file_version = self._b2_upload_bytes(
        bs,
        bucket_name=bucket_name,
        path=path,
        upload_progress=upload_progress,
        file_info=file_info,
        content_type=content_type,
    )
    return file_version.as_dict()

  @pfx_method
  def upload_file(
      self,
      f,
      *,
      bucket_name: str,
      path: str,
      file_info=None,
      content_type=None,
      upload_progress=None,
  ):
    ''' Upload the data from the file `f` to `path` within `bucket_name`.
        Return a `dict` containing the B2 `FileVersion` attribute values.

        Note that the b2api expects to be able to seek when given a file so
        this tries to `mmap.mmap` the file and use the bytes upload
        interface, falling back to coping to a scratch file.

        Parameters:
        * `f`: the file, preferably seekable
        * `bucket_name`: the bucket name
        * `path`: the subpath within the bucket
        * `file_info`: an optional mapping of extra information about the file
        * `content_type`: an optional MIME content type value
        * `upload_progress`: an optional `cs.progress.Progress` instance
          to which to report upload data
    '''
    try:
      fd = f.fileno()
      mm = mmap(fd, 0, prot=PROT_READ)
    except (AttributeError, OSError) as e:  # no .fileno, not mmapable
      warning("f=%s: %s", f, e)
      # upload via a scratch file
      bfr = f if isinstance(f,
                            CornuCopyBuffer) else CornuCopyBuffer.from_file(f)
      return self.upload_buffer(
          bfr,
          bucket_name=bucket_name,
          path=path,
          file_info=file_info,
          content_type=content_type,
          upload_progress=upload_progress,
      )
    else:
      file_version = self._b2_upload_bytes(
          mm,
          bucket_name=bucket_name,
          path=path,
          upload_progress=upload_progress,
      )
      return file_version.as_dict()

  @pfx_method
  def upload_filename(
      self,
      filename,
      *,
      bucket_name: str,
      path: str,
      file_info=None,
      content_type=None,
      upload_progress=None,
      as_is: bool = False,  # pylint: disable=unused-argument
  ):
    ''' Upload the data from the file named `filename`
        to `path` within `bucket_name`.
        Return a `dict` containing the upload result.

        The default implementation calls `self.upload_file()`.

        Parameters:
        * `filename`: the filename of the file
        * `bucket_name`: the bucket name
        * `path`: the subpath within the bucket
        * `file_info`: an optional mapping of extra information about the file
        * `content_type`: an optional MIME content type value
        * `upload_progress`: an optional `cs.progress.Progress` instance
          to which to report upload data
        * `as_is`: an optional flag indicating that the supplied filename
          refers to a file whose contents will never be modified
          (though it may be unlinked); default `False`

        The `as_is` flag supports modes which can use the original file
        in a persistent object. In particular, the `FSCloud` subclass
        will try to hard link the file into its storage area
        if this flag is true.
    '''
    file_version = self._b2_upload_filename(
        filename,
        bucket_name=bucket_name,
        path=path,
        upload_progress=upload_progress,
        file_info=file_info,
        content_type=content_type,
    )
    return file_version.as_dict()

  # pylint: disable=too-many-arguments
  @typechecked
  def download_buffer(
      self,
      *,
      bucket_name: str,
      path: str,
      download_progress=None,
  ) -> (CornuCopyBuffer, dict):
    ''' Download from `path` within `bucket_name`,
        returning `(buffer,file_info)`
        being a `CornuCopyBuffer` presenting the data bytes
        and the file info uploaded with the file.

        Parameters:
        * `bucket_name`: the bucket name
        * `path`: the subpath within the bucket
        * `download_progress`: an optional `cs.progress.Progress` instance
          to which to report download data
    '''
    bucket = self.bucket_by_name(bucket_name)
    progress_listener = (
        None
        if download_progress is None else B2ProgressShim(download_progress)
    )
    download_dest = B2DownloadBufferShim()
    try:
      file_info = bucket.download_file_by_name(
          path, download_dest, progress_listener
      )
    except B2FileNotPresent as e:
      raise FileNotFoundError(self.pathfor(bucket_name, path)) from e
    return download_dest.bfr, file_info

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
