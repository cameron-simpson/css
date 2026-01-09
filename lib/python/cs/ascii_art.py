#!/usr/bin/env python3

''' Utilities to assist with ASCII art such as railroad diagrams;
    since these use Unicode box drawing characters and are better
    for diagrams such as railroad diagrams, this is neither ASCII
    nor art.

    This is still pretty alpha.

    This is the current test function:

        def test_railroad():
            box5 = TextBox("one\ntwo\nthree\nfour\nfive")
            print(box5)
            seq = Sequence(
                (
                    START, Repeat("repeat me"), "2 lines\naaaa",
                    Choice(("one", "two", "three", box5)), END
                )
            )
            print(seq)

    which prints:

                                      ╭┤one├──╮
                                      ├┤two├──┤
                                      ├┤three├┤
                           ╭───────╮  │╭─────╮│
                           │2 lines│  ││one  ││
        ├┼──┬┤repeat me├┬──┤aaaa   ├──┤│two  │├──┼┤
            ╰─────←─────╯  ╰───────╯  ╰┤three├╯
                                       │four │
                                       │five │
                                       ╰─────╯

'''

from abc import ABC, abstractmethod
from dataclasses import dataclass
from functools import cached_property
from typing import Optional, Union
import unicodedata

def box_char_name(
    heavy=False, arc=False, up=False, down=False, left=False, right=False
):
  ''' Compute the Unicode entity name for a box drawing glyph with
      the specified line weight and lines.

      Parameters:
      * `arc`: return an arc character instead of a rectangluar box corner
      * `heavy`: the line wieght: `HEAVY` for `True`, `LIGHT` for `False`
      * `up`: with a line upward from the centre
      * `down`: with a line downward from the centre
      * `left`: with a line leftward from the centre
      * `right`: with a line rightward from the centre
  '''
  if not (up or down or left or right):
    return 'SPACE'
  count = len(list(filter(None, (up, down, left, right))))
  if count < 2:
    raise ValueError(
        f'at least 2 of {up=}, {down=}, {left=}, {right=} must be true'
    )
  if arc:
    # just turn arc off if we can't do it
    if count > 2 or (up and down) or (left and right):
      arc = False
    # if count > 2:
    #   raise ValueError(
    #       f'{arc=} may not be true if there are more than 2 directions'
    #   )
    # if up and down or left and right:
    #   raise ValueError(
    #       f'{arc=} may not be true if there is a horizontal or vertical'
    #   )
  return " ".join(
      filter(
          None, (
              'BOX DRAWINGS',
              'HEAVY' if heavy else 'LIGHT',
              'ARC' if arc else '',
              (
                  'VERTICAL'
                  if up and down else 'UP' if up else 'DOWN' if down else ''
              ),
              'AND' if (up or down) and (left or right) else '',
              (
                  'HORIZONTAL' if left and right else
                  'LEFT' if left else 'RIGHT' if right else ''
              ),
          )
      )
  )

def box_char(
    heavy=False, arc=False, up=False, down=False, left=False, right=False
):
  ''' Return the Unicode entity for a box drawing glyph with
      the specified line weight and lines.

      Parameters:
      * `arc`: return an arc character instead of a rectangluar box corner
      * `heavy`: the line wieght: `HEAVY` for `True`, `LIGHT` for `False`
      * `up`: with a line upward from the centre
      * `down`: with a line downward from the centre
      * `left`: with a line leftward from the centre
      * `right`: with a line rightward from the centre
  '''
  if not (up or down or left or right):
    return " "
  return unicodedata.lookup(
      box_char_name(
          heavy=heavy, arc=arc, up=up, down=down, left=left, right=right
      )
  )

# precompute various drawing characters
VERT = box_char(up=True, down=True)
VERT_ = box_char(up=True, down=True, heavy=True)
VERT_LEFT = box_char(up=True, down=True, left=True)
VERT_LEFT_ = box_char(up=True, down=True, left=True, heavy=True)
VERT_RIGHT = box_char(up=True, down=True, right=True)
VERT_RIGHT_ = box_char(up=True, down=True, right=True, heavy=True)
HORIZ = box_char(left=True, right=True)
HORIZ_ = box_char(left=True, right=True, heavy=True)
HORIZ_UP = box_char(left=True, right=True, up=True)
HORIZ_UP_ = box_char(left=True, right=True, up=True, heavy=True)
HORIZ_DOWN = box_char(left=True, right=True, down=True)
HORIZ_DOWN_ = box_char(left=True, right=True, down=True, heavy=True)
CROSS = box_char(up=True, down=True, left=True, right=True)
CROSS_ = box_char(up=True, down=True, left=True, right=True, heavy=True)
LEFT_ARROW = '\N{LEFTWARDS ARROW}'
RIGHT_ARROW = '\N{RIGHTWARDS ARROW}'

@dataclass(slots=True)
class Cell:
  ''' A representation of a character cell for a hypothetical future
      "drawing on a canvas" mode.
  '''
  heavy: bool = False
  arc: bool = False
  up: bool = False
  down: bool = False
  left: bool = False
  right: bool = False
  _glyph: Optional[str] = None

  def __str__(self):
    glyph = self._glpyh
    if glyph is None:
      glyph = self._glyph = box_char(
          heavy=self.heavy,
          arc=self.arc,
          up=self.up,
          down=self.down,
          left=self.left,
          right=self.right,
      )
    return glyph

class Boxy(ABC):
  ''' The abstract base class for various boxes.
  '''

  def __str__(self):
    ''' Return the default rendering of the text box.
    '''
    return self.render()

  @staticmethod
  def horiz(
      width: int,
      middle='',
      *,
      arc=True,
      heavy=False,
      left_up=False,
      left_down=False,
      right_up=False,
      right_down=False,
  ):
    ''' Compute a horizontal line with an optional symbol in the
        middle and up or down connections.
    '''
    length = max(0, width - len(middle))
    left_length = length // 2
    right_length = length - left_length
    if left_up or left_down:
      left_end = box_char(arc=arc, right=True, up=left_up, down=left_down)
      left_length -= 1
      if left_length < 0:
        raise ValueError(
            f'insuffient room for {middle=} and the leftmost symbol'
        )
    else:
      left_end = ''
    if right_up or right_down:
      right_end = box_char(arc=arc, left=True, up=right_up, down=right_down)
      right_length -= 1
      if right_length < 0:
        raise ValueError(
            f'insuffient room for {middle=} and the rightmost symbol'
        )
    else:
      right_end = ''
    return left_end + HORIZ * left_length + middle + HORIZ * right_length + right_end

  def render(self, **render_lines_kw):
    ''' Render the text box as a single multiline string.
    '''
    return "\n".join(self.render_lines(**render_lines_kw))

  @abstractmethod
  def render_lines(self, **render_kw):
    ''' Render the box as a list of single line strings.
    '''
    raise NotImplementedError

  # the default attachment points are at the midpoints of the box

  @property
  def n(self):
    return self.width // 2

  @property
  def s(self):
    return self.width // 2

  @property
  def w(self):
    return self.height // 2

  @property
  def e(self):
    return self.height // 2

  # default points with just the midpoint
  @property
  def ns(self):
    return self.n,

  @property
  def ss(self):
    return self.s,

  @property
  def ws(self):
    return self.w,

  @property
  def es(self):
    return self.e,

  def from_str(cls, s: str) -> Union["Terminal", "TextBox"]:
    ''' Promote a string to a `Terminal` or `TextBox`.
        Nonempty strings with no newlines become `Terminal`s,
        otherwise a `TextBox`.
    '''
    if s and '\n' not in s:
      return Terminal(s)
    return TextBox(s)

@dataclass(frozen=True)
class Symbol(Boxy):
  ''' A bare text based symbol like `START` or `END`.
  '''
  text: str
  height: int = 1

  def __hash__(self):
    return id(self)

  def __eq__(self, other):
    return self is other

  def render_lines(self, **_):
    return [self.text]

  @property
  def width(self):
    return len(self.text)

@dataclass(frozen=True)
class Terminal(Symbol):
  ''' Like a symbol, but with a marker either side.
  '''

  def __hash__(self):
    return id(self)

  def __eq__(self, other):
    return self is other

  def render_lines(self, heavy=False, attach_w=False, attach_e=False, **_):
    return [
        "".join(
            (
                box_char(heavy=heavy, left=attach_w, up=True,
                         down=True), self.text,
                box_char(heavy=heavy, right=attach_e, up=True, down=True)
            )
        )
    ]

  @property
  def width(self):
    return len(self.text) + 2

START = Symbol(VERT_RIGHT + CROSS)
END = Symbol(CROSS + VERT_LEFT)

@dataclass
class TextBox(Boxy):
  ''' A text box with borders.
  '''

  text: str
  arc: bool = False
  heavy: bool = False

  def __hash__(self):
    return id(self)

  def __eq__(self, other):
    return self is other

  def render_lines(
      self,
      *,
      arc=None,
      heavy=None,
      up=False,
      down=False,
      left=False,
      right=False,
      attach_w=False,
      attach_e=False,
  ):
    ''' Render the text box as a list of single line strings.
    '''
    if arc is None:
      arc = self.arc
    if heavy is None:
      heavy = self.heavy
    nlines = self.height
    line_width = max(self.max_text_length, 1)
    return [
        self.horiz(self.width, left_down=True, right_down=True),
        *(
            "".join(
                (
                    box_char(
                        up=True, down=True, left=attach_w and i == self.w
                    ),
                    f'{text_line:<{line_width}}',
                    box_char(
                        up=True, down=True, right=attach_e and i == self.e
                    ),
                )
            ) for i, text_line in enumerate(self.lines, 1)
        ),
        self.horiz(self.width, left_up=True, right_up=True),
    ]

  @cached_property
  def lines(self):
    ''' The lines of text.
    '''
    return self.text.splitlines() or [""]

  @cached_property
  def nlines(self):
    ''' The number of lines of text.
    '''
    return len(self.lines)

  @cached_property
  def max_text_length(self):
    ''' The length of the longest line of text.
    '''
    return max(map(len, self.lines))

  @property
  def width(self):
    ''' The width including the borders
    '''
    return self.max_text_length + 2

  @property
  def height(self):
    ''' The height including the borders
    '''
    return len(self.lines) + 2

  @property
  def e(self):
    ''' The vertical offset to the east connection point.
    '''
    return self.height // 2

  @property
  def w(self):
    ''' The vertical offset to the west connection point.
    '''
    return self.height // 2

  @property
  def n(self):
    ''' The horizontal offset to the north connection point.
    '''
    return self.width // 2

  @property
  def s(self):
    ''' The horizontal offset to the south connection point.
    '''
    return self.width // 2

@dataclass
class _RailRoadAround(Boxy):
  content: Boxy
  above: bool = False
  middle: str = ''

  def __post_init__(self):
    if isinstance(self.content, str):
      self.content = self.from_str(self.content)

  def render_lines(self, heavy=False, attach_e=False, attach_w=False):
    ie = self.content.e
    iw = self.content.w
    inner_width = self.content.width
    inner_height = self.content.height
    above = self.above
    lines = []
    if above:
      lines.append(
          self.horiz(self.width, self.middle, left_down=True, right_down=True)
      )
    inner_lines = self.content.render_lines(
        heavy=heavy, attach_e=True, attach_w=True
    )
    for i, line in enumerate(inner_lines):
      lines.append(
          "".join(
              (
                  box_char(
                      up=i <= iw if above else i > iw,
                      down=i < iw if above else i >= iw,
                      left=attach_w and i == iw,
                      right=i == iw,
                  ),
                  line,
                  box_char(
                      up=i <= ie if above else i > ie,
                      down=i < ie if above else i >= ie,
                      left=i == ie,
                      right=attach_e and i == ie,
                  ),
              )
          )
      )
    if not above:
      lines.append(
          self.horiz(self.width, self.middle, left_up=True, right_up=True)
      )
    return lines

  @property
  def width(self):
    return self.content.width + 2

  @property
  def height(self):
    return self.content.height + 1

  @property
  def e(self):
    return self.content.e + int(self.above)

  @property
  def w(self):
    return self.content.w + int(self.above)

@dataclass
class Repeat(_RailRoadAround):
  middle: str = LEFT_ARROW

  def __hash__(self):
    return id(self)

  def __eq__(self, other):
    return self is other

@dataclass
class Optional(_RailRoadAround):
  middle: str = RIGHT_ARROW

  def __hash__(self):
    return id(self)

  def __eq__(self, other):
    return self is other

@dataclass
class _RailRoadMulti(Boxy):
  content: list[Boxy]

  def __post_init__(self):
    if len(self.content) == 0:
      raise ValueError('at least one content box is required')
    # promote str to TextBox
    self.content = [
        self.from_str(box) if isinstance(box, str) else box
        for box in self.content
    ]

  def append(self, box):
    self.content.append(self.from_str(box) if isinstance(box, str) else box)

@dataclass
class Stack(_RailRoadMulti):
  ''' An unadorned vertical stack of the content.
  '''

  def __hash__(self):
    return id(self)

  def __eq__(self, other):
    return self is other

  @cached_property
  def height(self):
    ''' The overall height of the content.
    '''
    return sum(box.height for box in self.content)

  @cached_property
  def width(self):
    ''' The overall width of the content.
    '''
    return max(box.width for box in self.content)

  @cached_property
  def ws(self):
    ''' A cached `tuple` of the line offsets of each stacked box's `.w` position.
    '''
    height = 0
    ws = []
    for box in self.content:
      ws.append(height + box.w)
      height += box.height
    return tuple(ws)

  @cached_property
  def es(self):
    ''' A cached `tuple` of the line offsets of each stacked box's `.e` position.
    '''
    height = 0
    es = []
    for box in self.content:
      es.append(height + box.e)
      height += box.height
    return tuple(es)

  def render_lines(
      self,
      align='left',  # vs centre/center and middle
      heavy=False,
      attach_e=False,
      attach_w=False,
      sep_len=2,
      open_ended=False,
  ):
    nboxes = len(self.content)
    lines = []
    for bi, box in enumerate(self.content):
      box_pad_length = self.width - box.width
      box_pad_left = (
          0 if align == 'left' else
          box_pad_length if align == 'right' else box_pad_length // 2
      )
      box_pad_right = box_pad_length - box_pad_left
      spaces_left = " " * box_pad_left
      line_left = self.horiz(box_pad_left)
      spaces_right = " " * box_pad_right
      line_right = self.horiz(box_pad_right)
      for li, box_line in enumerate(box.render_lines(
          heavy=heavy,
          attach_e=attach_e,
          attach_w=attach_w,
      )):
        lines.append(
            "".join(
                (
                    (line_left if attach_w and li == box.w else spaces_left),
                    box_line,
                    (line_right if attach_e and li == box.e else spaces_right),
                )
            )
        )
    assert len(lines) == self.height
    return lines

@dataclass
class Choice(Stack):

  def __hash__(self):
    return id(self)

  def __eq__(self, other):
    return self is other

  @property
  def w(self):
    return (self.ws[0] + self.ws[-1]) // 2

  @property
  def e(self):
    return (self.es[0] + self.es[-1]) // 2

  def render_lines(
      self,
      heavy=False,
      attach_e=False,
      attach_w=False,
      **render_kw,
  ):
    nboxes = len(self.content)
    lines = []
    top_w = self.ws[0]
    bottom_w = self.ws[-1]
    top_e = self.es[0]
    bottom_e = self.es[-1]
    for li, inner_line in enumerate(super().render_lines(heavy=heavy,
                                                         attach_e=attach_e,
                                                         attach_w=attach_w,
                                                         **render_kw)):
      lines.append(
          "".join(
              (
                  box_char(
                      arc=True,
                      heavy=heavy,
                      left=attach_w and li == self.w,
                      right=li in self.ws,
                      up=li > top_w and li <= bottom_w,
                      down=li < bottom_w and li >= top_w,
                  ),
                  inner_line,
                  box_char(
                      arc=True,
                      heavy=heavy,
                      left=li in self.es,
                      right=attach_e and li == self.e,
                      up=li > top_e and li <= bottom_e,
                      down=li < bottom_e and li >= top_e,
                  ),
              )
          )
      )
    assert len(lines) == self.height
    return lines

  @cached_property
  def inner_width(self):
    return max(box.width for box in self.content)

  @property
  def width(self):
    return super().width + 2
  def render_lines(
      self,
      heavy=False,
      attach_e=False,
      attach_w=False,
      sep_len=2,
      open_ended=False,
  ):
    nboxes = len(self.content)
    lines = []
    for bi, box in enumerate(self.content):
      box_width = box.width
      box_pad_length = self.inner_width - box_width
      box_pad_right_line = self.horiz(box_pad_length)
      box_pad_right_spaces = " " * box_pad_length
      for li, box_line in enumerate(box.render_lines(heavy=heavy,
                                                     attach_w=True,
                                                     attach_e=not open_ended)):
        bli = len(lines)  # the overall Choice line index
        left_up = (
            li > box.w if bi == 0 else li <= box.w if bi == nboxes -
            1 else True
        )
        left_down = (
            li >= box.w if bi == 0 else li < box.w if bi == nboxes -
            1 else True
        )
        right_up = (
            li > box.e if bi == 0 else li <= box.e if bi == nboxes -
            1 else True
        )
        right_down = (
            li >= box.e if bi == 0 else li < box.e if bi == nboxes -
            1 else True
        )
        lines.append(
            "".join(
                (
                    box_char(
                        arc=True,
                        heavy=heavy,
                        left=attach_w and bli == self.w,
                        right=li == box.w,
                        up=left_up,
                        down=left_down,
                    ),
                    box_line,
                    box_pad_right_line
                    if li == box.e else box_pad_right_spaces,
                    (
                        '' if open_ended else box_char(
                            arc=True,
                            heavy=heavy,
                            left=li == box.e,
                            right=attach_e and bli == self.e,
                            up=right_up,
                            down=right_down,
                        )
                    ),
                )
            )
        )
    assert len(lines) == self.height
    return lines

  @cached_property
  def inner_width(self):
    return max(box.width for box in self.content)

  @property
  def width(self):
    # TODO: should be only +1 for open_ended renders
    return self.inner_width + 2

  @cached_property
  def height(self):
    return sum(box.height for box in self.content)

  @property
  def w(self):
    return self.height // 2

@dataclass
class Sequence(_RailRoadMulti):

  def __hash__(self):
    return id(self)

  def __eq__(self, other):
    return self is other

  def render_lines(self, heavy=False, attach_e=True, attach_w=True, sep_len=2):
    # compute the required lines above and below the attach line
    boxes = self.content
    # compute padding - this assumes box.e == box.w
    lines_above = 0
    lines_below = 0
    for box in boxes:
      assert box.e == box.w
      lines_above = max(lines_above, box.w)
      lines_below = max(lines_below, box.height - box.w - 1)
    total_lines = lines_above + lines_below + 1
    lines = [[] for _ in range(total_lines)]
    for bi, box in enumerate(boxes):
      pad = " " * box.width
      row = 0
      pad_above = lines_above - box.w
      if pad_above > 0:
        for _ in range(pad_above):
          lines[row].append(pad)
          row += 1
      for box_line in box.render_lines(
          heavy=heavy,
          attach_w=bi > 0,
          attach_e=bi < len(boxes) - 1,
      ):
        lines[row].append(box_line)
        row += 1
      pad_below = lines_below - (box.height - box.e - 1)
      if pad_below > 0:
        for _ in range(pad_below):
          lines[row].append(pad)
          row += 1
      assert row == len(lines), f'{row=} != {len(lines)=}'
    return [
        (self.horiz(sep_len) if row == lines_above else " " *
         sep_len).join(line_v) for row, line_v in enumerate(lines)
    ]

def test_railroad():
  box5 = TextBox("one\ntwo\nthree\nfour\nfive")
  print(box5)
  seq = Sequence(
      (
          START, Repeat("repeat me"), "2 lines\naaaa",
          Choice(("one", "two", "three", box5)), END
      )
  )
  print(seq)
  stack = Stack(("st1", "2", "3\n4", "four five"))
  print(stack.render(attach_e=True, align='right'))

if __name__ == '__main__':
  test_railroad()
