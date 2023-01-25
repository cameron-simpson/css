#!/usr/bin/env python3

''' A cache for storing conversions of source files such as thumbnails
    or transcoded media, etc.
'''

import os
from os.path import (
    dirname,
    exists as existspath,
    expanduser,
    isabs as isabspath,
    join as joinpath,
    normpath,
    realpath,
    split as splitpath,
)
from stat import S_ISREG
from typing import Optional

from icontract import require

from cs.fileutils import atomic_filename
from cs.fs import needdir, HasFSPath
from cs.hashutils import SHA256
from cs.pfx import Pfx, pfx_call, pfx_method
from cs.seq import splitoff

class ConvCache(HasFSPath):
  ''' A cache for conversions of file contents.
  '''

  # TODO: XDG path? ~/.cache/convof ?
  DEFAULT_CACHE_BASEPATH = '~/var/cache/convof'
  DEFAULT_HASHCLASS = SHA256

  def __init__(self, fspath: Optional[str] = None, content_key_func=None):
    ''' Initialise a `ConvCache`.

        Parameters:
        * `fspath`: optional base path of the cache, default from
          `ConvCache.DEFAULT_CACHE_BASEPATH`;
          if this does not exist it will be created using `os.mkdir`
        * `content_key_func`: optional function to compute a key
          from the contents of a file, default `DEFAULT_HASHCLASS.from_fspath`
          (the SHA256 hash of the contents)
    '''
    if fspath is None:
      fspath = expanduser(self.DEFAULT_CACHE_BASEPATH)
    HasFSPath.__init__(self, fspath)
    if content_key_func is None:
      content_key_func = self.DEFAULT_HASHCLASS.from_fspath
    self._content_key_func = content_key_func
    needdir(fspath)
    self._content_keys = {}

  @pfx_method
  def content_key(self, srcpath):
    ''' Return a content key for the filesystem path `srcpath`.
    '''
    srcpath = realpath(srcpath)
    S = os.stat(srcpath)
    if not S_ISREG(S.st_mode):
      raise ValueError("not a regular file")
    signature = S.st_mtime, S.st_size
    try:
      content_key, cached_signature = self._content_keys[srcpath]
    except KeyError:
      content_key = None
    else:
      if cached_signature != signature:
        content_key = None
    if content_key is None:
      content_key = self._content_key_func(srcpath)
      self._content_keys[srcpath] = content_key, signature
    return content_key

  def content_subpath(self, srcpath):
    ''' Return the content key based subpath component.

        This default assumes the content key is a hash code and
        breaks it hex representation into a 3 level hierarchy
        such as `'d6/d9/c510785c468c9aa4b7bda343fb79'`.
    '''
    content_key = self.content_key(srcpath)
    return joinpath(*splitoff(content_key.hex(), 2, 2))

  @require(lambda conv_subpath: not isabspath(conv_subpath))
  def convof(self, srcpath, conv_subpath, conv_func, ext=None):
    ''' Return the filesystem path of the cached conversion of `srcpath` via `conv_func`.

        Parameters:
        * `srcpath`: the source filesystem path
        * `conv_subpath`: a name for the conversion which encompasses
          the salient aspaects such as `'png/64/64'` for a 64x64 pixel
          thumbnail in PNG format
        * `conv_func`: a callable of the form `conv_func(srcpath,dstpath)`
          to convert the contents of `srcpath` and write the result
          to the filesystem path `dstpath`
        * `ext`: an optional filename extension, default from the
          first component of `conv_subpath`
    '''
    conv_subpath = normpath(conv_subpath)
    conv_subparts = splitpath(conv_subpath)
    assert conv_subparts and '.' not in conv_subparts and '..' not in conv_subparts
    if ext is None:
      ext = conv_subparts[0]
    suffix = '.' + ext
    dstpath = self.pathto(
        joinpath(conv_subpath,
                 self.content_subpath(srcpath) + suffix)
    )
    dstdirpath = dirname(dstpath)
    pfx_call(os.makedirs, dstdirpath)
    if not existspath(dstpath):
      with Pfx('<%s %s >%s', srcpath, conv_func, dstpath):
        with atomic_filename(dstpath,
                             prefix=f'{self.__class__.__name__}.convof-',
                             suffix=suffix) as T:
          pfx_call(conv_func, srcpath, T.name)
    return dstpath

_default_conv_cache = ConvCache()

def convof(srcpath, conv_subpath, conv_func, ext=None):
  ''' `ConvCache.convof` using the default cache.
  '''
  return _default_conv_cache.convof(srcpath, conv_subpath, conv_func, ext=ext)
