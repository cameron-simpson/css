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
    images,
    *,
    compression=ZIP_STORED,
    metadata=None,
    **zipkw,
):
  ''' A context manager for creating a CBZ archive file,
      yielding an open `ZipFile` instance.
      On return the `ZipFile` is closed and the CBZ file is present at `fspath`.

      Parameters:
      * `fspath`: the filesystem path for the new CBZ file
      * `images`: an iterable of images to save in the CBZ file in reading order
      * `metadata`: optional metadata, which may be a `str` or a JSNONable mapping
      Other keyword parameters are passed to the `ZipFile` creation
      along with an `'x'` open mode.

      The `images` iterable may yield either `str` or `(str,str)` tuples.
      A plain `str` is replaced by a `(str,arcname)` tuple.
      The first `str` is the filesystem path of an image file.
      The second `str` is the `arcname` used when storing the file
      into the archive.
  '''
  images = tuple(images)
  with atomic_filename(fspath) as T:
    with pfx_call(
        ZipFile,
        T.name,
        'w',  # not 'x' because the temp file T.name exists
        compression=compression,
        **zipkw,
    ) as cbz:
      if metadata:
        if isinstance(metadata, str):
          cbz.metadata = metadata
        else:
          cbz.metadata = json.dumps(metadata, separators=(',', ':'))
      pagen_width = len(str(len(images)))
      for pagen, img in enumerate(images, 1):
        with Pfx(img):
          if isinstance(img, str):
            imgfspath, arcname = img, f'pages/{pagen:0{pagen_width}d}--{basename(img)}'
          # TODO: Path instances?
          else:
            imgfspath, arcname = img
          pfx_call(cbz.write, imgfspath, arcname=arcname)
      yield cbz
