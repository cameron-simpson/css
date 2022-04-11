#!/usr/bin/env python3

''' Filesystem support.
'''

import errno
import os
from os.path import dirname, isdir as isdirpath, join as joinpath
from icontract import require
from typeguard import typechecked
from cs.buffer import CornuCopyBuffer
from cs.fstags import FSTags
from cs.logutils import warning
from cs.obj import SingletonMixin, as_dict
from cs.pfx import Pfx, pfx_method
from . import Cloud, validate_subpath

class FSCloud(SingletonMixin, Cloud):
  ''' A filesystem based cloud storage handle.

      In this regime objects are stored at the path
      `/`*bucket_name*`/`*path*
      with associated information stored in a `.fstags` file
      (see the `cs.fstags` module).
  '''

  PREFIX = 'fs'

  DEFAULT_MAX_CONNECTIONS = 8

  @staticmethod
  @require(lambda credentials: credentials is None)
  def _singleton_key(credentials, max_connections=None):
    return credentials

  @require(lambda credentials: credentials is None)
  def __init__(self, credentials, max_connections=None):
    assert credentials is None
    if hasattr(self, 'credentials'):
      return
    super().__init__(credentials, max_connections=max_connections)

  def __str__(self):
    return f"{self.PREFIX}:///"

  __repr__ = __str__

  def bucketpath(self, bucket_name, *, credentials=None):
    ''' Return the path for the supplied `bucket_name`.
        Include the `credentials` if supplied.
    '''
    assert credentials is None
    return f'{self.PREFIX}://{bucket_name}'

  @classmethod
  def parse_sitepart(cls, sitepart):
    ''' Parse the site part of an fspath, return `(credentials,bucket_name)`.
        Since filesystem paths have no credentials we just return the sitepart.
    '''
    return None, sitepart

  def stat(self, *, bucket_name: str, path: str):
    ''' Stat `/`*bucket_name*`/`*path*.
    '''
    filename = os.sep + joinpath(bucket_name, path)
    try:
      st = os.stat(filename)
    except OSError as e:
      if e.errno == errno.ENOENT:
        return None
      raise
    result = as_dict(st, 'st_')
    result.update(path=filename)
    return result

  def tmpdir_for(self, *, bucket_name: str, path: str):
    ''' Offer a preferred directory location for scratch files
        located at `(bucket_name,path)`,
        suitable for the `dir` parameter of `tempfile.NamedTemporaryFile`.

        For the `fs` cloud implementation this is the directory of
        the upload target, allowing the upload itself to be a file
        rename or hard link of the scratch file.
    '''
    validate_subpath(path)
    filename = os.sep + joinpath(bucket_name, path)
    tmp_dirpath = dirname(filename)
    if not isdirpath(tmp_dirpath):
      with Pfx("makedirs(%r)", tmp_dirpath):
        os.makedirs(tmp_dirpath, 0o777)
    return tmp_dirpath

  @staticmethod
  def _apply_file_info(filename, *, file_info, content_type):
    ''' Apply the `file_info` and/or `content_type`
        to the file named `filename`.
    '''
    if file_info or content_type:
      with FSTags() as fstags:
        tags = fstags[filename]
        if content_type:
          tags['mime.content_type'] = content_type
        if file_info:
          tags.update(file_info, prefix='file_info')

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
      as_is: bool = False,
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

        This `FSCloud` implementation will try to hard link the file into its
        storage area if this flag is true, falling back on a copy if necessary.
        Users wanting to take advantage of this,
        for example by creating the file as a scratch file
        with `tempfile.NamedTemporaryFile`,
        can obtain a suitable directory in which to create the file
        from the `tmpdir_for(bucket_name,path)` method.
    '''
    dst_filename = os.sep + joinpath(bucket_name, path)
    if as_is:
      with Pfx("remove(%r)", dst_filename):
        try:
          os.remove(dst_filename)
        except OSError as e:
          rm_ok = e.errno == errno.ENOENT
      if rm_ok:
        with Pfx("link(%r,%r)", filename, dst_filename):
          try:
            os.link(filename, dst_filename)
          except OSError as e:
            if e.errno != errno.EXDEV:
              warning(str(e))
          else:
            # success, do admin and return
            self._apply_file_info(
                dst_filename, file_info=file_info, content_type=content_type
            )
            result = as_dict(os.stat(dst_filename), 'st_')
            result.update(path=dst_filename)
            return result
    # fall back to the default implementation
    return super().upload_filename(
        filename,
        bucket_name=bucket_name,
        path=path,
        file_info=file_info,
        content_type=content_type,
        upload_progress=upload_progress,
        as_is=as_is
    )

  # pylint: disable=too-many-arguments,arguments-differ
  @typechecked
  def upload_buffer(
      self,
      bfr: CornuCopyBuffer,
      bucket_name: str,
      path: str,
      file_info=None,
      content_type=None,
      upload_progress=None,
  ):
    ''' Upload bytes from `bfr` to `path` within `bucket_name`,
        which means to the file `/`*bucket_name*`/`*path*.
        Return a `dict` with some relevant information.

        Parameters:
        * `bfr`: the source buffer
        * `bucket_name`: the bucket name
        * `path`: the subpath within the bucket
        * `file_info`: an optional mapping of extra information about the file
        * `content_type`: an optional MIME content type value
        * `upload_progress`: an optional `cs.progress.Progress` instance
          to which to report upload data
    '''
    dst_filename = os.sep + joinpath(bucket_name, path)
    dirpath = dirname(dst_filename)
    with self._conn_sem:
      if not isdirpath(dirpath):
        ##warning("create directory %r", dirpath)
        with Pfx("makedirs(%r)", dirpath):
          os.makedirs(dirpath, 0o777)
      with open(dst_filename, 'wb') as f:
        for bs in bfr:
          f.write(bs)
          if upload_progress is not None:
            upload_progress += len(bs)
    self._apply_file_info(
        dst_filename, file_info=file_info, content_type=content_type
    )
    result = as_dict(os.stat(dst_filename), 'st_')
    result.update(path=dst_filename)
    return result

  # pylint: disable=too-many-arguments,arguments-differ
  @typechecked
  def download_buffer(
      self,
      *,
      bucket_name: str,
      path: str,
      download_progress=None,  # pylint: disable=unused-argument
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
    filename = os.sep + joinpath(bucket_name, path)
    with Pfx("open(%r)", filename):
      with open(filename, 'rb') as f:
        bfr = CornuCopyBuffer.from_fd(f.fileno(), progress=download_progress)
    with FSTags() as fstags:
      file_info = fstags[filename].as_dict()
    return bfr, file_info
