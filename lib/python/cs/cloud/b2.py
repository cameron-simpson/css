#!/usr/bin/env python3

''' BackBlaze B2 support.
'''

from collections import namedtuple
from contextlib import contextmanager, nullcontext
import os
from b2sdk.v1 import (
    B2Api,
    InMemoryAccountInfo,
    AbstractProgressListener,
    AbstractUploadSource,
)
from icontract import require
from typeguard import typechecked
from cs.lex import hexify
from cs.obj import SingletonMixin, as_dict
from cs.pfx import pfx_method, XP
from cs.progress import progressbar, auto_progressbar
from cs.threads import locked_property
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

  @locked_property
  def api(self):
    ''' The B2API, authorized from `self.credentials`.
    '''
    api = B2Api(InMemoryAccountInfo())
    api.authorize_account(
        "production", self.credentials.keyId, self.credentials.apiKey
    )
    return api

  __repr__ = __str__

  def bucketpath(self, bucket_name, *, credentials=None, path=None):
    ''' Return the path for the supplied `bucket_name`.
        Include the `credentials` if supplied.
    '''
    bpath = (
        f'{self.PREFIX}://{self.credentials.keyId}:{self.credentials.apiKey}@{bucket_name}'
        if credentials else f'{self.PREFIX}://{bucket_name}'
    )
    if path:
      bpath = joinpath(bpath, path)
    return bpath

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
      bfr,
      bucket_name: str,
      path: str,
      file_info=None,
      content_type=None,
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
    '''
    bucket = self.api.get_bucket_by_name(bucket_name)
    progress_listener = None if progress is None else B2ProgressShim(progress)
    result = bucket.upload(
        B2BufferShim(bfr),
        file_name=path,
        content_type=content_type,
        file_info=file_info,
        progress_listener=progress_listener,
    )
    XP("upload to %r => %s", path, result)
    return as_dict(result)

class B2BufferShim(AbstractUploadSource):
  ''' Shim to present a `CornuCopyBuffer` as an `AbstractUploadSource` for B2.
  '''

  def __init__(self, bfr, sha1bytes=None):
    super().__init__()
    self.bfr = bfr
    self.sha1bytes = sha1bytes

  @contextmanager
  def open(self):
    ''' Just hand the buffer back, it supports reads.
    '''
    with nullcontext():
      yield self.bfr

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
    try:
      fd = self.bfr.fd
    except AttributeError:
      pass
    else:
      try:
        S = os.fstat(fd)
      except OSError:
        pass
      else:
        return S.st_size
    return self.bfr.end_offset

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
    self.progress.total += total_byte_count

  def bytes_completed(self, byte_count):
    ''' Advance the progress position.
    '''
    advance = byte_count - self.latest_byte_count
    self.progress.position += advance
    self.latest_byte_count = byte_count

  def close(self):
    pass
