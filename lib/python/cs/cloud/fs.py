#!/usr/bin/env python3

''' BackBlaze B2 support.
'''

import os
from os.path import join as joinpath
from icontract import require
from typeguard import typechecked
from cs.fstags import FSTags
from cs.obj import SingletonMixin, as_dict
from cs.pfx import pfx_method, XP
from . import Cloud

class FSCloud(SingletonMixin, Cloud):
  ''' A filesystem handle.
  '''

  PREFIX = 'fs'

  @staticmethod
  @require(lambda credentials: credentials is None)
  def _singleton_key(credentials):
    return credentials

  @require(lambda credentials: credentials is None)
  def __init__(self, credentials):
    assert credentials is None
    if hasattr(self, 'credentials'):
      return
    super().__init__(credentials)

  def __str__(self):
    return f"{self.PREFIX}:///"

  __repr__ = __str__

  def bucketpath(self, bucket_name, credentials=None):
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

  @typechecked
  @staticmethod
  def upload_buffer(
      bfr,
      bucket_name: str,
      path: str,
      file_info=None,
      content_type=None,
      progress=None,
  ):
    ''' Upload bytes from `bfr` to `path` within `bucket_name`,
        which means to the file `/`*bucket_name*`/`*path*.
        Return a `dict` with some relevant information.

    '''
    filename = os.sep + joinpath(bucket_name, path)
    with open(filename, 'wb') as f:
      for bs in bfr:
        f.write(bs)
        progress += len(bs)
    if file_info or content_type:
      with FSTags() as fstags:
        tags = fstags[filename].direct_tags
        if content_type:
          tags['mime.content_type'] = content_type
        if file_info:
          tags.update(file_info, prefix='file_info')
    return as_dict(os.stat(filename), 'st_')
