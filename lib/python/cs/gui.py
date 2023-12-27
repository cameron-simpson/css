#!/usr/bin/env python3
#

''' Some easy to assemble simple GUI widgets, currently based on PyQt5.
'''

from collections import defaultdict, namedtuple
from PIL import Image as PILImage
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QPixmap
from PyQt5.QtWidgets import (
    QApplication, QMainWindow,
    QWidget, QTabWidget, QLabel, QGroupBox,
    QHBoxLayout, QVBoxLayout, QGridLayout, QScrollArea,
)
from cs.imageutils import ThumbnailCache
from cs.pfx import Pfx
from cs.x import X

class App(object):
  ''' The GUI application main instance (not the main GUI window).
  '''

  def __init__(self, argv):
    self.app = QApplication(argv)

  def start(self):
    ''' Start the GUI application main loop.
    '''
    return self.app.exec_()

Position = namedtuple('Position', 'left top')
Size = namedtuple('Size', 'width height')

_RGBColor = namedtuple('RGBColor', 'red green blue')
class RGBColor(_RGBColor):
  ''' A colour in RGB space.
  '''
  @property
  def htmlcolor(self):
    ''' Transcription of the colour for HTML.
    '''
    return '#%02x%02x%02x' % (self.red, self.green, self.blue)

RED = RGBColor(255, 0, 0)
GREEN = RGBColor(0, 255, 0)
BLUE = RGBColor(0, 0, 255)
WHITE = RGBColor(255, 255, 255)
BLACK = RGBColor(0, 0, 0)

DEFAULT_FGCOLOR = GREEN
DEFAULT_BGCOLOR = RGBColor(128, 128, 128)   # was BLACK

class _Element(object):
  ''' The base class for all widgets.
  '''

  def __init__(
      self, widget,
      title=None,
      bgcolor=None, fgcolor=None,
      size=None,
  ):
    if widget is None:
      widget = QWidget()
    if title is None:
      title = type(self).__name__
    if bgcolor is None:
      bgcolor = DEFAULT_BGCOLOR
    if fgcolor is None:
      fgcolor = DEFAULT_FGCOLOR
    self.widget = widget
    self.title = title
    self.bgcolor = bgcolor
    self.fgcolor = fgcolor
    if size is not None:
      self.size = size

  def show(self):
    ''' Show the widget.
    '''
    self.widget.show()

  @property
  def title(self):
    ''' The widget title.
    '''
    return self._title
  @title.setter
  def title(self, new_title):
    ''' Set the widget title.
    '''
    assert isinstance(new_title, str)
    self._title = new_title
    self.widget.setWindowTitle(new_title)

  @property
  def position(self):
    ''' The widget position.
    '''
    geom = self.widget.geometry()
    return Position(geom.x(), geom.y())
  @position.setter
  def position(self, new_position):
    ''' Set the widget position.
    '''
    new_position = Position(*new_position)
    size = self.size
    self.widget.setGeometry(
        new_position.left,
        new_position.top,
        size.width,
        size.height)

  @property
  def size(self):
    ''' The widget size.
    '''
    w = self.widget
    return Size(w.width(), w.height())
  @size.setter
  def size(self, new_size):
    ''' Set the widget size.
    '''
    new_size = Size(*new_size)
    self._size = new_size
    position = self.position
    self.widget.setGeometry(
        position.left,
        position.top,
        new_size.width,
        new_size.height)

  @property
  def bgcolor(self):
    ''' The background colour.
    '''
    return self._bgcolor
  @bgcolor.setter
  def bgcolor(self, new_color):
    ''' Set the background colour.
    '''
    new_color = RGBColor(*new_color)
    self._bgcolor = new_color
    P = self.widget.palette()
    P.setColor(self.widget.backgroundRole(), QColor(*new_color))
    self.widget.setAutoFillBackground(True)
    self.widget.setPalette(P)

  @property
  def fgcolor(self):
    ''' The foreground colour.
    '''
    return self._fgcolor
  @fgcolor.setter
  def fgcolor(self, new_color):
    ''' Set the forground colour.
    '''
    new_color = RGBColor(*new_color)
    self._fgcolor = new_color

class Widget(_Element):
  ''' A basic widget, and _Element with a default kit widget.
  '''

  def __init__(self, widget=None, **kw):
    if widget is None:
      widget = QWidget()
    super().__init__(widget, **kw)

class _Layout(Widget):

  def __init__(self, layout, **kw):
    super().__init__(**kw)
    self.layout = layout
    self.cells = []

  def add(self, new_widget):
    ''' Append a widget to the layout.
    '''
    assert isinstance(new_widget, _Element)
    self.cells.append(new_widget)
    self.layout.addWidget(new_widget.widget)
    return new_widget

class HBox(_Layout):
  ''' A scrollable horizontal stack of widgets.
  '''

  def __init__(self, widget=None, **kw):
    if widget is None:
      widget = QWidget()
    scrollarea = QScrollArea()
    scrollarea.setWidget(widget)
    scrollarea.setWidgetResizable(True)
    scrollarea.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
    scrollarea.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
    layout = QHBoxLayout()
    widget.setLayout(layout)
    super().__init__(layout, widget=scrollarea, **kw)
    self.bgcolor = RGBColor(40, 40, 40)

class VBox(_Layout):
  ''' A vertical stack of widgets.
  '''

  def __init__(self, widget=None, **kw):
    if widget is None:
      widget = QWidget()
    scrollarea = QScrollArea()
    scrollarea.setWidget(widget)
    scrollarea.setWidgetResizable(True)
    scrollarea.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
    scrollarea.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
    layout = QVBoxLayout()
    widget.setLayout(layout)
    super().__init__(layout, widget=scrollarea, **kw)

class Grid(_Layout):
  ''' A grid arrangement of smaller widgets.
  '''

  def __init__(self, **kw):
    widget = QWidget()
    scrollarea = QScrollArea()
    scrollarea.setWidget(widget)
    scrollarea.setWidgetResizable(True)
    scrollarea.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
    scrollarea.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
    layout = QGridLayout()
    widget.setLayout(layout)
    super().__init__(layout, widget=scrollarea, **kw)
    self.grid = defaultdict(dict)

  def __getitem__(self, x):
    return self.grid[x]

  def __setitem__(self, xy, element):
    x, y = xy
    assert isinstance(x, int)
    assert isinstance(y, int)
    assert isinstance(element, _Element)
    self.grid[x][y] = element
    self.layout.addWidget(element.widget, y, x)

class TabSet(_Element):
  ''' A tab set, showing one of a set of Elements.
  '''

  def __init__(self, **kw):
    super().__init__(QTabWidget(), **kw)
    self.tabs = []

  def add(self, new_tab):
    ''' Add an element to the tab set.
    '''
    assert isinstance(new_tab, _Element)
    self.tabs.append(new_tab)
    X("add qt %s to tab set %s", new_tab.widget, self.widget)
    self.widget.addTab(new_tab.widget, new_tab.title)
    return new_tab

class Label(_Element):
  ''' A text label.
  '''

  def __init__(self, text, **kw):
    super().__init__(QLabel(), **kw)
    self.text = text

  @property
  def text(self):
    ''' The label's text.
    '''
    return self._text
  @text.setter
  def text(self, new_text):
    ''' Set the text on the label.
    '''
    self._text = new_text
    html = (
        "<font color='%s'>%s</font>"
        % (
            self.fgcolor.htmlcolor,
            new_text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        )
    )
    self.widget.setText(html)

class Image(_Element):
  ''' A view of an image.
  '''

  def __init__(self, image_path, title=None, **kw):
    if title is None:
      title = image_path
    super().__init__(QLabel(), title=title, **kw)
    self.image_path = image_path
    self._pixmap = None

  @property
  def pixmap(self):
    ''' The image pixmap.
    '''
    pm = self._pixmap
    if not pm:
      X("load pixmap from %r", self.image_path)
      pm = self._pixmap = QPixmap(self.image_path)
    return pm

  def clear(self):
    ''' Clear the label.

        This is to save memory, permits when out of sight.
    '''
    self._pixmap = None
    self.widget.clear()

  def expose(self):
    ''' Ensure the label pixmap is set.
    '''
    self.widget.setPixmap(self.pixmap)

class Thumbnail(Image):
  ''' A thumbnail view of an image.
  '''

  cache = ThumbnailCache()

  def __init__(self, image_path, size=None, **kw):
    if size is None:
      size = Size(128, 128)
    super().__init__(image_path, size=size, **kw)

  @property
  def path(self):
    ''' The pathname of the thumbnail image file.
    '''
    dx, dy = self.size
    return self.cache.thumb_for_path(dx, dy, self.image_path)

  @property
  def pixmap(self):
    ''' The thumbnail pixmap.
    '''
    pm = self._pixmap
    if pm is None:
      osize = self.size
      pm = self._pixmap = QPixmap(self.path).scaled(
          osize.width, osize.height, Qt.KeepAspectRatio)
    return pm

class MainWindow(_Element):
  ''' An application main window.
  '''

  def __init__(self, **kw):
    super().__init__(QMainWindow(), **kw)
    self._statusline = None
    self._inner = None

  @property
  def inner(self):
    ''' The main inner widget.
    '''
    return self._inner

  @inner.setter
  def inner(self, inner_element):
    ''' Set the main inner widget.
    '''
    assert isinstance(inner_element, _Element)
    self._inner = inner_element
    self.widget.setCentralWidget(inner_element.widget)

  @property
  def statusline(self):
    ''' The current status line value.
    '''
    return self._statusline
  @statusline.setter
  def statusline(self, new_status):
    ''' Set the status line value.
    '''
    assert new_status is None or isinstance(new_status, str)
    self._statusline = new_status
    X("statusbar => %r", new_status)
    self.widget.statusBar().showMessage(new_status if new_status else '')
