#!/usr/bin/env python3

''' Tkinter based GUI for a `Tagger`.
'''

from abc import ABC
from collections import namedtuple
from contextlib import contextmanager
import os
from os.path import (
    basename,
    isdir as isdirpath,
    isfile as isfilepath,
    join as joinpath,
    realpath,
)
import tkinter as tk
from tkinter import ttk
from typing import Iterable, List, Optional
from uuid import UUID, uuid4

from icontract import require, ensure
from PIL import Image, ImageTk
from typeguard import typechecked

from cs.context import stackattrs
from cs.fileutils import shortpath
from cs.logutils import warning
from cs.mappings import IndexedMapping, UUIDedDict
from cs.pfx import pfx, Pfx, pfx_method
from cs.queues import ListQueue
from cs.resources import MultiOpenMixin, RunState
from cs.tagset import Tag, TagSet

from cs.lex import r
from cs.py.func import trace
from cs.x import X

from .util import ispng, pngfor

class TaggerGUI(MultiOpenMixin):

  def __init__(self, tagger, fspaths):
    self.tagger = tagger
    self.fspaths = fspaths

  def __str__(self):
    return "%s(%s)" % (type(self).__name__, self.tagger)

  @contextmanager
  def startup_shutdown(self):
    root = tk.Tk()
    app = LabelFrame(root, text="APP")

    # Define the window's contents
    @trace
    def select_path(i, path):
      X("SELECT_PATH: i=%r, path=%r", i, path)
      self.pathview.fspath = path

    pathlist = self.pathlist = PathListWidget(
        app, self.fspaths, command=select_path
    )
    pathlist.grid(column=0, row=0, sticky=tk.N)
    pathview = self.pathview = PathView(app, tagger=self.tagger)
    pathview.grid(column=1, row=0, sticky=tk.N)

    thumbview = self.thumbview = ThumbNailScrubber(
        app, self.fspaths, command=select_path
    )
    thumbview.grid(column=0, row=1, columnspan=2)
    pathview.fspath = self.fspaths[0]
    app.grid()
    with stackattrs(self, app=app, pathlist=pathlist, pathview=pathview):
      yield app

  def run(self, runstate=None):
    print("run...")
    if runstate is None:
      runstate = RunState(str(self))
    # Display and interact with the Window using an Event Loop
    app = self.app
    if False:
      for record in self.tree:
        if isfilepath(record.fullpath):
          print("set self.fspath =", repr(record.fullpath))
          self.fspath = record.fullpath
          break
    with runstate:
      print("before mainloop")
      self.app.mainloop()
      print("after mainloop")

  @property
  def fspath(self):
    return self.pathview.fspath

  @fspath.setter
  @pfx
  def fspath(self, new_fspath):
    self.pathview.fspath = new_fspath
    # TODO: make the tree display the associated element
    try:
      pathinfo = self.tree[new_fspath]
    except KeyError:
      warning("path not in tree")

@require(lambda x1: x1 >= 0)
@require(lambda dx1: dx1 > 0)
@require(lambda x2: x2 >= 0)
@require(lambda dx2: dx2 > 0)
@ensure(lambda result, dx1: result[1] <= dx1)
@ensure(lambda result, dx2: result[1] <= dx2)
def overlap1(x1, dx1, x2, dx2):
  ''' Compute the overlap of 2 ranges,
      return `None` for no overlap
      or `(overlap_x,overlap_dx)` if they overlap.
  '''
  x1b = x1 + dx1
  x2b = x2 + dx2
  if x1 < x2:
    if x1b <= x2:
      return None
    return x2, min(x1b, x2b) - x2
  if x2b <= x1:
    return None
  return x1, min(x1b, x2b) - x1

class WidgetGeometry(namedtuple('WidgetGeometry', 'x y dx dy')):
  ''' A geometry tuple and associated methods.
  '''

  def overlap(self, other):
    ''' Compute an overlap rectangle between two `WidgetGeometry` objects.
        Returns `None` if there is no overlap,
        otherwise a new `WidgetGeometry` indicating the overlap.
    '''
    # compute the horizontal overlap
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

  def __init__(self, parent, *a, key=None, fixed_size=None, **kw):
    X("INIT _WIDGET %s, parent=%s %r", type(self).__name__, parent, kw)
    if fixed_size:
      X("FIXED SIZE %s = %r", type(self).__name__, fixed_size)
      kw.update(width=fixed_size[0], height=fixed_size[1])
    self.__parent = parent
    super().__init__(parent, *a, **kw)
    if key is None:
      key = uuid4()
    self.key = key
    self.fixed_size = fixed_size
    ##X("_Widget: call super():%s(*a=%r,**kw=%r)", super(), a, kw)

  def update(self, **kw):
    X("%s.update: kw=%r", type(self).__name__, kw)
    super().update(**kw)
    if self.fixed_size:
      self.set_size(self.fixed_size)
  @property
  def parent(self):
    return self.__parent


# local shims for the tk and ttk widgets
class Button(_Widget, tk.Button):
  pass

class Combobox(_Widget, ttk.Combobox):
  pass

class Frame(_Widget, tk.Frame):
  pass

class Label(_Widget, tk.Label):
  pass

class LabelFrame(_Widget, tk.LabelFrame):
  pass

class Listbox(_Widget, tk.Listbox):
  pass

class PanedWindow(_Widget, tk.PanedWindow):
  pass

class _ImageWidget(_Widget):

  def __init__(self, parent, *, path, **kw):
    ''' Initialise the image widget to display `path`.
    '''
    super().__init__(parent, **kw)
    self.fspath = path

  @property
  def fspath(self):
    ''' The filesystem path of the current display.
    '''
    return self._fspath

  @fspath.setter
  def fspath(self, new_fspath):
    if new_fspath is None:
      display_fspath = None
    else:
      size = self.fixed_size or (self.width, self.height)
      try:
        display_fspath = pngfor(new_fspath, size)
      except (OSError, ValueError) as e:
        warning("%r: %s", new_fspath, e)
        display_fspath = None
    X("display_fspath=%r", display_fspath)
    if display_fspath is None:
      self.config(text=new_fspath, image=None)
    else:
      ##os.system("open %r" % (display_fspath,))
      img = Image.open(display_fspath)
      X("Image.open => %s : %r", img, img.size)
      image = ImageTk.PhotoImage(img)
      self.configure(
          text=basename(new_fspath),
          bd=2,
          background="black",
          ##compound=tk.BOTTOM,
          image=image,
          width=size[0],
          height=size[1],
      )
      self.image = image
    self.grid()
    self._fspath = new_fspath

class ImageWidget(_ImageWidget, Label):
  ''' An image widget which can show anything Pillow can read.
  '''

class ImageButton(_ImageWidget, Button):
  ''' An image button which can show anything Pillow can read.
  '''

class _PathList(_Widget, tk.PanedWindow):

  @typechecked
  def __init__(
      self, parent, pathlist: List[str], *, command, make_subwidget, **kw
  ):
    super().__init__(parent, **kw)
    self.command = command
    self.make_subwidget = make_subwidget
    self.update_pathlist(pathlist)

  def update_pathlist(self, new_paths: Iterable[str]):
    ''' Update the path list.
    '''
    self._pathlist = list(new_paths)
    for child in list(self.panes()):
      self.remove(child)
    for i, path in enumerate(self._pathlist):
      thumbnail = self.make_subwidget(i, path)
      self.add(thumbnail)

  @property
  def pathlist(self):
    ''' Return the current path list.
    '''
    return self._pathlist

  @pathlist.setter
  def pathlist(self, new_paths: Iterable[str]):
    ''' Update the path list.
    '''
    self.updatepathlist(new_paths)

class PathListWidget(_PathList):

  def __init__(self, parent, pathlist: List[str], *, command, **kw):
    super().__init__(
        parent,
        pathlist=pathlist,
        orient=tk.VERTICAL,
        command=command,
        make_subwidget=(
            lambda i, path: tk.Button(
                self,
                text=shortpath(path),
                command=lambda: self.command(i, path)
            )
        ),
        **kw
    )

class TagWidget(Frame):
  ''' A Dsiplay for a `Tag`.
  '''

  @typechecked
  def __init__(self, parent, tags: TagSet, tag_name: str, *, alt_values=None):
    if alt_values is None:
      alt_values = set()
    else:
      alt_values = set(alt_values)
    super().__init__(parent)
    self.tags = tags
    self.tag_name = tag_name
    self.alt_values = alt_values
    self.label = tk.Label(self, text=tag_name)
    self.label.grid(column=0, row=0, sticky=tk.E)
    self.choices = ttk.Combobox(self, values=sorted(self.alt_values))
    if tag_name in tags:
      self.choices.set(tags[tag_name])
    self.choices.grid(column=1, row=0, sticky=tk.W)

class _TagsView(_Widget):

  def __init__(self, parent, *, get_tag_widget=None, **kw):
    super().__init__(parent, **kw)
    self.tags = TagSet()
    if get_tag_widget is None:
      get_tag_widget = TagWidget
    self.get_tag_widget = get_tag_widget

  def set_tags(self, tags):
    ''' Set the tags.
    '''
    self.tags.clear()
    self.tags.update(tags)

class TagsView(_TagsView, PanedWindow):

  def __init__(self, parent, **kw):
    super().__init__(parent, orient=tk.VERTICAL, showhandle=True, **kw)
    self.set_tags(())

  def tag_widget(self, tag):
    return TagWidget(
        None,
        self.tags,
        tag.name,
        alt_values=None if tag.value is None else (tag.value,)
    )

  def set_tags(self, tags):
    super().set_tags(tags)
    for child in list(self.panes()):
      self.remove(child)
    self.add(tk.Label(text="pre tag ======================="))
    self.add(self.tag_widget(Tag('dummy', 1)))
    for tag in sorted(self.tags):
      self.add(self.tag_widget(tag))
    self.add(tk.Label(text="post tag"))
    self.add(tk.Label(text="pad"))

class PathView(_Widget, tk.LabelFrame):
  ''' A preview of a filesystem path.
  '''

  def __init__(self, parent, fspath=None, *, tagger, **kw):
    super().__init__(parent, text=fspath or "NONE", **kw)
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
        ##background_color='grey',
    )
    ##self.preview.grid(column=0, row=0)
    self.preview.grid(column=0, row=0)

    self.tagsview = TagsView(
        self,
        fixed_size=(200, None),  ## 1080),
        ##fixed_size=(200, 600),  ## 1080),
        ##get_tag_widget=lambda tag: self._tag_widget(tag.name),
    )
    ##self.tagsview.grid(column=0, row=0)
    self.tagsview.grid(column=1, row=0, sticky=tk.N)

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
    self.config(text=new_fspath or "NONE")
    self.preview.fspath = new_fspath
    tags = self.tagged.merged_tags()
    ##self.tagsview.set_tags(tags)
    ##self.tagsview.set_size(size=(1920, 120))
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
    if self._fspath is None:
      return None
    return self.tagger.fstags[self._fspath]

  def _tag_widget(self, tag_name):
    ''' Return the `TagWidget` representing the `Tag` named `tag_name`.
    '''
    try:
      widget = self._tag_widgets[tag_name]
    except KeyError:
      tagged = self.tagger.fstags[self._fspath]
      direct_tags = tagged
      all_tags = tagged.merged_tags()
      try:
        value = direct_tags[tag_name]
      except KeyError:
        try:
          value = all_tags[tag_name]
        except KeyError:
          value = None
          missing = True
        else:
          missing = False
          is_direct = False
      else:
        missing = False
        is_direct = True
      suggested_values = self.tagger.suggested_tags(tagged.filepath
                                                    ).get(tag_name, set())
      widget = self._tag_widgets[tag_name] = TagWidget(
          tagged, tag_name, suggested_values
      )
    return widget

class ThumbNailScrubber(_PathList):

  def __init__(self, parent, pathlist: List[str], *, command, **kw):
    super().__init__(
        parent,
        pathlist=pathlist,
        orient=tk.HORIZONTAL,
        command=command,
        make_subwidget=(
            lambda i, path: trace(ImageButton)(
                self,
                path=path,
                command=lambda: self.command(i, path),
                fixed_size=(64, 64)
            )
        ),
        **kw
    )
