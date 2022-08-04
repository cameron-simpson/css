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
from typing import Union

from typeguard import typechecked
from matplotlib.figure import Axes, Figure

from cs.buffer import CornuCopyBuffer
from cs.deco import fmtdoc
from cs.lex import r
from cs.pfx import pfx_call

DISTINFO = {
    'keywords': ["python3"],
    'classifiers': [
        "Development Status :: 3 - Alpha",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.buffer',
        'cs.deco',
        'cs.lex',
        'cs.pfx',
        'matplotlib',
        'typeguard',
    ],
}

DEFAULT_FIGURE_SIZE = 10, 7
DEFAULT_FIGURE_DPI = 100

@typechecked
@fmtdoc
def axes(figure=None, ax=None, **fig_kw) -> Axes:
  ''' Return a set of `Axes`.

      Parameters:
      * `figure`: optional `Figure` from which to obtain the `Axes`
        or an `(x,y)` figure size or an `(x,y,dpi)` figure size
      * `ax`: optional `Axes` or axes index

      If `ax` is already an `Axes` it is returned unchanged.
      Otherwise `ax` should be the index of a set of axes,
      default `0`.

      If `figure` is a `Figure`, `ax` is used to select one of its
      sets of axes.

      Otherwise a `Figure` is created and a set of axes is selected.
      The default figure size comes from `DEFAULT_FIGURE_SIZE` (`{DEFAULT_FIGURE_SIZE}`)
      and the default dpi comes from `DEFAULT_FIGURE_DPI` (`{DEFAULT_FIGURE_DPI}`).
      The `figure` positional parameter may be supplied
      as a 2-tuple `(fig_dx,fig_dy)` to override the default size
      or as a 3-tuple `(fig_dx,fig_dy,dpi)` to override the default size and dpi.
  '''
  # default Figure dimensions
  fig_dx, fig_dy = DEFAULT_FIGURE_SIZE
  dpi = DEFAULT_FIGURE_DPI
  if not isinstance(ax, Axes):
    if isinstance(figure, Figure):
      ax = figure.axes[0 if ax is None else ax]
    elif figure is None:
      if ax is None:
        # make a figure and choose the Axes from it
        figure = Figure(figsize=(fig_dx, fig_dy), dpi=dpi, **fig_kw)
        figure.add_subplot()
        # pylint: disable=unsubscriptable-object
        ax = figure.axes[0 if ax is None else ax]
      else:
        # Axes already have a Figure
        figure = ax.figure
    else:
      try:
        fig_dx, fig_dy = figure
      except ValueError:
        try:
          fig_dx, fig_dy, dpi = figure
        except ValueError:
          # pylint: disable=raise-missing-from
          raise TypeError(
              "invalid figure:%s, expected Figure or (x,y) or (x,y,dpi)" %
              (r(figure),)
          )
      # make a figure and choose the Axes from it
      figure = Figure(figsize=(fig_dx, fig_dy), dpi=dpi, **fig_kw)
      figure.add_subplot()
      # pylint: disable=unsubscriptable-object
      ax = figure.axes[0 if ax is None else ax]
  return ax

@typechecked
def remove_decorations(figure_or_ax: Union[Figure, Axes]):
  ''' Remove all decorations from a `Figure` or `Axes` instance,
      intended for making bare plots such as a tile in GUI.

      Presently this removes:
      - axes markings and legend from each axis
      - the padding from all the figure subplots
  '''
  if isinstance(figure_or_ax, Axes):
    axs = (figure_or_ax,)
    figure = figure_or_ax.figure
  else:
    figure = figure_or_ax
    axs = figure.axes
  for ax in axs:
    ax.set_axis_off()
    ax.get_legend().remove()
  figure.subplots_adjust(bottom=0, top=1, left=0, right=1, hspace=0, wspace=0)

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
