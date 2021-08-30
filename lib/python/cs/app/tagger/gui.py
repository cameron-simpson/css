#!/usr/bin/env python3

from contextlib import contextmanager
import os
from os.path import (basename, isdir as isdirpath, join as joinpath, realpath)
from uuid import uuid4

import PySimpleGUI as sg
##import PySimpleGUIQt as sg

from cs.fileutils import shortpath
from cs.logutils import warning
from cs.mappings import IndexedMapping, UUIDedDict
from cs.pfx import pfx, Pfx
from cs.resources import MultiOpenMixin, RunState
from .util import ispng, pngfor


class TaggerGUI(MultiOpenMixin):

  def __init__(self, tagger, pathnames):
    self.tagger = tagger
    self.pathnames = pathnames

  def __str__(self):
    return "%s(%s)" % (type(self).__name__, self.tagger)

  @contextmanager
  def startup_shutdown(self):
    # Define the window's contents
    self.tree = PathListWidget(self.pathnames)
    self.preview = ImageWidget()
    layout = [
        [self.tree, self.preview],
        [sg.Text("What's your name?")],
        [sg.Input(key='-INPUT-')],
        [sg.Text(size=(40, 1), key='-OUTPUT-')],
        [sg.Button('Ok'), sg.Button('Quit')],
    ]
    self.window = sg.Window(str(self), layout, finalize=True)
    for record in self.tree:
      if isfilepath(record.fullpath):
        self.pathname = record.fullpath
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
            self.pathname = record.fullpath
        else:
          warning("unexpected event %r: %r", event, values)

  @property
  def pathname(self):
    return self.preview.pathname

  @pathname.setter
  @pfx
  def pathname(self, new_pathname):
    # TODO: make the tree display the associated element
    self.pathview.update(value="Path: " + shortpath(new_pathname))
    self.preview.pathname = new_pathname
    self.preview.set_size(size=(1920, 1280))
    tags = self.tagger.fstags[new_pathname].all_tags
    self.tagsview.set_tags(tags)
    ##self.tagsview.set_size(size=(1920, 120))
    try:
      pathinfo = self.tree[new_pathname]
    except KeyError:
      warning("path not in tree")

class _Widget:

  def __init__(self, *, key=None, fixed_size=None, **kw):
    if key is None:
      key = uuid4()
    self.key = key
    super().__init__(key=key, **kw)
    self.__fixed_size = fixed_size

  def update(self, **kw):
    X("%s.update: kw=%r", type(self).__name__, kw)
    super().update(**kw)
    if self.__fixed_size:
      X("  update: set_size(%r)", self.__fixed_size)
      self.set_size(self.__fixed_size)

class ImageWidget(_Widget, sg.Image):
  ''' An image widget which can show anything Pillow can read.
  '''

  @property
  def pathname(self):
    return self._pathname

  @pathname.setter
  def pathname(self, new_pathname):
    if new_pathname is not None:
      try:
        if ispng(new_pathname):
          display_pathname = new_pathname
        else:
          display_pathname = pngfor(new_pathname)
      except (OSError, ValueError):
        new_pathname = None
    if new_pathname is None:
      self.update()
    else:
      self.update(filename=display_pathname)
    self._pathname = new_pathname

class PathListWidget(_Widget, sg.Tree):

  DEFAULT_NUM_ROWS = 16

  def __init__(self, pathnames, num_rows=None, justification='left', **kw):
    if num_rows is None:
      num_rows = self.DEFAULT_NUM_ROWS
    treedata, pathinfo = self.make_treedata(pathnames)
    super().__init__(
        data=treedata,
        headings=['id', 'name'],
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
    q = [self.treedata.root_node]
    while q:
      node = q.pop(0)
      if node.key:
        try:
          record = self[node.key]
        except KeyError:
          warning("skip key %r", node.key)
        else:
          yield record
        q.extend(node.children)

  @pfx
  def make_treedata(self, pathnames):
    treedata = sg.TreeData()
    for pathname in pathnames:
      with Pfx(pathname):
        fullpath = realpath(pathname)
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

class TagsView(_Widget, sg.Text):

  def set_tags(self, tags):
    self.update(value='\n'.join(map(str, tags)))

class TagsView__Canvas(_Widget, sg.Canvas):

  def set_tags(self, tags):
    canvas = self.tk_canvas
    canvas.delete(canvas.find_all())
    for tag in tags:
      canvas.create_text((0, 0), text=str(tag))
