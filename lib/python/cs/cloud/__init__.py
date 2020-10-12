#!/usr/bin/env python3

''' Stuff for working with cloud storage.
'''

from abc import ABC, abstractmethod, abstractclassmethod
from collections import namedtuple
import os
from os.path import join as joinpath
from threading import RLock
from icontract import require
from typeguard import typechecked
from cs.buffer import CornuCopyBuffer
from cs.lex import is_identifier
from cs.logutils import warning
from cs.obj import SingletonMixin
from cs.pfx import Pfx, pfx_method
from cs.py.modules import import_module_name

from cs.x import X

def is_valid_subpath(subpath):
  ''' True if `subpath` is valid per the `validate_subpath()` function.
  '''
  try:
    validate_subpath(subpath)
  except ValueError:
    return False
  return True

@typechecked
def validate_subpath(subpath: str):
  ''' Validate a subpath against `is_valid_subpath`,
      raise `ValueError` on violations.

      Criteria:
      * not empty
      * does not start or end with a slash (`'/'`)
      * does not contain any multiple slashes
  '''
  with Pfx("validate_subpath(%r)", subpath):
    if not subpath:
      raise ValueError("empty subpath")
    if subpath.startswith('/'):
      raise ValueError("subpath starts with a slash")
    if subpath.endswith('/'):
      raise ValueError("subpath ends with a slash")
    if '//' in subpath:
      raise ValueError("subpath contains a multislash")

class CloudPath(namedtuple('CloudPath',
                           'cloudcls credentials bucket_name subpath')):
  ''' A deconstructed cloud path.
  '''

  @classmethod
  @typechecked
  def from_str(cls, cloudpath: str):
    ''' Parse a cloudpath
        of the form *prefix*`://`[*credentials*`@`]*bucket_name*[`/`*subpath`]
        such as `"b2://keyId:apiKey@bucket_name/subpath"`.
        Return a `namedtuple` with fields
        `(cloudcls,credentials,bucket_name,subpath)`.
    '''
    try:
      prefix, tail = cloudpath.split('://', 1)
    except ValueError:
      raise ValueError("missing ://")
    try:
      cloudcls = Cloud.from_prefix(prefix)
    except KeyError:
      raise ValueError("unknown cloud service %r" % (prefix,))
    try:
      sitepart, subpath = tail.split('/', 1)
    except ValueError:
      sitepart, subpath = tail, None
    else:
      if subpath:
        validate_subpath(subpath)
    credentials, bucket_name = cloudcls.parse_sitepart(sitepart)
    return cls(cloudcls, credentials, bucket_name, subpath)

  def as_path(self):
    ''' The `CloudPath` as a string.
    '''
    return joinpath(
        self.cloud.bucketpath(self.bucket_name), self.subpath or ""
    )

  @property
  def cloud(self):
    ''' The cloud service supporting this path.
    '''
    return self.cloudcls(self.credentials)

class Cloud(ABC):
  ''' A cloud storage service.
  '''

  def __init__(self, credentials):
    self.credentials = credentials
    self._lock = RLock()

  @staticmethod
  @typechecked
  @require(lambda prefix: is_identifier(prefix))  # pylint: disable=unnecessary-lambda
  def from_prefix(prefix: str):
    ''' Return the `Cloud` subclass
    '''
    module_name = __name__ + '.' + prefix
    class_name = prefix.upper() + 'Cloud'
    return import_module_name(module_name, class_name)

  @abstractmethod
  def bucketpath(self, bucket_name, credentials=None):
    ''' Return the path for the supplied `bucket_name`.
        Include the `credentials` if supplied.
    '''
    raise NotImplementedError("bucketpath")

  @abstractclassmethod
  def parse_sitepart(cls, sitepart):
    ''' Parse the site part of an fspath, return `(credentials,bucket_name)`.
    '''
    raise NotImplementedError("bucketpath")

  @classmethod
  @typechecked
  def from_sitepart(cls, sitepart: str):
    ''' Return a `Cloud` instance from the site part of a cloud path.
    '''
    credentials, _ = cls.parse_sitepart(sitepart)
    return cls(credentials)

  @abstractmethod
  def stat(self, *, bucket_name: str, path: str):
    ''' Probe the file at `path` in bucket `bucket_name`,
        return the file information as a `dict` or `None`.
    '''
    raise NotImplementedError("stat")

  # pylint: disable=too-many-arguments
  @abstractmethod
  def upload_buffer(
      self,
      bfr: CornuCopyBuffer,
      *,
      bucket_name: str,
      path: str,
      file_info=None,
      content_type=None,
      progress=None,
      length=None,
  ):
    ''' Upload bytes from `bfr` to `path` within `bucket_name`.

        Parameters:
        * `bfr`: the source buffer
        * `bucket_name`: the bucket name
        * `path`: the subpath within the bucket
        * `file_info`: an optional mapping of extra information about the file
        * `content_type`: an optional MIME content type value
        * `progress`: an optional `cs.progress.Progress` instance
        * `length`: an option indication of the length of the buffer
    '''
    raise NotImplementedError("upload_buffer")

  @pfx_method
  def upload_filename(
      self,
      filename,
      *,
      bucket_name: str,
      path: str,
      file_info=None,
      content_type=None,
      progress=None,
      length=None,
  ):
    ''' Upload the data from the file `f` to `path` within `bucket_name`.
        Return a `dict` containing the upload result.

        The default implementation calls `self.upload_file()`.

        Parameters:
        * `bfr`: the source buffer
        * `bucket_name`: the bucket name
        * `path`: the subpath within the bucket
        * `file_info`: an optional mapping of extra information about the file
        * `content_type`: an optional MIME content type value
        * `progress`: an optional `cs.progress.Progress` instance
        * `length`: an optional indication of the length of the buffer
    '''
    with Pfx("open(%r,'rb')", filename):
      with open(filename, 'rb') as f:
        stat_length = os.fstat(f.fileno()).st_size
        if length is None:
          length = stat_length
        elif length != stat_length:
          # warn but do not override the caller
          warning(
              "supplied length=%r != os.fstat().st_size=%r", length,
              stat_length
          )
        return self.upload_file(
            f,
            bucket_name=bucket_name,
            path=path,
            file_info=file_info,
            content_type=content_type,
            progress=progress,
            length=length
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
      length=None,
  ):
    ''' Upload the data from the file `f` to `path` within `bucket_name`.
        Return a `dict` containing the upload result.

        The default implementation calls `self.upload_buffer()`.

        Parameters:
        * `f`: the seekable file
        * `bucket_name`: the bucket name
        * `path`: the subpath within the bucket
        * `file_info`: an optional mapping of extra information about the file
        * `content_type`: an optional MIME content type value
        * `progress`: an optional `cs.progress.Progress` instance
        * `length`: an option indication of the length of the buffer
    '''
    return self.upload_buffer(
        CornuCopyBuffer.from_file(f),
        bucket_name=bucket_name,
        path=path,
        file_info=file_info,
        content_type=content_type,
        progress=progress,
        length=length
    )

  # pylint: disable=too-many-arguments
  @abstractmethod
  def download_buffer(
      self,
      *,
      bucket_name: str,
      path: str,
      progress=None,
  ):
    ''' Download from `path` within `bucket_name`,
        returning `(buffer,file_info)`
        being a CornuCopyBuffer` presenting the data bytes
        and the file info uploaded with the file.

        Parameters:
        * `bucket_name`: the bucket name
        * `path`: the subpath within the bucket
        * `progress`: an optional `cs.progress.Progress` instance
    '''
    raise NotImplementedError("download_buffer")

class CloudArea(namedtuple('CloudArea', 'cloud bucket_name basepath')):
  ''' A storage area in a cloud bucket.
  '''

  @classmethod
  def from_cloudpath(cls, path: str):
    ''' Construct a new `CloudArea` from the cloud path `path`.
    '''
    CP = CloudPath.from_str(path)
    return cls(CP.cloud, CP.bucket_name, CP.subpath)

  def subarea(self, subpath):
    ''' Return a `CloudArea` which is located within this `CloudArea`.
    '''
    validate_subpath(subpath)
    return type(self)(
        self.cloud, self.bucket_name, joinpath(self.basepath, subpath)
    )

  @property
  def cloudpath(self):
    ''' The path to this storage area.
    '''
    return joinpath(self.cloud.bucketpath(self.bucket_name), self.basepath)

  def __getitem__(self, filepath):
    validate_subpath(filepath)
    return CloudAreaFile(self, filepath)

class CloudAreaFile(SingletonMixin):
  ''' A reference to a file in cloud storage area.
  '''

  @staticmethod
  def _singleton_key(cloud_area, filepath):
    validate_subpath(filepath)
    return cloud_area, filepath

  ##@typechecked
  def __init__(self, cloud_area: CloudArea, filepath: str):
    X("CAF init cloud_area=%s filepath=%r", cloud_area, filepath)
    if hasattr(self, 'filepath'):
      return
    validate_subpath(filepath)
    self.cloud_area = cloud_area
    self.filepath = filepath

  def __str__(self):
    return self.cloudpath

  @property
  def cloud(self):
    ''' The `Cloud` for the storage area.
    '''
    return self.cloud_area.cloud

  @property
  def bucket_name(self):
    ''' The cloud bucket name.
    '''
    return self.cloud_area.bucket_name

  @property
  def bucket_path(self):
    ''' The path within the cloud bucket.
    '''
    return joinpath(self.cloud_area.basepath, self.filepath)

  @property
  def cloudpath(self):
    ''' The cloud path for this file.
    '''
    return joinpath(self.cloud_area.cloudpath, self.filepath)

  def upload_buffer(self, bfr, *, progress=None):
    ''' Upload a buffer into the cloud.
    '''
    return self.cloud.upload_buffer(
        bfr,
        bucket_name=self.bucket_name,
        path=self.bucket_path,
        progress=progress
    )

  def upload_filename(self, filename, *, progress=None):
    ''' Upload a local file into the cloud.
    '''
    with open(filename, 'rb') as f:
      bfr = CornuCopyBuffer.from_fd(f.fileno())
      return self.upload_buffer(bfr, progress=progress)

  def download_buffer(self, *, progress=None):
    ''' Download from the cloud, return `(CornuCopyBuffer,dict)`.
    '''
    return self.cloud.download_buffer(
        bucket_name=self.bucket_name, path=self.bucket_path, progress=progress
    )
