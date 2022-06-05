#!/usr/bin/env python3

''' Support for MOBI format ebook files.
'''

from contextlib import contextmanager
from getopt import GetoptError
from glob import glob
import os
from os.path import (
    basename,
    exists as existspath,
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
from cs.logutils import error, info
from cs.pfx import pfx, pfx_call

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
      mobibase, _ = splitext(basename(self.path))
      cbzpath = mobibase + '.cbz'
    if existspath(cbzpath):
      raise ValueError("CBZ path %r already exists" % (cbzpath,))
    with self.extracted() as df:
      dirpath, _ = df
      imagepaths = sorted(glob(joinpath(dirpath, 'mobi8/OEBPS/Images/*.*')))
      if not imagepaths:
        imagepaths = sorted(glob(joinpath(dirpath, 'mobi7/Images/*.*')))
      if not imagepaths:
        for dirp, dirnames, filenames in os.walk(dirpath):
          dirnames[:] = sorted(dirnames)
          for f in sorted(filenames):
            print(joinpath(dirp, f))
        raise ValueError("no image paths")
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
        raise
    return cbzpath

class MobiCommand(BaseCommand):
  ''' Command line implementation for `mobi2cbz`.
  '''

  def cmd_extract(self, argv):
    ''' Usage: {cmd} mobipath [outdir]
          Extract the contents of the MOBI file mobipath
          into the directory outdir, default based on the mobipath basename.
          Prints the outdir and the name of the top file.
    '''
    outdirpath = None
    mobipath = self.poparg(argv, "mobipath")
    if argv:
      outdirpath = argv.pop(0)
    if argv:
      raise GetoptError("extra arguments after outdir: %r" % (argv,))
    if not existspath(mobipath):
      raise GetoptError("mobipath does not exist: %r" % (mobipath,))
    if outdirpath is None:
      outdirpath, _ = splitext(basename(mobipath))
    if existspath(outdirpath):
      raise GetoptError("outdir already exists: %s" % (outdirpath,))
    MB = Mobi(mobipath)
    extdirpath, rfilepath = MB.extract(outdirpath)
    assert extdirpath == outdirpath
    print(outdirpath)
    print(rfilepath)

  def cmd_make_cbz(self, argv):
    ''' Usage: {cmd} mobipath [cbzpath]
          Unpack a MOBI file and construct a CBZ file.
          Prints the path of the CBZ file to the output.
          The default cbzpath is mobibase.cbz where mobibase is the
          basename of mobipath with its extension removed.
    '''
    mobipath = self.poparg(argv, "mobipath")
    mobibase, _ = splitext(basename(mobipath))
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
