#!/usr/bin/env python3

from contextlib import contextmanager
import PySimpleGUIQt as sg
from cs.resources import MultiOpenMixin, RunState
from .util import ispng, pngfor

class ImageWidget(sg.Image):

  def __init__(self, pathname, **kw):
    if ispng(pathname):
      display_pathname = pathname
    else:
      display_pathname = pngfor(pathname)
    super().__init__(filename=display_pathname, **kw)
    self.pathname = pathname
    self.display_pathname = display_pathname

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
        )
