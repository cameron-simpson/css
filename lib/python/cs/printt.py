#!/usr/bin/env python3

''' Print text tables, in the terminal or to a file, using `printt()` and its supporting functions.
'''

from datetime import date, datetime
from pprint import pformat
from typing import Any, Callable, Iterable, Mapping, Sequence

from typeguard import typechecked

from cs.ascii_art import box_char, HORIZ, LARGE_CIRCLE
from cs.deco import attr
from cs.lex import indent
from cs.seq import with_neighbours

__version__ = '20261016-post'

DISTINFO = {
    'keywords': ["python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
        "Topic :: Text Processing",
    ],
    'install_requires': [
        'cs.ascii_art>=LARGE_CIRCLE',
        'cs.deco',
        'cs.lex',
        'cs.seq',
        'typeguard',
    ],
}

##class TabulatePrettyPrinter(PrettyPrinter):
##  ''' A `PrettyPrinter` subclass which presents `date` and
##      `datetime` as ISO8601 strings.
##
##      This is used by `tabulate` and `printt`.
##  '''
##
##  def __init__(self, compact=True, sort_dicts=True, **ppkw):
##    ''' The default format settings use `compact=True` and `sort_dicts=True`.
##    '''
##    super().__init__(compact=compact, sort_dicts=sort_dicts, **ppkw)
##
##  def format(self, obj, *fmt_a) -> Tuple[str, bool, bool]:
##    ''' Use ISO8601 for `date` and `datetime` objects.
##    '''
##    print("PFORMAT", type(obj))
##    if type(obj) is not str:
##      breakpoint()
##    if isinstance(obj, date):
##      return obj.isoformat(), True, False
##    if isinstance(obj, datetime):
##      return obj.isoformat(' '), True, False
##    print("check for image", type(obj), repr(obj))
##    try:
##      from cs.imageutils import Image, SixelImageString
##    except ImportError as e:
##      ##warning(f'cs.imageutils import error: {e}')
##      ##breakpoint()
##      # this happens very ealy for cs.cmdutils
##      pass
##    else:
##      ##print("imported cs.imageutils")
##      print("check for Image vs", type(obj))
##      if isinstance(obj, Image.Image):
##        print("an Image, return SixelImageString")
##        sis = SixelImageString(obj)
##        print("sis =", repr(sis))
##        breakpoint()
##        print("RRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRR")
##        return SixelImageString(obj), True, False
##      print("check for SixelImageString vs", SixelImageString)
##      print(isinstance(obj, SixelImageString))
##      if isinstance(obj, SixelImageString):
##        print("a SixelImageString, return directly")
##        return obj, True, False
##    print("PFORMAT: fall through to super for type", type(obj))
##    return super().format(obj, *fmt_a)

def row_cells(
    row: str | Sequence[Any],
    as_str: Callable[Any, str] | None = None,
) -> list[list[str]]:
  r'''Turn a row of items into a grid.

      This is a helper function for `flatten_table_rows()`.

      Process:
      - convert each non-`str` item into a string using `as_str`
      - break each string into lines
      - make enough rows to cover the tallest column

      The default `as_str` comes from `tabulate.as_str`.

      Example:

          >>> for cells in row_cells(['col1', 'multi\nline\ntext', 'goes\nhere', 'and\nhere']):
          ...     print(cells)
          ...
          ['col1', 'multi', 'goes', 'and']
          ['', 'line', 'here', 'here']
          ['', 'text', '', '']
  '''
  if as_str is None:
    as_str = tabulate.default_as_str
  # promote a str to a single item list
  if isinstance(row, str):
    row = [row]
  # turn non-str row items into strings
  row = [(cell if isinstance(cell, str) else as_str(cell)) for cell in row]
  if all("\n" not in cell for cell in row):
    # no multiline row cells
    return [row]
  # split multiline cells into columns, pad columns to match
  cols = [[subcell.rstrip() for subcell in cell.splitlines()] for cell in row]
  max_height = max(map(len, cols))
  row_cells = [
      [(col[subrow] if subrow < len(col) else '')
       for col in cols]
      for subrow in range(max_height)
  ]
  return row_cells

@typechecked
def flatten_table_rows(
    table_rows: list,
    as_str: Callable[Any, str] | None = None,
) -> tuple[list[list[str]], list[int]]:
  ''' Flatten a list of table rows or tuples-of-table-rows into a
      `(flat_rows,attach)` 2 -tuple where `flat_rows` is a list lof
      list-of-str rows and `attach` is a list of indices into `flat_rows`
      which are attachment points.
      Nested subtables are connected with lines.

      The default `as_str` comes from `tabulate.as_str`.

      This is a helper function for `tabulate()`, itself a helper
      function for `printt()`.
  '''
  if as_str is None:
    as_str = tabulate.default_as_str

  def into_lists_and_tuples(raw_rows):
    ''' Expand the rows into a list of list or tuple.
    '''
    lt_rows = []
    for row in raw_rows:
      if isinstance(row, (list, tuple)):
        lt_rows.append(row)
      elif isinstance(row, Mapping):
        m_rows = list(row.items())
        # we do not sort in place because a TypeError is raised
        # during the sort if the keys are not all comparable
        try:
          m_rows = sorted(m_rows)
        except TypeError:
          pass
        lt_rows.append(tuple(map(list, m_rows)))
      else:
        lt_rows.append([row])
    return lt_rows

  def is_attachable_cell_grid(cells):
    ''' Test if a cell row has nonwhitepace in column 0
      '''
    return bool(cells and cells[0] and cells[0][0].lstrip())

  # promote rows to lists or AttachedLines
  rows: list[list[str]] = []
  attach: list[int] = []

  def indent_cell(cell, line_indent: str):
    try:
      indent_method = cell.indent
    except AttributeError:
      pass
    else:
      if callable(indent_method):
        return indent_method(line_indent)
    return indent(cell, line_indent)

  for prev_trow, trow, next_trow in with_neighbours(
      into_lists_and_tuples(table_rows)):
    # FIXME: this calls row_cells twice on each row
    #        maybe call row_cells on all lists?
    #        in a prepass?
    if isinstance(trow, list):
      attach_below = bool(
          isinstance(next_trow, tuple) and next_trow
          and is_attachable_cell_grid(row_cells(next_trow[0], as_str=as_str))
      )
      cells = row_cells(trow, as_str=as_str)
      attachable = is_attachable_cell_grid(cells)
      if attachable:
        attach.append(len(rows))
      # append the cell rows, possibly indented and attached
      for ci, cell_row in enumerate(cells):
        if ci > 0 and attach_below:
          line_indent = box_char(
              arc=True,
              up=True,
              down=attach_below,
              right=attachable and ci == 0,
          )
          cell_row[0] = indent_cell(cell_row[0], line_indent)
        rows.append(cell_row)
    elif isinstance(trow, tuple):
      # an indented subscetion
      subrows, subattach = flatten_table_rows(
          list(trow), as_str=as_str
      )  # was True
      # indent all the subrows
      n_subattach = len(subattach)
      for subndx, subrow in enumerate(subrows):
        # NB: must be before the subattach.pop(0) below
        attach_above = bool(
            subattach
            and (type(prev_trow) is list or len(subattach) < n_subattach)
        )
        do_attach = bool(subattach and subattach[0] == subndx)
        if do_attach:
          subattach.pop(0)
        sub_attach_below = bool(subattach)  # more things to come
        up = attach_above  ## do_attach or sub_attach_below,
        down = sub_attach_below
        right = do_attach
        line_indent = (
            LARGE_CIRCLE if right and not up and not down else
            box_char(arc=True, up=up, down=down, right=right)
        ) + (
            HORIZ if do_attach else " "
        )
        if subrow:
          subrow[0] = indent_cell(subrow[0], line_indent)
        rows.append(subrow)
    else:
      raise TypeError(f'row type {type(trow)} is neither list nor tuple')
  return rows, attach

def tabulate_as_str(
    obj, *, compact=True, sort_dicts=True, **pformat_kw
) -> str:
  ''' The default function to return the printable form of an object `obj`
      for use in a table in `tabulate()`.

      This function calls `pprint.pformat()` except for certain specially handled types:
      - `date` and `datetime`: their ISO8601 format
      - `PIL.Image.Image`: is cs.imageutils is available, a SIXEL presentation
  '''
  if isinstance(obj, date):
    return obj.isoformat()
  if isinstance(obj, datetime):
    return obj.isoformat(' ')
  try:
    from cs.imageutils import Image, SixelImageString
  except ImportError:
    # this happens very early for cs.cmdutils
    pass
  else:
    if isinstance(obj, Image.Image):
      return SixelImageString(obj)
    if isinstance(obj, SixelImageString):
      return obj
  return pformat(obj, compact=compact, sort_dicts=sort_dicts, **pformat_kw)

@attr(default_as_str=tabulate_as_str)
def tabulate(
    *rows,
    sep='  ',
    as_str: Callable[Any, str] | None = None,
) -> Iterable[str]:
  r'''A generator yielding lines of values from `rows` aligned in columns.

      Usually, each row in rows is a `list` of strings. Non-`str` objects are
      promoted to `str` via the `as_str` function.
      If the strings contain newlines they will be split into
      subrows.

      A row may also be a `tuple`, indicating a subtable, which
      will be embedded with its column 0 indented and joined up
      with lines to indicate structure.

      A row may also be a mapping, in which case it is transformed
      into multiple rows of `[key,value]` from the `row.items()`,
      sorted by `key` if possible.

      The default `as_str` function is `TabulatePrettyPrinter().pformat`;
      the `TabulatePrettyPrinter` class is a subclass of `pprint.PrettyPrinter`
      which also understands `datetime.date` and `datetime.datetime` objects.
      This is exposed as `tabulate.default_as_str`.

      Example:

          >>> for row in tabulate(
          ...    ['one col'],
          ...    ['three', 'column', 'row'],
          ...    ['row3', 'multi\nline\ntext', 'goes\nhere', 'and\nhere'],
          ...    ['two', 'cols'],
          ...    (
          ...        ['subrow', 'description'],
          ...        (["0nd", "subtable"],),
          ...        (
          ...            ['inner', 'table'],
          ...            ['', 'unattached'],
          ...            ['inner\nbottom', 'last inner row'],
          ...        ),
          ...        (["2nd\nbbb", "subtable"],),
          ...        ['subrow2'],
          ...     ),
          ...     ['one col'],
          ...     ['three', 'column', 'row'],
          ...     ['row3', 'multi\nline\ntext', 'goes\nhere', 'and\nhere'],
          ...     ['two', 'cols'],
          ...     (
          ...        ['subrow','description'],
          ...        (
          ...           ['inner','table'],
          ...           ['','unattached'],
          ...           ['inner\nbottom','last inner row'],
          ...        ),
          ...     ),
          ...     ['subrow2'],
          ... ):
          ...     print(row)
          ...
          one col
          three       column          row
          row3        multi           goes  and
                      line            here  here
                      text
          two         cols
          ├─subrow    description
          │ ╰─0nd     subtable
          │ ╭─inner   table
          │ │         unattached
          │ ╰─inner   last inner row
          │   bottom
          │ ◯─2nd     subtable
          │   bbb
          ╰─subrow2
          one col
          three       column          row
          row3        multi           goes  and
                      line            here  here
                      text
          two         cols
          ╰─subrow    description
            ├─inner   table
            │         unattached
            ╰─inner   last inner row
              bottom
          subrow2
          >>>
  '''
  if as_str is None:
    as_str = tabulate.default_as_str
  if not rows:
    # avoids max of empty list
    return
  rows, _ = flatten_table_rows(list(rows), as_str=as_str)
  # pad short rows with empty columns
  max_cols = max(map(len, rows))
  for row in rows:
    if len(row) < max_cols:
      row += [''] * (max_cols - len(row))
  col_widths = [
      max(map(len, (row[c]
                    for row in rows)))
      for c in range(max(map(len, rows)))
  ]
  for row in rows:
    yield sep.join(
        f'{col_val:<{col_widths[c]}}' for c, col_val in enumerate(row)
    ).rstrip()

def printt(
    *table, file=None, flush=False, indent='', print_func=None, **tabulate_kw
):
  ''' A wrapper for `tabulate()` to print the results.
      Each positional argument is a table row.

      Parameters:
      * `file`: optional output file, passed to `print_func`
      * `flush`: optional flush flag, passed to `print_func`
      * `indent`: optional leading indent for the output lines;
        if this is an `int` instead of a `str`, use that many spaces
      * `print_func`: optional `print()` function, default `builtins.print`
      Other keyword arguments are passed to `tabulate()`.
  '''
  if isinstance(indent, int):
    indent = ' ' * indent
  if print_func is None:
    # obtain the _current_ builtins.print
    from builtins import print as print_func
  for line in tabulate(*table, **tabulate_kw):
    print_func(indent + line, file=file, flush=flush)
