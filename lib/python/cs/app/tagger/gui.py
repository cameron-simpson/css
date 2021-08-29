#!/usr/bin/env python3

from contextlib import contextmanager
import PySimpleGUIQt as sg
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
    layout = [
        [ImageWidget(self.pathnames[0])], [sg.Text("What's your name?")],
        [sg.Input(key='-INPUT-')], [sg.Text(size=(40, 1), key='-OUTPUT-')],
        [sg.Button('Ok'), sg.Button('Quit')]
    ]
    self.window = sg.Window(str(self), layout)
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
        print("event =", event)
        # See if user wants to quit or window was closed
        if event == sg.WINDOW_CLOSED or event == 'Quit':
          break
        # Output a message to the window
        window['-OUTPUT-'].update(
            'Hello ' + values['-INPUT-'] + "! Thanks for trying PySimpleGUI"

class ImageWidget(sg.Image):
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

class PathListWidget(sg.Tree):

  DEFAULT_NUM_ROWS = 16

  def __init__(self, pathnames, num_rows=None):
    if num_rows is None:
      num_rows = self.DEFAULT_NUM_ROWS
    key = uuid4()
    treedata, pathinfo = self.make_treedata(pathnames)
    super().__init__(
        key=key,
        data=treedata,
        headings=['id', 'name'],
        enable_events=True,
        num_rows=num_rows,
    )
    self.key = key
    self.treedata = treedata
    self.pathinfo = pathinfo

  def __getitem__(self, uuid):
    return self.pathinfo.by_uuid[uuid]

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
