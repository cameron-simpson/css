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
from dataclasses import dataclass, field
from functools import cached_property
import sys
from types import SimpleNamespace as NS
from typing import Optional, Union
import unicodedata

from cs.context import stackattrs
from cs.deco import decorator

def box_char_name(
    heavy=False, arc=False, up=False, down=False, left=False, right=False
):
  ''' Compute the Unicode entity name for a box drawing glyph with
      the specified line weight and lines.

      See: https://www.unicode.org/charts/nameslist/n_2500.html

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
  # TODO: Looks like there are single direction characters, supporting count=1
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
              'HEAVY' if heavy and not arc else 'LIGHT',
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

@decorator
def render(render_func, **render_defaults):
  ''' A decorator to wrap rendering methods with the prevailing
      render context, as updated by any keyword arguments supplied
      to the method.

      The decorator may be suplied with its own defaults; these
      will be applied to the render context first, then whatever
      render keyword arguments as supplied to the method.

      The decorated method accepts an optional `ctx` parameter
      to use as the render context; the default comes from
      `self.render_context` which is normally an attribute of
      `self.__class__`.

      The wrapped method is called with a keyword argument for every
      attribute of the updated render context.
      The render context is returned to its previous state on return
      from the method.

      This means that the render methods have an unusual signature,
      for example:

          @render(heavy=True)
          def renderlines(self, *, heavy, attach_w, attach_e, **render_kw):

      This:
      - enumerates only thekeyword argument of interest to the method
      - "default" values are best not supplied as `None` to the method
      - has a `render_kw` to collect any uninteresting render parameters

      The interesting parameters should arrive prefilled and also
      do not need to be passed int interior calls to render method
      because the render context has these values. Only values
      different to the render context need provision.
  '''

  def render_wrapper(self_or_cls, *a, ctx=None, **render_kw):
    if ctx is None:
      ctx = self_or_cls.render_context
    apply_attrs = dict(render_defaults)
    apply_attrs.update(render_kw)
    with stackattrs(ctx, **apply_attrs):
      return render_func(self_or_cls, *a, **ctx.__dict__)

  return render_wrapper

class Boxy(ABC):
  ''' The abstract base class for various boxes.
  '''

  # the render context
  render_context = NS(
      arc=True,
      heavy=False,
      attach_w=False,
      attach_e=False,
  )

  def __str__(self):
    ''' Return the default rendering of the text box.
    '''
    return self.render()

  def from_str(cls, s: str) -> Union["Terminal", "TextBox"]:
    ''' Promote a string to a `Terminal` or `TextBox`.
        Nonempty strings with no newlines become `Terminal`s,
        otherwise a `TextBox`.
    '''
    if s and '\n' not in s:
      return Terminal(s)
    return TextBox(s)

  @classmethod
  @render
  def horiz(
      cls,  # used by @render
      width: int,
      middle='',
      *,
      arc,
      heavy,
      left_up=False,
      left_down=False,
      right_up=False,
      right_down=False,
      **_,
  ):
    ''' Compute a horizontal line with an optional symbol in the
        middle and up or down connections.
    '''
    length = max(0, width - len(middle))
    left_length = length // 2
    right_length = length - left_length
    if left_up or left_down:
      left_end = box_char(
          arc=arc, right=True, up=left_up, down=left_down, heavy=heavy
      )
      left_length -= 1
      if left_length < 0:
        raise ValueError(
            f'insuffient room for {middle=} and the leftmost symbol'
        )
    else:
      left_end = ''
    horiz_c = HORIZ_ if heavy else HORIZ
    if right_up or right_down:
      right_end = box_char(
          arc=arc, left=True, up=right_up, down=right_down, heavy=heavy
      )
      right_length -= 1
      if right_length < 0:
        raise ValueError(
            f'insuffient room for {middle=} and the rightmost symbol'
        )
    else:
      right_end = ''
    return left_end + horiz_c * left_length + middle + horiz_c * right_length + right_end

  @abstractmethod
  def render_lines(self, **render_kw):
    ''' Render the box as a list of single line strings.
    '''
    raise NotImplementedError

  @render
  def print(self, file=None, **render_kw):
    if file is None:
      file = sys.stdout
    print(self.render(**render_kw), file=file)

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
  def top_w(self):
    return self.ws[0]

  @property
  def bottom_w(self):
    return self.ws[-1]

  @property
  def es(self):
    return self.e,

  @property
  def top_e(self):
    return self.es[0]

  @property
  def bottom_e(self):
    return self.es[-1]

  @staticmethod
  def conn_char(
      li, lefts: list[int], rights: list[int], arc=True, heavy=False
  ) -> str:
    ''' Compute the connective `box_char` for a column of connective characters.
    '''
    # compute the vertical span
    if lefts:
      if rights:
        top = min(lefts[0], rights[0])
      else:
        top = lefts[0]
    elif rights:
      top = rights[0]
    if lefts:
      if rights:
        bottom = max(lefts[-1], rights[-1])
      else:
        bottom = lefts[-1]
    elif rights:
      bottom = rights[-1]
    return box_char(
        arc=arc,
        heavy=heavy,
        left=li in lefts,
        right=li in rights,
        up=(lefts or rights) and li > top and li <= bottom,
        down=(lefts or rights) and li >= top and li < bottom,
    )

  # this is last to avoid replacing @render
  def render(self, **render_kw):
    ''' Render the text box as a single multiline string.
    '''
    return "\n".join(self.render_lines(**render_kw))

@dataclass
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

@dataclass
class Terminal(Symbol):
  ''' Like a symbol, but with a marker either side.
  '''

  def __hash__(self):
    return id(self)

  def __eq__(self, other):
    return self is other

  @render
  def render_lines(self, heavy, attach_w, attach_e, **_):
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

  @render
  def render_lines(self, *, arc, heavy, attach_w, attach_e, **_):
    ''' Render the text box as a list of single line strings.
    '''
    nlines = self.height
    line_width = max(self.max_text_length, 1)
    return [
        self.horiz(self.width, left_down=True, right_down=True),
        *(
            "".join(
                (
                    box_char(
                        up=True,
                        down=True,
                        left=attach_w and i == self.w,
                        heavy=heavy
                    ),
                    f'{text_line:<{line_width}}',
                    box_char(
                        up=True,
                        down=True,
                        right=attach_e and i == self.e,
                        heavy=heavy
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

  @render
  def render_lines(self, *, heavy, attach_e, attach_w, **_):
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
  content: list[Boxy] = field(default_factory=list)

  def __post_init__(self):
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
  def inner_width(self):
    return max(box.width for box in self.content)

  @property
  def width(self):
    ''' The overall width of the content.
    '''
    return self.inner_width

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

  @render(align='left')  # vs right and... anything else
  def render_lines(self, *, align, heavy, attach_e, attach_w, **_):
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
    ##assert len(lines) == self.height
    return lines

@dataclass
class Choice(Stack):

  def __hash__(self):
    return id(self)

  def __eq__(self, other):
    return self is other

  @property
  def w(self):
    return (super().ws[0] + super().ws[-1]) // 2

  @property
  def ws(self):
    return self.w,

  @property
  def e(self):
    return (super().es[0] + super().es[-1]) // 2

  @property
  def es(self):
    return self.e,

  @property
  def width(self):
    return super().width + 2

  @render
  def render_lines(self, *, arc, heavy, attach_e, attach_w, **_):
    nboxes = len(self.content)
    lines = []
    top_w = self.ws[0]
    bottom_w = self.ws[-1]
    top_e = self.es[0]
    bottom_e = self.es[-1]
    for li, inner_line in enumerate(super().render_lines()):
      lines.append(
          "".join(
              (
                  self.conn_char(
                      li,
                      self.ws if attach_w else (),
                      super().ws,
                      arc=arc,
                      heavy=heavy
                  ),
                  inner_line,
                  self.conn_char(
                      li,
                      super().es,
                      self.es if attach_e else (),
                      arc=arc,
                      heavy=heavy
                  ),
              )
          )
      )
    assert len(lines) == self.height
    return lines

@dataclass
class Merge(Stack):
  ''' Merge multiple inputs into a single output.
  '''

  def __hash__(self):
    return id(self)

  def __eq__(self, other):
    return self is other

  @property
  def e(self):
    ses = super().es
    return (ses[0] + ses[-1]) // 2

  @property
  def es(self):
    return self.e,

  @property
  def width(self):
    return super().width + 1

  @render
  def render_lines(self, *, arc, heavy, attach_e, attach_w, **_):
    nboxes = len(self.content)
    lines = []
    for li, inner_line in enumerate(super().render_lines(attach_e=True)):
      lines.append(
          "".join(
              (
                  inner_line,
                  self.conn_char(
                      li,
                      super().es,
                      self.es if attach_e else (),
                      arc=arc,
                      heavy=heavy
                  ),
              )
          )
      )
    return lines

@dataclass
class Split(Stack):
  ''' Split a single input into multiple outputs.
  '''

  def __hash__(self):
    return id(self)

  def __eq__(self, other):
    return self is other

  @property
  def w(self):
    sws = super().ws
    return (sws[0] + sws[-1]) // 2

  @property
  def ws(self):
    return self.w,

  @property
  def width(self):
    return super().width + 1

  @render
  def render_lines(self, *, arc, heavy, attach_e, attach_w, **_):
    nboxes = len(self.content)
    lines = []
    for li, inner_line in enumerate(super().render_lines(attach_w=True)):
      lines.append(
          "".join(
              (
                  self.conn_char(
                      li,
                      self.ws if attach_w else (),
                      super().ws,
                      arc=arc,
                      heavy=heavy
                  ),
                  inner_line,
              )
          )
      )
    return lines

@dataclass
class Sequence(_RailRoadMulti):
  ''' A railroad sequence.
  '''

  def __hash__(self):
    return id(self)

  def __eq__(self, other):
    return self is other

  @property
  @render(sep_len=2)
  def width(self, *, sep_len, **_):
    return sum(box.width for box in self.content) + sep_len * (
        len(self.content) - 1
    )

  @property
  def height(self):
    return max(box.height for box in self.content)

  @render(sep_len=2)
  def render_lines(self, *, heavy, attach_e, attach_w, sep_len, **_):
    boxes = self.content
    # we start the nominal attach point of the leftmost box at 0
    attach = 0
    box_tops = []
    box_bottoms = []
    # measure the high and low extents of the boxes, adjusting the
    # attach point as we go
    for box in boxes:
      box_tops.append(attach - box.w)
      box_bottoms.append(attach + box.height - box.w)
      attach += box.e - box.w
    boxes_top = min(box_tops)
    boxes_bottom = max(box_bottoms)
    total_lines = boxes_bottom - boxes_top
    lines = [[] for _ in range(total_lines)]
    attach = 0
    sep_spaces = " " * sep_len
    sep_line = self.horiz(sep_len)
    for bi, (box, box_top, box_bottom) in enumerate(zip(boxes, box_tops,
                                                        box_bottoms)):
      pad = " " * box.width
      row = 0
      pad_above = box_top - boxes_top
      pad_below = total_lines - pad_above - box.height
      if pad_above > 0:
        for _ in range(pad_above):
          if bi > 0:
            lines[row].append(sep_spaces)
          lines[row].append(pad)
          row += 1
      for li, box_line in enumerate(box.render_lines(
          attach_w=bi > 0,
          attach_e=bi < len(boxes) - 1,
      )):
        if bi > 0:
          lines[row].append(sep_line if li == box.w else sep_spaces)
        lines[row].append(box_line)
        row += 1
      if pad_below > 0:
        for _ in range(pad_below):
          if bi > 0:
            lines[row].append(sep_spaces)
          lines[row].append(pad)
          row += 1
      attach += box.e - box.w
      assert row == len(lines), f'{row=} != {len(lines)=}'
    return ["".join(line_v) for line_v in lines]

def test_railroad():
  box5 = TextBox("one\ntwo\nthree\nfour\nfive")
  print(box5)
  box5.print(heavy=True)
  seq = Sequence(
      (
          START, Repeat("repeat me"), "2 lines\naaaa",
          Choice(("one", "two", "three", box5)), END
      )
  )
  print(seq)
  seq.print(heavy=True)
  stack = Stack(("st1", "2", "3\n4", "four five"))
  print(stack.render(attach_e=True, align='right'))
  merge = Merge((START, Sequence(("1", "2", "33")), "something"))
  merge.print()
  merge.print(attach_w=True)
  merge.print(attach_e=True)
  merge.print(attach_w=True, attach_e=True)
  split = Split((START, Sequence(("1", "2", "33")), "something"))
  split.print(attach_w=True, align='right')

if __name__ == '__main__':
  test_railroad()
