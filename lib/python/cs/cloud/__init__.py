#!/usr/bin/env python3

''' Stuff for working with cloud storage.
'''

from abc import ABC, abstractmethod, abstractclassmethod
from collections import namedtuple
from os.path import join as joinpath
from threading import RLock, Semaphore
from icontract import require
from typeguard import typechecked
from cs.buffer import CornuCopyBuffer
from cs.lex import is_identifier
from cs.obj import SingletonMixin
from cs.pfx import Pfx, pfx_method
from cs.py.modules import import_module_name

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
    if any(map(lambda part: part in ('.', '..'), subpath.split('/'))):
      raise ValueError("subpath contains '.' or '..'")

class ParsedCloudPath(namedtuple('ParsedCloudPath',
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
      cloudcls = Cloud.subclass_from_prefix(prefix)
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
    ''' The `ParsedCloudPath` as a string.
    '''
    return joinpath(
        self.cloud.bucketpath(self.bucket_name), self.subpath or ""
    )

  def new_cloud(self, **kw):
    ''' Make a new `Cloud` instance using the supplied keyword parameters `kw`.
    '''
    return self.cloudcls(self.credentials, **kw)

  @property
  def cloud(self):
    ''' The default cloud service supporting this path.
    '''
    return self.new_cloud()

class Cloud(ABC):
  ''' A cloud storage service.
  '''

  DEFAULT_MAX_CONNECTIONS = 32

  def __init__(self, credentials, *, max_connections=None):
    if max_connections is None:
      max_connections = self.DEFAULT_MAX_CONNECTIONS
    elif max_connections < 1:
      raise ValueError("max_connections:%s < 1" % (max_connections,))
    self.credentials = credentials
    self._lock = RLock()
    self.max_connections = max_connections
    self._conn_sem = Semaphore(max_connections)

  @staticmethod
  @typechecked
  @require(lambda prefix: is_identifier(prefix))  # pylint: disable=unnecessary-lambda
  def subclass_from_prefix(prefix: str):
    ''' Return the `Cloud` subclass.
    '''
    module_name = __name__ + '.' + prefix
    class_name = prefix.upper() + 'Cloud'
    try:
      return import_module_name(module_name, class_name)
    except (ModuleNotFoundError, ImportError) as e:
      raise ValueError(
          "no module %r for cloud service %r" % (module_name, prefix)
      ) from e

  @abstractmethod
  def bucketpath(self, bucket_name, *, credentials=None):
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

  def pathfor(self, bucket_name: str, subpath: str):
    ''' Return a cloud path string.
    '''
    if subpath:
      validate_subpath(subpath)
    return ParsedCloudPath(
        cloudcls=type(self),
        credentials=self.credentials,
        bucket_name=bucket_name,
        subpath=subpath
    ).as_path()

  # pylint: disable=no-self-use,unused-argument
  def tmpdir_for(self, *, bucket_name: str, path: str):
    ''' Offer a preferred directory location for scratch files
        located at `(bucket_name,path)`,
        suitable for the `dir` parameter of `tempfile.NamedTemporaryFile`.

        This default implementation returns `None`,
        as the location tends not to matter to most clouds.

        For the `fs` cloud implementation this is the directory of
        the upload target, allowing the upload itself to be a file
        rename of the scratch file.
    '''
    return None

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
      upload_progress=None,
  ):
    ''' Upload bytes from `bfr` to `path` within `bucket_name`.

        Parameters:
        * `bfr`: the source buffer
        * `bucket_name`: the bucket name
        * `path`: the subpath within the bucket
        * `file_info`: an optional mapping of extra information about the file
        * `content_type`: an optional MIME content type value
        * `upload_progress`: an optional `cs.progress.Progress` instance
          to which to report upload data
    '''
    raise NotImplementedError("upload_buffer")

  # pylint: disable=too-many-arguments
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
    ''' Upload bytes from `bs` to `path` within `bucket_name`.

        The default implementation calls `self.upload_buffer()`.

        Parameters:
        * `bs`: the source `bytes`-like object
        * `bucket_name`: the bucket name
        * `path`: the subpath within the bucket
        * `file_info`: an optional mapping of extra information about the file
        * `content_type`: an optional MIME content type value
        * `upload_progress`: an optional `cs.progress.Progress` instance
          to which to report upload data
    '''
    return self.upload_buffer(
        CornuCopyBuffer([bs]),
        bucket_name=bucket_name,
        path=path,
        file_info=file_info,
        content_type=content_type,
        upload_progress=upload_progress
    )

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
    with Pfx("open(%r,'rb')", filename):
      with open(filename, 'rb') as f:
        return self.upload_file(
            f,
            bucket_name=bucket_name,
            path=path,
            file_info=file_info,
            content_type=content_type,
            upload_progress=upload_progress,
        )

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
        Return a `dict` containing the upload result.

        The default implementation calls `self.upload_buffer()`.

        Parameters:
        * `f`: the file
        * `bucket_name`: the bucket name
        * `path`: the subpath within the bucket
        * `file_info`: an optional mapping of extra information about the file
        * `content_type`: an optional MIME content type value
        * `upload_progress`: an optional `cs.progress.Progress` instance
          to which to report upload data
    '''
    return self.upload_buffer(
        CornuCopyBuffer.from_file(f),
        bucket_name=bucket_name,
        path=path,
        file_info=file_info,
        content_type=content_type,
        upload_progress=upload_progress,
    )

  # pylint: disable=too-many-arguments
  @abstractmethod
  def download_buffer(
      self,
      *,
      bucket_name: str,
      path: str,
      download_progress=None,
  ):
    ''' Download from `path` within `bucket_name`,
        returning `(buffer,file_info)`
        being a CornuCopyBuffer` presenting the data bytes
        and the file info uploaded with the file.

        Parameters:
        * `bucket_name`: the bucket name
        * `path`: the subpath within the bucket
        * `download_progress`: an optional `cs.progress.Progress` instance
          to which to report download data
    '''
    raise NotImplementedError("download_buffer")

class CloudArea(namedtuple('CloudArea', 'cloud bucket_name basepath')):
  ''' A storage area in a cloud bucket.
  '''

  @classmethod
  def from_cloudpath(cls, path: str, **kw):
    ''' Construct a new `CloudArea` from the cloud path `path`
        using the supplied keyword parameters `kw`.
    '''
    CP = ParsedCloudPath.from_str(path)
    cloud = CP.new_cloud(**kw)
    return cls(cloud, CP.bucket_name, CP.subpath)

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

  def upload_buffer(self, bfr, *, upload_progress=None):
    ''' Upload a buffer into the cloud.
    '''
    return self.cloud.upload_buffer(
        bfr,
        bucket_name=self.bucket_name,
        path=self.bucket_path,
        upload_progress=upload_progress
    )

  def upload_filename(self, filename, *, upload_progress=None):
    ''' Upload a local file into the cloud.
    '''
    return self.cloud.upload_filename(
        filename,
        bucket_name=self.bucket_name,
        path=self.bucket_path,
        upload_progress=upload_progress
    )

  def download_buffer(self, *, download_progress=None):
    ''' Download from the cloud, return `(CornuCopyBuffer,dict)`.
    '''
    return self.cloud.download_buffer(
        bucket_name=self.bucket_name,
        path=self.bucket_path,
        download_progress=download_progress
    )
