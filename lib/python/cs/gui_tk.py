#!/usr/bin/env python3

''' Assorted Tkinter based GUI widgets and also `BaseTkCommand`
    which subclasses `cs.cmdutils.BaseCommand`.
'''

from abc import ABC
from collections import defaultdict, namedtuple
from contextlib import contextmanager
import os
from os.path import (
    abspath,
    basename,
    expanduser,
    isdir as isdirpath,
    isfile as isfilepath,
    join as joinpath,
    splitext,
)
import platform
import tkinter as tk
from tkinter import Tk, ttk
from typing import Iterable, List
from uuid import uuid4

from icontract import require, ensure
from PIL import Image, ImageTk, UnidentifiedImageError
from typeguard import typechecked

from cs.cache import convof
from cs.cmdutils import BaseCommand
from cs.fs import needdir, shortpath
from cs.fstags import FSTags
from cs.hashutils import SHA256
from cs.lex import cutprefix
from cs.logutils import warning
from cs.pfx import pfx, pfx_method, pfx_call
from cs.resources import RunState, uses_runstate
from cs.tagset import Tag

from cs.lex import r
from cs.x import X
from cs.py.stack import caller

is_darwin = platform.system() == "Darwin"

# used by pngfor()
CONVCACHE_ROOT_ENVVAR = 'CONVCACHE_ROOT'
CONVCACHE_ROOT = os.environ.get(
    CONVCACHE_ROOT_ENVVAR, expanduser('~/var/cache/convof')
)

class BaseTkCommand(BaseCommand):
  ''' A subclass of `cs.cmdutils.BaseCommand`
      intended for commands with a GUI.
      This prepares a Tk top level widget in `run_context()` as
      `self.options.tk_app` and calls its `mainloop()` at the end
      of `run()` if the command returns `None`.
      By default the top level widget is a `tk.Frame`
      unless overridden in `__init__` by the `widget_class` keyword parameter.
  '''

  DEFAULT_WIDGET_CLASS = tk.Frame

  def __init__(self, argv=None, *, widget_class=None, **kw):
    ''' Initialise the command as for `BaseCommand.__init__`.
        The keyword parameter `widget_class` may be used to specify the top
        level widget class to use instead of `tk.Frame`.
    '''
    if widget_class is None:
      widget_class = self.DEFAULT_WIDGET_CLASS
    super().__init__(argv, **kw)
    self.widget_class = widget_class

  @contextmanager
  def run_context(self):
    ''' In addition to the behaviour of `BaseCommand.run_context()`,
        also create a top level `tk.Frame` as `self.options.tk_app`.
    '''
    with super().run_context():
      app = self.options.tk_app = self.widget_class(Tk())
      X("app.gid...")
      app.grid()
      X("yield")
      yield

  @uses_runstate
  def run(self, runstate: RunState, **kw):
    ''' Run a command.
        Returns the exit status of the command.

        In addition to the behaviour of `BaseCommand.run()`,
        this implementation treats the command return value specially:
        if it is `None` and not `self.options.runstate.cancelled`,
        this context manager will call:

            self.options.tk_app.mainloop()

        on return from the command. This supports commands
        just doing the application setup.
    '''
    xit = super().run(**kw)
    if xit is None:
      # the command did GUI setup - run the app now
      if not runstate.cancelled:
        with runstate:
          self.options.tk_app.mainloop()
    return xit

def ispng(pathname):
  ''' Is `pathname` that of a PNG image?
      Just tests the filename extension at present.
  '''
  return splitext(basename(pathname))[1].lower() == '.png'

@uses_fstags
def image_size(path, *, fstags: FSTags):
  ''' Return the pixel size of the image file at `path`
      as an `(dx,dy)` tuple, or `None` if the contents cannot be parsed.
  '''
  tagged = fstags[path]
  try:
    size = tagged['pil.size']
  except KeyError:
    try:
      with Image.open(path) as im:
        tagged['pil.format'] = im.format
        size = tagged['pil.size'] = im.size
        tagged['mime_type'] = 'image/' + im.format.lower()
    except UnidentifiedImageError as e:
      warning("unhandled image: %s", e)
      size = tagged['pil.size'] = None
  if size is not None:
    size = tuple(size)
  return size

@pfx
@uses_fstags
def imgof(
    path,
    max_size=None,
    *,
    min_size=None,
    fmt='png',
    force=False,
    fstags: FSTags,
):
  ''' Create a version of the image at `path`,
      scaled to fit within some size constraints.
      Return the pathname of the new file.

      Parameters:
      * `max_size`: optional `(width,height)` tuple, default `(1920,1800)`
      * `min_size`: optional `(width,height)` tuple, default half of `max_size`
      * `fmt`: optional output format, default `'png'`
      * `force`: optional flag (default `False`)
        to force recreation of the PNG version and associated cache entry
  '''
  assert '/' not in fmt
  FMT = fmt.upper()
  fmt = fmt.lower()
  if max_size is None:
    max_size = 1920, 1080
  if min_size is None:
    min_size = max_size[0] // 2, max_size[1] // 2
  tagged = fstags[path]
  path = tagged.fspath
  size = image_size(path)
  if size is None:
    # cannot determine source image size
    return None
  # choose a target size
  if size[0] > max_size[0] or size[1] > max_size[1]:
    # scale down
    scale = min(max_size[0] / size[0], max_size[1] / size[1])
    re_size = int(size[0] * scale), int(size[1] * scale)
  elif size[0] < min_size[0] or size[1] < min_size[1]:
    # scale up
    scale = min(min_size[0] / size[0], min_size[1] / size[1])
    re_size = int(size[0] * scale), int(size[1] * scale)
  else:
    re_size = None
    if tagged.get('pil.format').lower() == fmt:
      # already an image of the right size and format
      return path

  def resize(srcpath, dstpath):
    ''' Rescale and format `srcpath`, save as `dstpath`.
    '''
    with Image.open(srcpath) as im:
      if re_size is None:
        pfx_call(im.save, dstpath, FMT)
      else:
        im2 = im.resize(re_size)
        pfx_call(im2.save, dstpath, FMT)

  convsize = re_size or size
  return convof(
      path,
      f'{fmt}/{convsize[0]}x{convsize[1]}',
      resize,
      ext=fmt,
      force=force,
  )

def pngfor(path, **imgof_kw):
  ''' Call `imgof` specifying PNG output.
  '''
  return imgof(path, fmt='png', **imgof_kw)

##@require(lambda x1: x1 >= 0)
@require(lambda dx1: dx1 > 0)
##@require(lambda x2: x2 >= 0)
@require(lambda dx2: dx2 > 0)
@ensure(lambda result, dx1: result is None or result[1] <= dx1)
@ensure(lambda result, dx2: result is None or result[1] <= dx2)
def overlap1(x1, dx1, x2, dx2):
  ''' Compute the overlap of 2 ranges,
      return `None` for no overlap
      or `(overlap_x,overlap_dx)` if they overlap.
  '''
  if dx1 <= 0 or dx2 <= 0:
    # zero width spans cannot overlap
    return None
  if x1 <= x2 < x1 + dx1:
    # span 1 left of span 2 and overlapping
    return x2, min(x1 + dx1, x2 + dx2) - x2
  if x2 <= x1 < x2 + dx2:
    # span 2 left of span 1 and overlapping
    return x1, min(x1 + dx1, x2 + dx2) - x1
  return None

class WidgetGeometry(namedtuple('WidgetGeometry', 'x y dx dy')):
  ''' A geometry tuple and associated methods.
  '''

  @classmethod
  def of(cls, w):
    ''' The geometry of the widget `w` in root coordinates.
    '''
    x, y = w.winfo_rootx(), w.winfo_rooty()
    dx, dy = w.winfo_width(), w.winfo_height()
    return cls(x, y, dx, dy)

  def overlap(self, other):
    ''' Compute an overlap rectangle between two `WidgetGeometry` objects.
        Returns `None` if there is no overlap,
        otherwise a new `WidgetGeometry` indicating the overlap.
    '''
    # compute the horizontal overlap
    if self.dx <= 0 or self.dy <= 0:
      return None
    over = overlap1(self.x, self.dx, other.x, other.dx)
    if over is None:
      return None
    over_x, over_dx = over
    # compute the vertical overlap
    over = overlap1(self.y, self.dy, other.y, other.dy)
    if over is None:
      return None
    over_y, over_dy = over
    return type(self)(over_x, over_y, over_dx, over_dy)

class _Widget(ABC):

  WIDGET_BACKGROUND = 'black'
  WIDGET_FOREGROUND = 'white'

  ##@pfx_method
  @typechecked
  def __init__(self, parent, *tk_a, key=None, fixed_size=None, **tk_kw):
    # apply the type(self).WIDGET_* defaults if not present
    for attr in dir(self):
      K = cutprefix(attr, 'WIDGET_')
      if K is not attr:
        v = getattr(self, attr)
        if v is not None:
          tk_kw.setdefault(K.lower(), v)
    ##X("CALLER = %s", caller())
    ##X("type(self)=%r", type(self))
    ##X("type(parent)=%s", type(parent))
    ##X("parent=%s", r(parent))
    ##X(
    ##    "%s: _Widget.__init__: super().__init__(parent=%s,*a=%r,**kw=%r)...",
    ##    type(self), r(parent), a, kw
    ##)
    if fixed_size is None:
      fixed_size = (None, None)
    self.__parent = parent
    self.fixed_size = fixed_size
    if self.fixed_width is not None:
      tk_kw.update(width=self.fixed_width)
    if self.fixed_height is not None:
      tk_kw.update(height=self.fixed_height)
    if key is None:
      key = uuid4()
    self.key = key
    super().__init__(parent, *tk_a, **tk_kw)

  @property
  def parent(self):
    ''' The widget's parent as noted at initialisation.
    '''
    return self.__parent

  @property
  def fixed_width(self):
    ''' The width componet of the `fixed_size`.
    '''
    return self.fixed_size[0]

  @property
  def fixed_height(self):
    ''' The height componet of the `fixed_size`.
    '''
    return self.fixed_size[1]

  def is_visible(self):
    ''' Is this widget visible:
        - it and all ancestors are mapped
        - its rectangle overlaps its parent
        - its parent is visible
    '''
    ##assert self.winfo_ismapped() is self.winfo_viewable(), (
    ##    "%s.winfo_ismapped()=%s but %s.winfo_viewable()=%s" %
    ##    (self, self.winfo_ismapped(), self, self.winfo_viewable())
    ##)
    if not self.winfo_ismapped() or not self.winfo_viewable():
      return None
    g = WidgetGeometry.of(self)
    overlap = None
    p = self.parent
    while p:
      # compare geometry
      pg = WidgetGeometry.of(p)
      overlap = g.overlap(pg)
      if not overlap:
        return False
      try:
        p = p.parent
      except AttributeError as e:
        break
    assert hasattr(p, 'state'), "no .state on %s" % (p,)
    return p.state() == 'normal'

# local shims for the tk and ttk widgets
BaseButton = tk.Button
is_tk_button = True
if is_darwin:
  try:
    from tkmacosx import Button as BaseButton
    is_tk_button = False
  except ImportError as import_e:
    warning(
        "import tkmacosx: %s; buttons will look better with tkmacos on Darwin",
        import_e
    )

# pylint: disable=too-many-ancestors
class Button(_Widget, BaseButton):
  ''' Button `_Widget` subclass.
  '''

  def __init__(self, *a, background=None, highlightbackground=None, **kw):
    if background is None:
      background = self.WIDGET_BACKGROUND
      if not is_tk_button:
        kw.update(background=background)
    if highlightbackground is None:
      highlightbackground = background
    kw.update(highlightbackground=highlightbackground)
    super().__init__(*a, **kw)

# pylint: disable=too-many-ancestors
class Canvas(_Widget, tk.Canvas):
  ''' Canvas `_Widget` subclass.
  '''

  WIDGET_FOREGROUND = None

  def scroll_bbox_x(self):
    ''' Configure the canvas height and scrollregion for the current contents.
    '''
    bbox = self.bbox("all")
    self.configure(scrollregion=bbox)
    self.configure(height=bbox[3])

# pylint: disable=too-many-ancestors
class Combobox(_Widget, ttk.Combobox):
  ''' Combobox `_Widget` subclass.
  '''

# pylint: disable=too-many-ancestors
class Entry(_Widget, tk.Entry):
  ''' Entry `_Widget` subclass.
  '''

# pylint: disable=too-many-ancestors
class Frame(_Widget, tk.Frame):
  ''' Frame `_Widget` subclass.
  '''

  WIDGET_FOREGROUND = None

# pylint: disable=too-many-ancestors
class Label(_Widget, tk.Label):
  ''' Label `_Widget` subclass.
  '''

# pylint: disable=too-many-ancestors
class LabelFrame(_Widget, tk.LabelFrame):
  ''' LabelFrame `_Widget` subclass.
  '''

# pylint: disable=too-many-ancestors
class Listbox(_Widget, tk.Listbox):
  ''' Listbox `_Widget` subclass.
  '''

# pylint: disable=too-many-ancestors
class PanedWindow(_Widget, tk.PanedWindow):
  ''' PanedWindow `_Widget` subclass.
  '''

# pylint: disable=too-many-ancestors
class Scrollbar(_Widget, tk.Scrollbar):
  ''' Scrollbar `_Widget` subclass.
  '''
  WIDGET_FOREGROUND = None

# pylint: disable=too-many-ancestors
class Text(_Widget, tk.Text):
  ''' Text `_Widget` subclass.
  '''

class _ImageWidget(_Widget):

  def __init__(self, parent, *, path, **kw):
    ''' Initialise the image widget to display `path`.
    '''
    kw.setdefault('bitmap', 'gray25')
    kw.setdefault('text', shortpath(path) if path else "NONE")
    super().__init__(parent, **kw)
    self.fspath = path
    self.image = None
    self._image_for = None

  @property
  def fspath(self):
    ''' The filesystem path of the current display.
    '''
    return self._fspath

  @fspath.setter
  def fspath(self, new_fspath):
    self._fspath = new_fspath
    self.configure(text=new_fspath or r(new_fspath))

    def ev_set_image(ev):
      ''' Set the image once visible, fired at idle time.

          It is essential that this is queued with `after_idle`.
          If this ran directly during widget construction
          the `wait_visibility` call would block the follow construction.
      '''
      if self._image_for == self._fspath:
        return
      if not self.is_visible():
        return
      imgpath = self._fspath
      if imgpath is None:
        display_fspath = None
      else:
        size = self.fixed_size or (self.width, self.height)
        try:
          display_fspath = pngfor(expanduser(imgpath), max_size=size)
        except (OSError, ValueError) as e:
          warning("%r: %s", imgpath, e)
          display_fspath = None
      if display_fspath is None:
        self._image_for = None
        self.image = None
        self.configure(image=None)  # pylint: disable=no-member
      else:
        img = Image.open(display_fspath)
        image = ImageTk.PhotoImage(img)
        self.configure(
            text=basename(imgpath),
            image=image,
            width=size[0],
            height=size[1],
        )  # pylint: disable=no-member
        self.image = image
        self._image_for = self._fspath

    self.bind('<Configure>', ev_set_image)  # pylint: disable=no-member
    self.bind('<Map>', ev_set_image)  # pylint: disable=no-member
    self.bind('<Unmap>', ev_set_image)  # pylint: disable=no-member

class ImageWidget(_ImageWidget, Label):
  ''' An image widget which can show anything Pillow can read.
  '''

class ImageButton(_ImageWidget, tk.Button):
  ''' An image button which can show anything Pillow can read.
  '''

class TaggedPathSetVar(tk.Variable, Promotable):
  ''' A subclass for `tk.StringVar` maintaining a `TaggedPathSet`.
  '''

  @promote
  @typechecked
  def __init__(self, paths: TaggedPathSet = None, *, display=None):
    ''' Initialise the `TaggedPathSetVar`.
        If the optional `paths` is supplied, use that as the backing store.
    '''
    super().__init__()
    self.display = display or shortpath
    if paths is None:
      paths = TaggedPathSet()
    self.taggedpaths = paths

  @trace
  def get_str(self):
    ''' Get the current display paths in the expected `('v1',...)` form.
    '''
    return ''.join(
        '(',
        ','.join(repr(self.display(path.fspath)) for path in self.taggedpaths),
        ')',
    )

  def set(self, new_paths=Iterable[str]):
    ''' Update the list of paths.
    '''
    paths = self.taggedpaths
    paths.clear()
    paths.update(new_paths)
    super().set(' '.join(self.display(path.fspath) for path in paths))

  @classmethod
  def promote(cls, obj):
    ''' Promote an object to a `TaggedPathSet`.
    '''
    if isinstance(obj, cls):
      return obj
    return cls(paths=obj)

class HasTaggedPathSet:
  ''' A mixin with methods for maintaining a list of filesystem paths.
      This maintains a `.taggedpaths` attribute holding a `TaggedPathSet`.
  '''

  @promote
  def __init__(
      self,
      paths: TaggedPathSetVar = None,
      *,
      display=None,
  ):
    ''' Initialise `self.pathsvar`.

        Parameters:
        * `paths`: used to intialise `.taggedpaths`, optional; an existing
          `TaggedPathSet` or an iterable of `str` or `TaggedPath`
        * `display`: optional callable computing the friendly form of a path,
          default `cs.fs.shortpath`
    '''
    if paths is None:
      paths = TaggedPathSetVar(paths=paths, display=display)
    elif display is not None:
      paths.display - display
    self.pathsvar = paths

  @property
  def taggedpaths(self):
    ''' The `TaggedPathSet` instance.
    '''
    return self.pathsvar.taggedpaths

  @property
  def fspaths(self):
    ''' A list of the filesystem paths.
    '''
    return [path.fspath for path in self.taggedpaths]

  @fspaths.setter
  def fspaths(self, new_fspaths):
    ''' Setting `.fspaths` calls `self.set(new_fspaths)`.
    '''
    self.set(new_fspaths)

  @property
  def display(self):
    ''' The display function.
    '''
    return self.pathsvar.display

  def display_paths(self):
    ''' Return a list of the friendly form of each path.
    '''
    display = self.display
    return [display(path.fspath) for path in self.taggedpaths]

  def set(self, paths):
    ''' Set the `paths`.
    '''
    self.pathsvar.set(paths)

class PathList_Listbox(Listbox, HasTaggedPathSet):
  ''' A `Listbox` displaying filesystem paths.
  '''

  @promote
  @typechecked
  def __init__(
      self, parent, paths: TaggedPathSetVar = None, *, command, **listbox_kw
  ):
    HasTaggedPathSet.__init__(self, paths=paths)
    Listbox.__init__(self, parent, listvariable=self.pathsvar, **listbox_kw)
    self.command = command
    self.bind('<Button-1>', self._clicked)

  def _clicked(self, event):
    ''' Call `self.command(nearest_index, self.fspaths[nearest_index])`.
    '''
    nearest = self.nearest(event.y)
    self.command(nearest, self.fspaths[nearest])

  def _update_display(self):
    ''' Update the displayed list, to be called when `self.taggedpaths` is modified.
    '''
    display_paths = self.display_paths()
    self.pathsvar.set(' '.join(display_paths))
    if self.fixed_width is None:
      self.config(
          width=(max(map(len, display_paths)) if display_paths else 0) + 10
      )

  def setpaths(self, paths):
    self.pathsvar.set(paths)

  @pfx_method
  @promote
  def show_fspath(self, fspath: TaggedPath, select=False):
    ''' Adjust the list box so that `fspath` is visible.
    '''
    paths = self.taggedpaths
    if fspath not in paths:
      paths.add(fspath)
      self._update_display()
    index = paths.find(fspath)
    self.see(index)
    if select:
      self.selection_clear(0, len(self.taggedpaths) - 1)
      self.selection_set(index)

class TagValueStringVar(tk.StringVar):
  ''' A `StringVar` which holds a `Tag` value transcription.
  '''

  def __init__(self, value, **kw):
    ''' Initialise the `TagValueStringVar` with `value`.
        Keyword arguments are passed to `tk.StringVar.__init__`.
    '''
    super().__init__(master=None, value=None, **kw)
    self.set(value)

  def set(self, value):
    ''' Set the contents to `Tag.transcribe_value(value)`.
    '''
    super().set(Tag.transcribe_value(value))

  @pfx_method
  def get(self):
    ''' Return the value derived from the contents via `Tag.parse_value`.
        An attempt is made to cope with unparsed values.
    '''
    value0 = super().get()
    try:
      value, offset = pfx_call(Tag.parse_value, value0)
    except ValueError as e:
      warning(str(e))
      value = value0
    else:
      if offset < len(value0):
        warning("unparsed: %r", value0[offset:])
        if isinstance(value, str):
          value += value0[offset:]
        else:
          value = value0
    return value

class EditValueWidget(Frame):
  ''' A widget to edit a `Tag` value,
      a `Frame` containing a value specific editing widget.
  '''

  def __init__(self, parent, value, alt_values=None, **kw):
    super().__init__(parent, **kw)
    if value is None:
      value = ""
    if alt_values and not isinstance(value, (dict, list)):
      tv = TagValueStringVar(value)
      edit_widget = Combobox(self, textvariable=tv, values=sorted(alt_values))
      if value is None or value == "":
        edit_widget.set(sorted(alt_values)[0])
      get_value = lambda _: tv.get()
    elif isinstance(value, str):
      if '\n' in value:
        edit_widget = Text(self)
        edit_widget.insert(tk.END, value)
        get_value = lambda w: w.get(1.0, tk.END).rstrip('\n')
      else:
        tv = TagValueStringVar(value)
        edit_widget = Entry(self, textvariable=tv)
        get_value = lambda _: tv.get()
    elif isinstance(value, (int, float)):
      tv = TagValueStringVar(value)
      edit_widget = Entry(self, textvariable=tv)
      get_value = lambda _: tv.get()
    else:
      edit_text = Tag.transcribe_value(
          value,
          json_options=dict(indent=2, sort_keys=True, ensure_ascii=False)
      )
      edit_widget = Text(self)
      edit_widget.insert(tk.END, edit_text)

      def get_value(w):
        ''' Obtain the new value from the widget contents.
        '''
        edited = w.get(1.0, tk.END).rstrip('\n')
        try:
          new_value, offset = pfx_call(Tag.parse_value, edited)
        except ValueError as e:
          warning("toggle_editmode: %s", e)
          new_value = edited
        else:
          if offset < len(edited):
            warning("unparsed: %r", edited[offset:])
            if isinstance(new_value, str):
              new_value += edited[offset:]
            else:
              new_value = edited
        return new_value

    edit_widget.grid(sticky=tk.W + tk.E + tk.N + tk.S)
    self.edit_widget = edit_widget
    self.get = lambda: get_value(edit_widget)

  def focus_set(self):
    ''' Setting the focus should focus the `edit_widget`.
    '''
    self.edit_widget.focus_set()

  @staticmethod
  def _parse_value(value_s):
    try:
      value, offset = pfx_call(Tag.parse_value, value_s)
    except ValueError as e:
      warning("EditValue._parse_value(%s): %s", r(value_s), e)
      value = value_s
    else:
      if offset < len(value_s):
        warning("unparsed: %r", value_s[offset:])
        if isinstance(value, str):
          value += value_s[offset:]
        else:
          value = value_s
    return value

class ThumbNailScrubber(Frame, HasTaggedPathSet):
  ''' A row of thumbnails for a list of filesystem paths.
  '''

  THUMB_X = 64
  THUMB_Y = 64

  def __init__(self, parent, fspaths: List[str], *, command, **frame_kw):
    Frame.__init__(self, parent, **frame_kw)
    HasTaggedPathSet.__init__(self, fspaths)
    self.command = command
    self.make_subwidget = (
        lambda i, path: ImageButton(
            self,
            path=expanduser(path),
            command=lambda i=i, path=path: self.command(i, expanduser(path)),
            fixed_size=(self.THUMB_X, self.THUMB_Y),
        )
    )
    self.fspaths = fspaths

  def set_fspaths(self, new_fspaths):
    ''' Update the list of filesystem paths.
    '''
    paths = self.taggedpaths
    paths.clear()
    paths.update(new_fspaths)
    display_paths = self.display_paths()
    for child in list(self.grid_slaves()):
      child.grid_remove()
    for i, display_path in enumerate(display_paths):
      thumbnail = self.make_subwidget(i, display_path)
      thumbnail.grid(column=i, row=0)

  @property
  def fspaths(self):
    ''' The list of filesystem paths.
    '''
    return [path.fspath for path in self.taggedpaths]

  @fspaths.setter
  def fspaths(self, new_paths):
    ''' Set the list of filesystem paths.
    '''
    self.set_fspaths(new_paths)

  @pfx_method
  def show_fspath(self, fspath):
    ''' TODO: bring the correspnding thumbnail into view.
    '''
    warning("UNIMPLEMENTED: scrubber thumbnail not yet scrolled into view")
