#!/usr/bin/env python3

from contextlib import contextmanager
import os
from os.path import (
    basename,
    isdir as isdirpath,
    isfile as isfilepath,
    join as joinpath,
    realpath,
)
from uuid import UUID, uuid4

import PySimpleGUI as sg
##import PySimpleGUIQt as sg
from typeguard import typechecked

from cs.fileutils import shortpath
from cs.logutils import warning
from cs.mappings import IndexedMapping, UUIDedDict
from cs.pfx import pfx, Pfx
from cs.queues import ListQueue
from cs.resources import MultiOpenMixin, RunState
from cs.tagset import Tag, TagSet

from .util import ispng, pngfor

class TaggerGUI(MultiOpenMixin):

  def __init__(self, tagger, fspaths):
    self.tagger = tagger
    self.fspaths = fspaths

  def __str__(self):
    return "%s(%s)" % (type(self).__name__, self.tagger)

  @contextmanager
  def startup_shutdown(self):
    # Define the window's contents
    self.tree = PathListWidget(
        self.fspaths,
        key="paths",
        fixed_size=(200, None),
        expand_x=True,
        expand_y=True,
        show_expanded=True,
        pad=(3, 3),
    )
    self.pathview = PathView(tagger=self.tagger)
    layout = [
        [
            self.tree,
            self.pathview,
        ],
        [sg.Text("BAH")],
    ]
    self.window = sg.Window(
        str(self),
        layout,
        size=(2200, 1500),
        finalize=True,
    )
    for record in self.tree:
      if isfilepath(record.fullpath):
        self.fspath = record.fullpath
        break
    print("window made")
    yield self
    print("closing")
    self.window.close()

  def run(self, runstate=None):
    print("run...")
    if runstate is None:
      runstate = RunState(str(self))
    # Display and interact with the Window using an Event Loop
    window = self.window
    print("window =", window)
    with runstate:
      while not runstate.cancelled:
        print("event?")
        event, values = window.read()
        print("event =", repr(event), repr(values))
        # See if user wants to quit or window was closed
        if event == sg.WINDOW_CLOSED or event == 'Quit':
          runstate.cancel()
        elif event == self.tree.key:
          record_key, = values[event]
          print("record_key =", record_key)
          try:
            record = self.tree[record_key]
          except KeyError as e:
            warning("no self.tree[%r]: %s", record_key, e)
          else:
            print("record =", record)
            self.fspath = record.fullpath
        else:
          warning("unexpected event %r: %r", event, values)

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

class _Widget:

  def __init__(self, *a, key=None, fixed_size=None, **kw):
    if key is None:
      key = uuid4()
    self.key = key
    super().__init__(*a, key=key, **kw)
    self.fixed_size = fixed_size

  def update(self, **kw):
    X("%s.update: kw=%r", type(self).__name__, kw)
    super().update(**kw)
    if self.fixed_size:
      self.set_size(self.fixed_size)

class ImageWidget(_Widget, sg.Image):
  ''' An image widget which can show anything Pillow can read.
  '''

  @property
  def fspath(self):
    ''' The filesystem path of the current display.
    '''
    return self._fspath

  @fspath.setter
  def fspath(self, new_fspath):
    if new_fspath is not None:
      size = self.fixed_size or self.get_size()
      try:
        display_fspath = pngfor(new_fspath, size)
      except (OSError, ValueError):
        new_fspath = None
    if new_fspath is None:
      self.update()
    else:
      self.update(filename=display_fspath)
    self._fspath = new_fspath

class PathListWidget(_Widget, sg.Tree):

  DEFAULT_NUM_ROWS = 16

  def __init__(self, fspaths, num_rows=None, justification='left', **kw):
    if num_rows is None:
      num_rows = self.DEFAULT_NUM_ROWS
    treedata, pathinfo = self.make_treedata(fspaths)
    super().__init__(
        data=treedata,
        headings=[],
        col_widths=[20, 20],
        enable_events=True,
        num_rows=num_rows,
        **kw,
    )
    self.treedata = treedata
    self.pathinfo = pathinfo

  def __getitem__(self, uuid):
    ''' Return the path information record from a node key (`uuid`).
    '''
    if isinstance(uuid, UUID):
      return self.pathinfo.by_uuid[uuid]
    try:
      uuid = UUID(uuid)
    except ValueError:
      path = uuid
      return self.pathinfo.by_fullpath[realpath(path)]
    else:
      return self.pathinfo.by_uuid[uuid]

  def get(self, uuid, default=None):
    ''' Return `self[uuid]` or `default` if not present.
    '''
    try:
      return self[uuid]
    except KeyError:
      return default

  def __iter__(self):
    ''' Iterate over the path information records in tree order.
    '''
    q = ListQueue([self.treedata.root_node])
    for node in q:
      if node.key:
        try:
          record = self[node.key]
        except KeyError:
          warning("skip key %r", node.key)
        else:
          yield record
      q.extend(node.children)

  @pfx
  def make_treedata(self, fspaths):
    treedata = sg.TreeData()
    for fspath in fspaths:
      with Pfx(fspath):
        fullpath = realpath(fspath)
        pathinfo = IndexedMapping(pk='fullpath')
        top_record = UUIDedDict(fullpath=fullpath)
        pathinfo.add(top_record)
        treedata.insert(
            "",
            top_record.uuid,
            shortpath(top_record.fullpath),
            [basename(top_record.fullpath)],
            icon=None,
        )
      if isdirpath(fullpath):
        for dirpath, dirnames, filenames in os.walk(fullpath):
          with Pfx("walk %r", dirpath):
            record = pathinfo.by_fullpath[dirpath]
            parent_node = treedata.tree_dict[record.uuid]
            for dirname in sorted(dirnames):
              with Pfx(dirname):
                if dirname.startswith('.'):
                  continue
                subdir_path = joinpath(dirpath, dirname)
                subdir_record = UUIDedDict(fullpath=subdir_path)
                pathinfo.add(subdir_record)
                treedata.insert(
                    record.uuid,
                    subdir_record.uuid,
                    dirname,
                    [dirname],
                    icon=None,
                )
            for filename in sorted(filenames):
              with Pfx(filenames):
                if filename.startswith('.'):
                  continue
                filepath = joinpath(dirpath, filename)
                file_record = UUIDedDict(fullpath=filepath)
                pathinfo.add(file_record)
                treedata.insert(
                    record.uuid,
                    file_record.uuid,
                    filename,
                    [filename],
                    icon=None,
                )
    return treedata, pathinfo

class TagWidget(_Widget, sg.Column):
  ''' A Dsiplay for a `Tag`.
  '''

  def __init__(self, tag):
    self.tag = tag
    self.alt_values = set()
    layout = [
        [
            sg.Text(tag.name),
            sg.Combo(
                list(self.alt_values),
                default_value=tag.transcribe_value(tag.value),
            )
        ]
    ]
    super().__init__(layout=layout)

class _TagsView(_Widget):

  @typechecked
  def __init__(self, tags: TagSet, **kw):
    self.tags = tags
    super().__init__(**kw)

  def set_tags(self, tags):
    ''' Set the tags.
    '''
    self.tags.clear()
    self.tags.update(tags)

class TagsView_Text(_TagsView, sg.Text):

  def set_tags(self, tags):
    super().set_tags(tags)
    self.update(value='\n'.join(map(str, sef.tags)))

class TagsView__Canvas(_TagsView, sg.Canvas):

  def set_tags(self, tags):
    super().set_tags(tags)
    canvas = self.tk_canvas
    canvas.delete(canvas.find_all())
    for tag in self.tags:
      canvas.create_text((0, 0), text=str(tag))

class TagsView_Column(_TagsView, sg.Column):

  def __init__(self, tags, **kw):
    super().__init__(tags, layout=self.layout_tags(tags))

  @staticmethod
  def layout_tags(tags):
    return [[TagWidget(tag) for tag in sorted(tags)]]

  def set_tags(self, tags):
    super().set_tags(tags)
    self.layout(self.layout_tags(self.tags))

TagsView = TagsView_Column  # TagsView_Text

class PathView(_Widget, sg.Frame):
  ''' A preview of a filesystem path.
  '''

  def __init__(self, fspath=None, *, tagger):
    self._fspath = fspath
    self.tagger = tagger
    self.preview = ImageWidget(
        key="preview",
        fixed_size=(1920, 1080),
        background_color='grey',
        expand_x=True,
    )
    self.tagsview = TagsView(
        TagSet(),
        key="tags",
        fixed_size=(1920, 200),
        background_color='blue',
        expand_x=True,
    )
    layout = [
        [
            sg.Column(
                [
                    [TagWidget(Tag('tag1', 3)),
                     TagWidget(Tag('tag2', 4))],
                    [self.preview],
                    [sg.HorizontalSeparator()],
                    [self.tagsview],
                ],
                size=(1920, 1600),
            )
        ]
    ]
    super().__init__(fspath or "NONE", layout)

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
    self.update(value=shortpath(new_fspath) if new_fspath else "NONE")
    self.preview.fspath = new_fspath
    tags = self.tagger.fstags[new_fspath].all_tags
    self.tagsview.set_tags(tags)
    ##self.tagsview.set_size(size=(1920, 120))
