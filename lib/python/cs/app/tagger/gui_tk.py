#!/usr/bin/env python3

''' Tkinter based GUI for a `Tagger`.
'''

import platform
import sys
import tkinter as tk

from icontract import require, ensure
from typeguard import typechecked

from cs.fs import shortpath, HasFSPath
from cs.gui_tk import (
    _Widget,
    Button,
    Canvas,
    EditValueWidget,
    Frame,
    ImageWidget,
    LabelFrame,
    PathList_Listbox,
    Scrollbar,
    ThumbNailScrubber,
)
from cs.pfx import Pfx, pfx
from cs.tagset import Tag, TagSet

from . import Tagger

is_darwin = platform.system() == "Darwin"

def main(argv=None):
  ''' Tagger GUI command line mode.
  '''
  if argv is None:
    argv = sys.argv
  cmd = argv.pop(0)
  tagger = Tagger('.')
  with Pfx(cmd):
    root = tk.Tk()
    gui = TaggerWidget(root, tagger=tagger, fspaths=argv)
    gui.mainloop()

# pylint: disable=too-many-instance-attributes
class TaggerWidget(_Widget, tk.Frame, HasFSPath):
  ''' A `Frame` widget for a `Tagger`.
  '''

  WIDGET_FOREGROUND = None

  def __init__(self, parent, *, tagger, fspaths=None):
    super().__init__(parent)
    self.grid()
    if fspaths is None:
      fspaths = []
    self.tagger = tagger
    self._fspath = None
    self._fspaths = fspaths

    # callback to define the widget's contents
    def select_path(_, path):
      self.fspath = path

    pathlist = self.pathlist = PathList_Listbox(
        self,
        self.fspaths,
        command=select_path,
    )
    pathview = self.pathview = PathView(self, tagger=self.tagger)

    thumbscanvas = self.thumbscanvas = Canvas(self)

    thumbsscroll = Scrollbar(
        self,
        orient=tk.HORIZONTAL,
        command=thumbscanvas.xview,
    )
    thumbscanvas['xscrollcommand'] = thumbsscroll.set

    thumbsview = self.thumbsview = ThumbNailScrubber(
        thumbscanvas,
        self.fspaths,
        command=select_path,
    )
    thumbscanvas.create_window(
        thumbsscroll.winfo_width() / 2, 0, anchor=tk.N, window=thumbsview
    )

    pathlist.grid(column=0, row=0, sticky=tk.N + tk.S, rowspan=2)
    pathview.grid(column=1, row=0, sticky=tk.N + tk.S)
    thumbscanvas.grid(column=0, columnspan=2, sticky=tk.W + tk.E)
    thumbsscroll.grid(column=0, columnspan=2, sticky=tk.W + tk.E)

    self.columnconfigure(0, weight=1)
    self.columnconfigure(1, weight=8)
    self.columnconfigure(2, weight=1)
    self.rowconfigure(0, weight=1)

    # let the geometry settle
    self.grid()
    self.update_idletasks()

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
    self.config(text=shortpath(new_fspath) or "NONE")

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

if __name__ == '__main__':
  sys.exit(main(sys.argv))
