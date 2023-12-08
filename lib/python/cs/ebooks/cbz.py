#!/usr/bin/env python3

''' Support for Comic Book Archive zipfiles.
'''

from contextlib import contextmanager
import json
from os.path import basename
import sys
from zipfile import ZipFile, ZIP_STORED

from typeguard import typechecked

from cs.fileutils import atomic_filename
from cs.pfx import Pfx, pfx_call

# TODO: ABCF metadata? https://acbf.fandom.com/wiki/ACBF_Specifications

@contextmanager
@typechecked
def make_cbz(
    fspath: str,
    *,
    images,
    metadata=None,
    metadata_encoding=None,
    compression=ZIP_STORED,
    **zipkw,
):
  ''' A context manager for creating a CBZ archive file,
      yielding an open `ZipFile` instance.

      Parameters:
      * `fspath`: the filesystem path for the new CBZ file
      * `images`: an iterable of images to save in the CBZ file
      * `metadata`: optional metadata, which may be a `str` or a JSNONable mapping
      * `metadata_encoding`: optional metadata encoding;
        setting this is only supported in Python 3.11 onward
        but if not supplied, a default of `'utf-8'` will be used in 3.11 onward
      Other keyword parameters are passed to the `ZipFile` creation
      along with an `'x'` open mode.

      The `images` iterable may yield either `str` or `(str,str)` tuples.
      A plain `str` is replaced by a `(str,basename(str))` tuple.
      The first `str` is the filesystem path of an image file.
      The second `str` is the `arcname` used when storing the file
      into the archive.
  '''
  if metadata_encoding is None:
    if sys.version_info >= (3, 11):
      metadata_encoding = 'utf-8'
  if metadata_encoding is not None:
    zipkw.update(metadata_encoding=metadata_encoding)
  with atomic_filename(fspath) as T:
    with pfx_call(
        ZipFile,
        T.name,
        'w',
        compression=compression,
        **zipkw,
    ) as cbz:
      if metadata:
        if isinstance(metadata, str):
          cbz.metadata = metadata
        else:
          cbz.metadata = json.dumps(metadata, separators=(',', ':'))
      for img in images:
        with Pfx(img):
          if isinstance(img, str):
            imgfspath, arcname = img, basename(img)
          # TODO: Path instances?
          else:
            imgfspath, arcname = img
          pfx_call(cbz.write, imgfspath, arcname=arcname)
      yield cbz
