#!/usr/bin/env python3

''' Utilities for working with EBooks.
'''

from contextlib import contextmanager
from getopt import GetoptError
from glob import glob
import os
from os.path import (
    basename,
    exists as existspath,
    expanduser,
    isfile as isfilepath,
    join as joinpath,
    relpath,
    splitext,
)
import sys
from tempfile import TemporaryDirectory
from zipfile import ZipFile, ZIP_STORED

import mobi

from cs.cmdutils import BaseCommand
from cs.context import stackattrs
from cs.fileutils import shortpath
from cs.fstags import FSTags
from cs.logutils import error, info
from cs.pfx import pfx, pfx_call
from cs.resources import MultiOpenMixin

class Mobi:
  ''' Work with an existing MOBI ebook file.
  '''

  def __init__(self, mobipath):
    if not isfilepath(mobipath):
      raise ValueError("mobipath %r is not a file" % (mobipath,))
    self.path = mobipath

  def __repr__(self):
    return "%s(%r)" % (type(self).__name__, repr(self.path))

  __str__ = __repr__

  @pfx
  def extract(self, dirpath=None):
    ''' Extract the contents of the MOBI file into a directory.
        Return `(dirpath,rfilepath)` where `dirpath` is the extracted
        file tree and `filepath` is the relative pathname of the
        primary epub, html or pdf file depending on the mobi type.
    '''
    if dirpath is not None and existspath(dirpath):
      raise ValueError("dirpath %r already exists" % (dirpath,))
    # divert stdout because the mobi library sends some warnings etc to stdout
    with stackattrs(sys, stdout=sys.stderr):
      tmpdirpath, filepath = pfx_call(mobi.extract, self.path)
    rfilepath = relpath(filepath, tmpdirpath)
    if dirpath is None:
      dirpath = tmpdirpath
    else:
      pfx_call(os.rename, tmpdirpath, dirpath)
    return dirpath, rfilepath

  @contextmanager
  def extracted(self):
    ''' Context manager version of `extract()` which extracts the
        MOBI into a temporary directory and yields the resulting
        `(dirpath,rfilepath)` as for `extract()`.
    '''
    with TemporaryDirectory(prefix='%s.extracted-' % (type(self).__name__,),
                            suffix='-%s' %
                            (self.path.replace(os.sep, '_'),)) as T:
      dirpath, rfilepath = self.extract(dirpath=joinpath(T, 'extracted'))
      yield dirpath, rfilepath

  @pfx
  def make_cbz(self, cbzpath=None):
    ''' Create a CBZ file from the images in the MOBI file.
        Return the path to the created CBZ file.
    '''
    if cbzpath is None:
      mobibase, mobiext = splitext(basename(self.path))
      cbzpath = mobibase + '.cbz'
    if existspath(cbzpath):
      raise ValueError("CBZ path %r already exists" % (cbzpath,))
    with self.extracted() as df:
      dirpath, rfilepath = df
      imagepaths = sorted(glob(joinpath(dirpath, 'mobi8/OEBPS/Images/*.*')))
      info("write %s", cbzpath)
      try:
        with pfx_call(ZipFile, cbzpath, 'x', compression=ZIP_STORED) as cbz:
          for imagepath in imagepaths:
            pfx_call(cbz.write, imagepath, arcname=basename(imagepath))
      except FileExistsError as e:
        error("CBZ already eixsts: %r: %s", cbzpath, e)
        return 1
      except Exception:
        if existspath(cbzpath):
          pfx_call(os.unlink, cbzpath)
    return cbzpath

class Mobi2CBZCommand(BaseCommand):
  ''' Command line implementation for `mobi2cbz`.
  '''

  USAGE_FORMAT = r'''Usage: {cmd} mobipath [cbzpath]
    Unpack a MOBI file and construct a CBZ file.
    Prints the path of the CBZ file to the output.'''

  def main(self, argv):
    ''' `mobi2cbz` command line implementation.
    '''
    if not argv:
      raise GetoptError("missing mobipath")
    mobipath = argv.pop(0)
    mobibase, mobiext = splitext(basename(mobipath))
    if argv:
      cbzpath = argv.pop(0)
    else:
      cbzpath = mobibase + '.cbz'
    if argv:
      raise GetoptError("extra arguments after cbzpath: %r" % (argv,))
    if not existspath(mobipath):
      raise GetoptError("mobipath does not exist: %r" % (mobipath,))
    if existspath(cbzpath):
      raise GetoptError("CBZ already exists: %r" % (cbzpath,))
    MB = Mobi(mobipath)
    outcbzpath = MB.make_cbz(cbzpath)
    print(outcbzpath)

class KindleTree(MultiOpenMixin):
  ''' Work with a Kindle ebook tree.

      This actually knows very little about Kindle ebooks or its rather opaque database.
      This is mostly to aid keeping track of state using `cs.fstags`.
  '''

  def __init__(self, kindle_library=None):
    if kindle_library is None:
      kindle_library = os.environ.get('KINDLE_LIBRARY')
      if kindle_library is None:
        # default to the MacOS path, needs updates for other platforms
        kindle_library = expanduser(
            '~/Library/Containers/com.amazon.Kindle/Data/Library/Application Support/Kindle/My Kindle Content'
        )
    self.path = kindle_library
    self._bookrefs = {}

  def __str__(self):
    return "%s:%s" % (type(self).__name__, shortpath(self.path))

  def __repr__(self):
    return "%s(%r)" % (type(self).__name__, self.path)

  @contextmanager
  def startup_shutdown(self):
    ''' Context manager to obtain and release resources.
    '''
    with FSTags() as fstags:
      with stackattrs(self, fstags=fstags):
        yield

  @property
  def dbpath(self):
    ''' The path to the SQLite database file.
    '''
    return joinpath(self.path, 'book_asset.db')

  @staticmethod
  def is_book_subdir(subdir_name):
    ''' Test whther `subdir_name` is a Kindle ebook subdirectory basename.
    '''
    return subdir_name.endswith(('_EBOK', '_EBSP'))

  def book_subdir_names(self):
    ''' Return a list of the individual ebook subdirectory names.
    '''
    return [
        dirbase for dirbase in os.listdir(self.path)
        if self.is_book_subdir(dirbase)
    ]

  def keys(self):
    ''' The keys of a `KindleTree` are its book subdirectory names.
    '''
    return self.book_subdir_names()

  def __getitem__(self, subdir_name):
    ''' Return the `cs.fstags.TaggedPath` for the ebook subdirectory named `subdir_name`.
    '''
    if not self.is_book_subdir(subdir_name):
      raise ValueError(
          "not a Kindle ebook subdirectory name: %r" % (subdir_name,)
      )
    try:
      book = self._bookrefs[subdir_name]
    except KeyError:
      book = self._bookrefs[subdir_name] = KindleBook(self, subdir_name)
    return book

  def __iter__(self):
    ''' Mapping iteration method.
    '''
    return iter(self.keys())

  def values(self):
    ''' Mapping method yielding `KindleBook` instances.
    '''
    yield from map(self.__getitem__, self)

  def items(self):
    ''' Mapping method yielding `(subdir_name,KindleBook)` pairs.
    '''
    for k in self:
      yield k, self[k]

class KindleBook:
  ''' A reference to a Kindle library book subdirectory.
  '''

  def __init__(self, tree, subdir_name):
    ''' Initialise this book subdirectory reference.

        Parameters:
        * `tree`: the `Kindletree` containing the subdirectory
        * `subdir_name`: the subdirectory name
    '''
    self.tree = tree
    self.subdir_name = subdir_name

  def __str__(self):
    return "%s[%s]:%s" % (self.tree, self.subdir_name, self.tags)

  def __repr__(self):
    return "%s(%r,%r)" % (type(self).__name__, self.tree, self.subdir_name)

  @property
  def path(self):
    ''' The filesystem path of this book subdirectory.
    '''
    return joinpath(self.tree.path, self.subdir_name)

  @property
  def tags(self):
    ''' The `FSTags` for this book subdirectory.
    '''
    return self.tree.fstags[self.path]

if __name__ == '__main__':
  with KindleTree() as kindle:
    for book in kindle.values():
      print(book.subdir_name, book)
