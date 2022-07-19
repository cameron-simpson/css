#!/usr/bin/env python3

''' A few conveniences for working with matplotlib.
'''

from contextlib import contextmanager
import os
from os.path import (
    basename,
    dirname,
    exists as existspath,
    join as joinpath,
    splitext,
)
from subprocess import run
import sys
from tempfile import TemporaryDirectory

from cs.buffer import CornuCopyBuffer
from cs.pfx import pfx_call

# pylint: disable=redefined-builtin
@contextmanager
def saved_figure(figure_or_ax, dir=None, ext=None):
  ''' Context manager to save a `Figure` to a file and yield the file path.

      Parameters:
      * `figure_or_ax`: a `matplotlib.figure.Figure` or an object
        with a `.figure` attribute such as a set of `Axes`
      * `dir`: passed to `tempfile.TemporaryDirectory`
      * `ext`: optional file extension, default `'png'`
  '''
  figure = getattr(figure_or_ax, 'figure', figure_or_ax)
  if dir is None:
    dir = '.'
  if ext is None:
    ext = 'png'
  with TemporaryDirectory(dir=dir or '.') as tmppath:
    tmpimgpath = joinpath(tmppath, f'plot.{ext}')
    pfx_call(figure.savefig, tmpimgpath)
    yield tmpimgpath

def save_figure(figure_or_ax, imgpath: str, force=False):
  ''' Save a `Figure` to the file `imgpath`.

      Parameters:
      * `figure_or_ax`: a `matplotlib.figure.Figure` or an object
        with a `.figure` attribute such as a set of `Axes`
      * `imgpath`: the filesystem path to which to save the image
      * `force`: optional flag, default `False`: if true the `imgpath`
        will be written to even if it exists
  '''
  if not force and existspath(imgpath):
    raise ValueError("image path already exists: %r" % (imgpath,))
  _, imgext = splitext(basename(imgpath))
  ext = imgext[1:] if imgext else 'png'
  with saved_figure(figure_or_ax, dir=dirname(imgpath), ext=ext) as tmpimgpath:
    if not force and existspath(imgpath):
      raise ValueError("image path already exists: %r" % (imgpath,))
    pfx_call(os.link, tmpimgpath, imgpath)

def print_figure(figure_or_ax, imgformat=None, file=None):
  ''' Print `figure_or_ax` to a file.

      Parameters:
      * `figure_or_ax`: a `matplotlib.figure.Figure` or an object
        with a `.figure` attribute such as a set of `Axes`
      * `imgformat`: optional output format; if omitted use `'sixel'`
        if `file` is a terminal, otherwise `'png'`
      * `file`: the output file, default `sys.stdout`
  '''
  if file is None:
    file = sys.stdout
  if imgformat is None:
    if file.isatty():
      imgformat = 'sixel'
    else:
      imgformat = 'png'
  with saved_figure(figure_or_ax) as tmpimgpath:
    with open(tmpimgpath, 'rb') as imgf:
      if imgformat == 'sixel':
        file.flush()
        run(['img2sixel'], stdin=imgf, stdout=file.fileno(), check=True)
      else:
        for bs in CornuCopyBuffer.from_file(imgf):
          file.write(bs)
