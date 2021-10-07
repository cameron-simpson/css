#!/usr/bin/env python3

''' Tkinter based GUI for a `Tagger`.
'''

from abc import ABC
from collections import namedtuple
from contextlib import contextmanager
from os.path import (
    abspath,
    basename,
    expanduser,
)
import platform
from time import sleep
import tkinter as tk
from tkinter import ttk
from typing import Iterable, List
from uuid import uuid4

from icontract import require, ensure
from PIL import Image, ImageTk
from typeguard import typechecked

from cs.context import stackattrs
from cs.fileutils import shortpath
from cs.lex import cutprefix
from cs.logutils import warning
from cs.pfx import pfx, pfx_method, pfx_call
from cs.resources import MultiOpenMixin, RunState
from cs.tagset import Tag, TagSet

from cs.lex import r
from cs.x import X

from .util import pngfor

is_darwin = platform.system() == "Darwin"

_app = None

def _render(pause=0):
  _app.update_idletasks()
  if pause > 0:
    sleep(pause)

# pylint: disable=too-many-instance-attributes
class TaggerGUI(MultiOpenMixin):
  ''' A GUI for a `Tagger`.
  '''

  def __init__(self, tagger, fspaths=None):
    if fspaths is None:
      fspaths = ()
    self._fspaths = fspaths
    self._fspath = None
    self.tagger = tagger
    # widget references
    self.app = None
    self.pathlist = None
    self.thumbsview = None
    self.pathview = None
    self.thumbscanvas = None

  def __str__(self):
    return "%s(%s)" % (type(self).__name__, self.tagger)

  @property
  def fspaths(self):
    ''' The current list of filesystem paths.
    '''
    return self._fspaths

  @fspaths.setter
  def fspaths(self, new_fspaths):
    ''' Update the current list of filesystem paths.
    '''
    self._fspaths = list(new_fspaths)
    if self.pathlist is not None:
      self.pathlist.set_fspaths(self._fspaths)
    if self.thumbsview is not None:
      self.thumbsview.set_fspaths(self._fspaths)
      self.thumbscanvas.after_idle(self.thumbscanvas.scroll_bbox_x)

  @property
  def fspath(self):
    ''' The currently displayed filesystem path.
    '''
    return self._fspath

  @fspath.setter
  def fspath(self, new_fspath):
    self._fspath = new_fspath
    if self.pathview is not None:
      # display new_fspath
      self.pathview.fspath = new_fspath
    if self.pathlist is not None:
      # scroll to new_fspath
      self.pathlist.show_fspath(new_fspath, select=True)
    if self.thumbsview is not None:
      # scroll to new_fspath
      self.thumbsview.show_fspath(new_fspath)

  @contextmanager
  def startup_shutdown(self):
    global _app  # pylint: disable=global-statement
    root = tk.Tk()
    _app = app = Frame(root)
    app.grid()

    # Define the window's contents
    def select_path(_, path):
      self.fspath = path

    pathlist = PathList_Listbox(
        app,
        self.fspaths,
        command=select_path,
    )
    pathlist.grid(column=0, row=0, sticky=tk.N + tk.S, rowspan=2)
    pathview = PathView(app, tagger=self.tagger)
    pathview.grid(column=1, row=0, sticky=tk.N + tk.S)

    thumbscanvas = Canvas(app)
    thumbscanvas.grid(column=0, columnspan=2, sticky=tk.W + tk.E)

    thumbsscroll = Scrollbar(
        app,
        orient=tk.HORIZONTAL,
        command=thumbscanvas.xview,
    )
    thumbsscroll.grid(column=0, columnspan=2, sticky=tk.W + tk.E)
    thumbscanvas['xscrollcommand'] = thumbsscroll.set

    # let the geometry settle
    app.update_idletasks()

    thumbsview = ThumbNailScrubber(
        thumbscanvas,
        (),
        command=select_path,
    )
    thumbscanvas.create_window(
        thumbsscroll.winfo_width() / 2, 0, anchor=tk.N, window=thumbsview
    )

    # attach widget references
    with stackattrs(
        self,
        app=app,
        pathlist=pathlist,
        pathview=pathview,
        thumbscanvas=thumbscanvas,
        thumbsview=thumbsview,
    ):
      # set fspaths with side effects to widgets
      with stackattrs(self, fspaths=self.fspaths):
        # set current display with side effects to widgets
        with stackattrs(self,
                        fspath=self.fspaths[0] if self.fspaths else None):
          yield app

  def run(self, runstate=None):
    ''' Run the GUI.
    '''
    print("run...")
    if runstate is None:
      runstate = RunState(str(self))
    with runstate:
      print("before mainloop")
      self.app.mainloop()
      print("after mainloop")

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
  if x1 <= x2 and x1 + dx1 > x2:
    # span 1 left of span 2 and overlapping
    return x2, min(x1 + dx1, x2 + dx2) - x2
  if x2 <= x1 and x2 + dx2 > x1:
    # span 2 left of span 1 and overlapping
    return x1, min(x1 + dx1, x2 + dx2) - x1
  return None

class WidgetGeometry(namedtuple('WidgetGeometry', 'x y dx dy')):
  ''' A geometry tuple and associated methods.
  '''

  @classmethod
  def of(cls, w):
    ''' The geometry of this widget in root coordinates.
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

  @pfx_method
  def __init__(self, parent, *a, key=None, fixed_size=None, **kw):
    for attr in dir(self):
      K = cutprefix(attr, 'WIDGET_')
      if K is not attr:
        v = getattr(self, attr)
        if v is not None:
          kw.setdefault(K.lower(), v)
    if fixed_size is None:
      fixed_size = (None, None)
    self.__parent = parent
    self.fixed_size = fixed_size
    if self.fixed_width is not None:
      kw.update(width=self.fixed_width)
    if self.fixed_height is not None:
      kw.update(height=self.fixed_height)
    super().__init__(parent, *a, **kw)
    if key is None:
      key = uuid4()
    self.key = key

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
    assert self.winfo_ismapped() is self.winfo_viewable(), (
        "%s.winfo_ismapped()=%s but %s.winfo_viewable()=%s" %
        (self, self.winfo_ismapped(), self, self.winfo_viewable())
    )
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
  except ImportError as e:
    warning(
        "import tkmacosx: %s; buttons will look better with tkmacos on Darwin",
        e
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
          display_fspath = pngfor(expanduser(imgpath), size)
        except (OSError, ValueError) as e:
          warning("%r: %s", imgpath, e)
          display_fspath = None
      if display_fspath is None:
        self._image_for = None
        self.image = None
        self.configure(image=None)
      else:
        img = Image.open(display_fspath)
        image = ImageTk.PhotoImage(img)
        self.configure(
            text=basename(imgpath),
            image=image,
            width=size[0],
            height=size[1],
        )
        self.image = image
        self._image_for = self._fspath

    self.bind('<Configure>', ev_set_image)
    self.bind('<Map>', ev_set_image)
    self.bind('<Unmap>', ev_set_image)

class ImageWidget(_ImageWidget, Label):
  ''' An image widget which can show anything Pillow can read.
  '''

class ImageButton(_ImageWidget, tk.Button):
  ''' An image button which can show anything Pillow can read.
  '''

class _FSPathsMixin:
  ''' A mixin with methods for maintaining a list of filesystem paths.
  '''

  def __init__(self, canonical=None, display=None):
    if canonical is None:
      canonical = abspath
    if display is None:
      display = shortpath
    self.__canonical = canonical
    self.__display = display
    self.__index_by_canonical = {}
    self.__index_by_display = {}

  def __clear(self):
    self.__index_by_canonical.clear()
    self.__index_by_display.clear()

  def set_fspaths(self, new_fspaths):
    ''' Update the canonical and display index mappings.
        Return a list of the display paths.
    '''
    fspaths = list(new_fspaths)
    display_paths = []
    self.__clear()
    for i, fspath in enumerate(fspaths):
      cpath = self.__canonical(fspath)
      dpath = self.__display(fspath)
      display_paths.append(dpath)
      self.__index_by_canonical[cpath] = i
      self.__index_by_display[dpath] = i
    return display_paths

  @pfx_method
  def index_by_path(self, path):
    ''' Return the index for path via its display or canonical forms,
        or `None` if no match.
    '''
    dpath = self.__display(path)
    try:
      index = self.__index_by_display[dpath]
    except KeyError:
      cpath = self.__canonical(path)
      try:
        index = self.__index_by_canonical[cpath]
      except KeyError:
        warning("no index found, tried display=%r, canonical=%r", dpath, cpath)
        index = None
    return index

class PathList_Listbox(Listbox, _FSPathsMixin):
  ''' A `Listbox` displaying filesystem paths.
  '''

  @typechecked
  def __init__(self, parent, pathlist: List[str], *, command, **kw):
    super().__init__(parent, **kw)
    _FSPathsMixin.__init__(self)
    self.command = command
    self.pathlist = pathlist
    self.display_paths = None
    self._list_state = None
    self.bind('<Button-1>', self._clicked)

  def _clicked(self, event):
    nearest = self.nearest(event.y)
    self.command(nearest, expanduser(self.display_paths[nearest]))

  def set_fspaths(self, new_fspaths):
    ''' Update the filesystem paths.
    '''
    self.display_paths = super().set_fspaths(new_fspaths)
    list_state = getattr(self, '_list_state', None)
    if list_state is None:
      list_state = self._list_state = tk.StringVar(value=self.display_paths)
      self.config(listvariable=list_state)
    list_state.set(self.display_paths)
    if self.fixed_width is None:
      self.config(width=max(map(len, self.display_paths)) + 10)
    return self.display_paths

  @property
  def pathlist(self):
    ''' Return the currently displayed path list.
    '''
    return self.display_paths

  @pathlist.setter
  def pathlist(self, new_paths: Iterable[str]):
    ''' Update the path list.
    '''
    self.set_fspaths(new_paths)

  @pfx_method
  def show_fspath(self, fspath, select=False):
    ''' Adjust the list box so that `fspath` is visible.
    '''
    index = self.index_by_path(fspath)
    if index is None:
      warning("cannot show this path")
    else:
      self.see(index)
      if select:
        self.selection_clear(0, len(self.display_paths) - 1)
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
    s = super().get()
    try:
      value, offset = pfx_call(Tag.parse_value, s)
    except ValueError as e:
      warning(str(e))
      value = s
    else:
      if offset < len(s):
        warning("unparsed: %r", s[offset:])
        if isinstance(value, str):
          value += s[offset:]
        else:
          value = s
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

class TagWidget(Frame):
  ''' A display for a `Tag`.
  '''

  @typechecked
  def __init__(
      self, parent, tags: TagSet, tag: Tag, *, tagger, alt_values=None, **kw
  ):
    ''' Initialise a `TagWidget`.

        Parameters:
        * `parent`: the parent widget
        * `tags`: the reference `TagSet`;
          note that `tag` might not be present in `tags`
        * `tag`: the `Tag` to render
        * `alt_values`: optional iterable of alternate values;
          scalar values will have these presented as prefill options
          in edit mode
        Other keyword arguments are passed to the `Frame` superclass initialiser.
    '''
    self.tagger = tagger
    if alt_values is None:
      alt_values = set(tagger.ont_values(tag.name))
    else:
      alt_values = set(alt_values)
    super().__init__(parent, **kw)
    self.tags = tags
    self.tag = tag
    self.alt_values = alt_values
    self.label = Button(
        self,
        command=self.toggle_editmode,
        ##relief=tk.FLAT,
        ##overrelief=tk.FLAT,
        background='brown',
        highlightbackground='black',
        padx=0,
        pady=0,
        borderwidth=0,
    )
    self._set_colour()
    self._set_text()
    self.label.grid(column=0, row=0, sticky=tk.W)
    self.editor = None

  def _set_colour(self):
    self.label.configure(
        foreground='green' if self.tag.name in self.tags else 'gray',
    )

  def _set_text(self, new_text=None):
    if not new_text:
      tag = self.tag
      if not tag.value and self.alt_values:
        new_text = f"{tag.name} ? {', '.join(sorted(map(str,self.alt_values)))}"
      else:
        new_text = str(tag)
    self.label.configure(text=new_text)

  def toggle_editmode(self):
    ''' Present or withdraw the edit widget.

        The `Tag` is updated when the widget is withdrawn,
        and if the new value does not match the value in `self.tags`
        then the correponding `self.tags[tag.name]` is also updated.
    '''
    tag = self.tag
    tag_value = tag.value
    if self.editor is None:
      self.editor = EditValueWidget(
          self, tag_value, alt_values=self.alt_values
      )
      self.editor.grid(
          column=0, row=1, columnspan=2, sticky=tk.W + tk.E + tk.N + tk.S
      )
      self.editor.focus_set()
    else:
      # withdraw edit widget, update tag and label
      new_value = self.editor.get()
      self.editor.grid_remove()
      self.editor = None
      self.tag = Tag(self.tag.name, new_value, ontology=self.tag.ontology)
      if new_value != self.tags.get(self.tag.name):
        self.tags[self.tag.name] = new_value
        self._set_colour()
        self._set_text()

class _TagsView(_Widget):
  ''' A view of some `Tag`s.
  '''

  def __init__(self, parent, *, get_tag_widget=None, **kw):
    super().__init__(parent, **kw)
    # the working TagSet, distinct from those supplied
    self.tags = TagSet()
    # a function to get Tag suggestions from a Tag name
    self.get_suggested_tag_values = lambda tag_name: ()
    # a reference TagSet of background Tags
    self.bg_tags = TagSet()
    if get_tag_widget is None:
      get_tag_widget = TagWidget
    self.get_tag_widget = get_tag_widget

  def set_tags(self, tags, get_suggested_tag_values=None, bg_tags=None):
    ''' Update `self.tags` to match `tags`.
        Optionally set `self.get_suggested_tag_values`
        if `get_suggested_tag_values` is not `None`.
        Optionally set `self.bg_tags` if `bg_tags` is not `None`.
    '''
    self.tags.clear()
    self.tags.update(tags)
    if get_suggested_tag_values is not None:
      self.get_suggested_tag_values = get_suggested_tag_values
    if bg_tags is not None:
      self.bg_tags = bg_tags

class TagsView(_TagsView, LabelFrame):
  ''' A view of some `Tag`s.
  '''

  def __init__(self, parent, *, tagger, **kw):
    kw.setdefault('text', 'Tags')
    super().__init__(parent, **kw)
    self.tagger = tagger
    self.set_tags(())
    # mapping of tag name to widgets
    self._tag_widgets = {}
    # list of tag names in grid row order
    self._tag_names = []

  # TODO: general OrderedFrameMixin?
  @require(lambda self, widget: widget.parent is self)
  @require(lambda self: self._tag_names == sorted(self._tag_names))
  @ensure(lambda self: self._tag_names == sorted(self._tag_names))
  @typechecked
  def _add_tag(self, tag_name: str, widget: _Widget):
    ''' Insert `widget` for the `Tag` named `tag_name`
        at the appropriate place in the listing.
    '''
    w = self._tag_widgets.get(tag_name)
    if w is not None:
      # switch out the old widget
      grid_info = w.grid_info()
      w.grid_forget()
      widget.grid(**grid_info)
    else:
      # insert the new widget
      # ... by forgetting them all and appending
      # because it looks like you can't insert into a grid
      # or make a gap
      # find the insertion row
      for row, gridded_name in enumerate(self._tag_names):
        if gridded_name > tag_name:
          break
      else:
        # append to the end
        row = len(self._tag_names)
      ws = [widget]
      # move later rows down
      for name in self._tag_names[row:]:
        w = self._tag_widgets[name]
        grid_info = w.grid_info()
        w.grid_remove()
        ws.append(w)
      for grow, w in enumerate(ws, row):
        w.grid(row=grow)
      self._tag_names.insert(row, tag_name)
    self._tag_widgets[tag_name] = widget

  @require(lambda self: self._tag_names == sorted(self._tag_names))
  @ensure(lambda self: self._tag_names == sorted(self._tag_names))
  @typechecked
  def _del_tag(self, tag_name: str):
    ''' Delete the widget for the `Tag` named `tag_name` if present.
    '''
    w = self._tag_widgets.get(tag_name)
    if w is not None:
      i = self._tag_names.index(tag_name)
      del self._tag_names[i]
      del self._tag_widgets[tag_name]
      w.grid_remove()

  def tag_widget(self, tag, alt_values=None, **kw):
    ''' Create a new `TagWidget` for the `Tag` `tag`.
    '''
    return TagWidget(
        self,
        self.tags,
        tag,
        tagger=self.tagger,
        alt_values=alt_values,
        **kw,
    )

  def set_tags(self, tags, get_suggested_tag_values=None, bg_tags=None):
    old_tags = list(self.tags)
    super().set_tags(
        tags,
        get_suggested_tag_values=get_suggested_tag_values,
        bg_tags=bg_tags
    )
    display_tags = TagSet(self.tags)
    if bg_tags:
      # fill in background tags if not present
      for tag_name, tag_value in bg_tags.items():
        if tag_name not in tags:
          display_tags[tag_name] = tag_value
    # remove tags no longer named
    for tag in old_tags:
      if tag.name not in display_tags:
        self._del_tag(tag.name)
    # redo the displayed tags
    # TODO: update the widgets directly instead
    for tag in display_tags:
      alt_values = self.get_suggested_tag_values(tag)
      w = self.tag_widget(tag, alt_values=alt_values)
      self._add_tag(tag.name, w)
      w.grid(sticky=tk.W)

class PathView(LabelFrame):
  ''' A preview of a filesystem path.
  '''

  def __init__(self, parent, fspath=None, *, tagger, **kw):
    kw.setdefault('text', fspath or 'NONE')
    super().__init__(parent, **kw)
    self._fspath = fspath
    self.tagger = tagger
    # path->set(suggested_tags)
    self._suggested_tags = {}
    # tag_name->TagWidget
    self._tag_widgets = {}
    self.preview = ImageWidget(
        self,
        path=fspath,
        fixed_size=(1920, 1080),
    )
    self.preview.grid(column=0, row=0)

    self.tagsview = TagsView(
        self,
        tagger=tagger,
        fixed_size=(200, None),
    )
    self.tagsview.grid(column=1, row=0, sticky=tk.N + tk.S)

  @property
  def fspath(self):
    ''' The current filesystem path being previewed.
    '''
    return self._fspath

  @fspath.setter
  @pfx
  def fspath(self, new_fspath):
    ''' Switch the preview to look at a new filesystem path.
    '''
    print("SET fspath =", repr(new_fspath))
    self._fspath = new_fspath
    self._tag_widgets = {}
    self.config(text=shortpath(new_fspath) or "NONE")
    self.preview.fspath = new_fspath
    tagged = self.tagged
    all_tags = TagSet(tagged.merged_tags())
    suggested_tags = self.suggested_tags
    for sg_name in suggested_tags.keys():
      if sg_name not in all_tags:
        all_tags[sg_name] = None
    self.tagsview.set_tags(
        tagged, lambda tag: suggested_tags.get(tag.name), bg_tags=all_tags
    )
    print("tag suggestions =", repr(self.suggested_tags))

  @property
  def suggested_tags(self):
    ''' A mapping of `tag_name`=>`set(tag_value)` for the current fspath.
    '''
    fspath = self._fspath
    if fspath is None:
      return {}
    try:
      suggestions = self._suggested_tags[fspath]
    except KeyError:
      suggestions = self._suggested_tags[fspath] = self.tagger.suggested_tags(
          fspath
      )
    return suggestions

  @property
  def tagged(self):
    ''' The `TaggedFile` for the currently displayed path.
    '''
    if self._fspath is None:
      return None
    return self.tagger.fstags[self._fspath]

class ThumbNailScrubber(Frame, _FSPathsMixin):
  ''' A row of thumbnails for a list of fielsystem paths.
  '''

  THUMB_X = 64
  THUMB_Y = 64

  def __init__(self, parent, fspaths: List[str], *, command, **kw):
    super().__init__(parent, **kw)
    _FSPathsMixin.__init__(self)
    self.index_by_abspath = {}
    self.index_by_displaypath = {}
    self.command = command
    self.make_subwidget = (
        lambda i, path: ImageButton(
            self,
            path=expanduser(path),
            command=lambda i=i, path=path: self.command(i, expanduser(path)),
            fixed_size=(self.THUMB_X, self.THUMB_Y),
        )
    )
    self._fspaths = None
    self.fspaths = fspaths

  def set_fspaths(self, new_fspaths):
    ''' Update the list of fielsystem paths.
    '''
    display_paths = super().set_fspaths(new_fspaths)
    for child in list(self.grid_slaves()):
      child.grid_remove()
    for i, dpath in enumerate(display_paths):
      thumbnail = self.make_subwidget(i, dpath)
      thumbnail.grid(column=i, row=0)

  @property
  def fspaths(self):
    ''' The list of filesystem paths.
    '''
    return self._fspaths

  @fspaths.setter
  def fspaths(self, new_paths):
    ''' Set the list of filesystem paths.
    '''
    self.set_fspaths(new_paths)

  @pfx_method
  def show_fspath(self, fspath):
    ''' TODO: bring to correspnding thumbnail into view.
    '''
    warning("UNIMPLEMENTED")
