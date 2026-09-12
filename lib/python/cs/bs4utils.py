#!/usr/bin/env python3

''' Various utility functions and classes for working with the HTML soup from `beautifulsoup4`.
'''

from functools import cached_property
from typing import Any, Callable, Generator, Iterable, Self

from bs4 import BeautifulSoup, Tag as BS4Tag, NavigableString
from icontract import require
from lxml.builder import ElementMaker
from typeguard import typechecked

from cs.lex import cropped_repr, printt
from cs.pfx import pfx
from cs.gimmicks import warning

__version__ = '20260912-post'

DISTINFO = {
    'keywords': ["python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
        "Topic :: Text Processing",
    ],
    'install_requires': [
        'cs.lex',
        'cs.pfx',
        'cs.gimmicks',
        'beautifulsoup4',
        'icontract',
        'lxml',
        'typguard',
    ],
}

# TODO: find_all(...,recursive=False) does this? apparently not?
def child_tags(tag, child_name: str | None = None) -> Iterable[BS4Tag]:
  ''' A generator yielding the immediate child tags of `child`
      whose tag name is `child_name`.
      If `child_name` is `None`, yield all the immediate child tags,
      skipping things like strings and comments.
  '''
  for child in tag.children:
    if isinstance(child, BS4Tag) and (child_name is None
                                      or child.name == child_name):
      yield child

def find_up(
    tag,
    test: str | Callable[[BS4Tag], bool],
    *,
    first=False
) -> Generator[tuple[BS4Tag, BS4Tag], tuple[None, None]]:
  ''' A generator yielding `(found,ref)` 2-tuples obtained by
      search the tag tree left and up from `tag` using `.previous_sibling`
      and `.parent`, matching tags where `test(found)` is true.

      The `test` may be a tag `.name` value (a string) or a callable
      to evaluate a ound tag.

      If `first` is true (default `False`) then the search stops
      after the first match. If there are no matches the tuple
      `(None,None)` is returned (this does not happen if `first`
      is false).

      A primary use case for this is to find the heading tag for `tag`.

      In the tuple, `found` is the matched tag. `ref` is the reference
      tag, the later sibling of `found` where the search started
      for that level; if `found` is at the same level as `tag` then
      `ref` will be `tag`.

      For example, to locate the level 2 heading governing a tag:

          (h2,_), *_ = find_up(tag,lambda found: found.name == 'h2')

      or more concisely:

          (h2,_), *_ = find_up(tag, 'h2')

      or even:

          (h2,_), = find_up(tag, 'h2',first=True)

      Note that the first two will cause Python to raise an exception
      if there are no matches, while the third will provide `h2`
      as `None`.
  '''
  if isinstance(test, str):
    tag_name = test
    test = lambda tag: tag.name is not None and tag.name == tag_name
  reftag = tag
  while reftag is not None:
    prev = reftag.previous_sibling
    while prev is not None:
      if tag.name is not None and test(prev):
        yield prev, reftag
        if first:
          return
      prev = prev.previous_sibling
    reftag = reftag.parent
  if first:
    yield None, None

def find_heading(
    tag,
    filter: Callable[[BS4Tag],
                     bool] = (lambda tag: len(tag.get_text().strip()) > 0),
) -> BS4Tag | None:
  ''' Find the nearest heading satisfying the test `filter(tag)`.
      Return the tag or `None` is one is not found.
      The default `filter` tests that the heading is not empty.
      This uses `find_up` to locate the tag.
  '''
  (h, _), = find_up(
      tag,
      lambda tag: (
          tag.name and tag.name.startswith('h') and tag.name[1:].isdigit() and
          filter(tag)
      ),
      first=True,
  )
  return h

@typechecked
def tabulate_soup(
    tag: BS4Tag | NavigableString
) -> list[list[str, str] | tuple]:
  ''' Return a table describing `soup` for use with `cs.lex.printt`.
      Connect tags with their child tags using Unicode box characters.
  '''
  table = []
  if isinstance(tag, NavigableString):
    text = str(tag).strip()
    if text:
      table.append(['', text])
  else:
    # A tag with interior content.
    attrs = dict(tag.attrs)
    label = tag.name
    # pop off the id attribute if present, include in the label
    try:
      id_attr = attrs.pop('id')
    except KeyError:
      pass
    else:
      label += f' #{id_attr}'
    # pop off the name attribute if present, include in the label
    try:
      name_attr = attrs.pop('name')
    except KeyError:
      pass
    else:
      # I saw an amazon page embed an obscene amount of JSON in a
      # name attribute :-(
      label += f' name={cropped_repr(name_attr)}'
    children = list(
        child for child in tag.children if isinstance(child, NavigableString)
        or child.name not in ('script', 'style')
    )
    # count the subtags which aren't strings
    nsubtags = sum(
        not isinstance(child, NavigableString) for child in children
    )
    if not attrs and len(children) == 1 and isinstance(children[0],
                                                       NavigableString):
      # The super compact form:
      # a tag with no attrs and some text puts the text beside the tag name.
      assert nsubtags == 0
      text = f'{str(children[0]).strip()}'
      table.append([label, text])
    else:
      attr_text = "\n".join(
          f'{attr}={value!r}' for attr, value in sorted(attrs.items())
      )
      table.append([label, attr_text])
      if children:
        subtable = []
        for child in children:
          subtable.extend(tabulate_soup(child))
        table.append(tuple(subtable))
  return table

def printt_soup(tag: BS4Tag, **printt_kw):
  ''' Print the contents of the soup via `cs.lex.printt`
      using `tabulate_soup` to make the table.
  '''
  if isinstance(tag, BS4Tag) and tag.name == 'html':
    table = []
    if tag.head:
      table.extend(tabulate_soup(tag.head))
    table.extend(tabulate_soup(tag.body))
  else:
    table = tabulate_soup(tag)
  printt(*table, **printt_kw)

def as_xml(tag: BS4Tag, *, E=None):
  ''' Transform `tag` into an `lxml` XML element.
  '''
  if E is None:
    E = ElementMaker()
  return E(tag.name, *map(as_xml, tag.children), **tag.attrs)

class Widget:
  ''' Base class for various "widget" HTML constructs, such as a
      TABLE, or in principle anything else regular on a page.

      A `Widgwt` supplies:
      - `__init__(tag)` to record the target BS4 tag, typically the
        top level tag encompassing the wudget
      - `find_all(soup)`: returning a list of the top level tags
        within the BS4 tag `soup`; the default method calls
        `soup.find_all()` with the lower case version of the class
        name via `soup.find_all()`
      - `scan(soup)`: a factory method calling `cls(tag)` for every
        tag found by `find_all(soup)`

      Everything else in a subclass supports whatever needs doing
      with the widget; the `Table` class is an exemplar:
      - its `__init__` method passes the tag to `super().__init__()`
        as normal, then find s a few top level things about the table
        - the caption, header, bodies, footer
      - the default `find_all` is used because the lass name matches
        the HTML tag name
      - everything else more complex is provided as methods or
        `@cached_property` properties, computed on demand
  '''

  def __init__(self, tag: BS4Tag):
    ''' Initialise this `Widget` by saving `tag` as `self.tag` and
        then calling `self.scan()`.
    '''
    self.tag = tag

  @classmethod
  def find_all(cls, soup) -> list[BS4Tag]:
    ''' The default `find_all` finds tags from `soup` whose name matches the class name.
    '''
    return soup.find_all(cls.__name__.lower())

  @classmethod
  def scan(cls, soup) -> list[Self]:
    ''' Return a list of all `Widget`s of this type found in `soup`.
    '''
    return [cls(tag) for tag in cls.find_all(soup)]

class Table(Widget):
  ''' A `Widget` subclass representing an HTML TABLE tag.
  '''

  def __init__(self, tag):
    ''' Scan the TABLE for the basic structures, used for the other properties etc later.

        Note that if there was no TBODY, the immediate rows of the
        TABLE are presented as though they were in a single TBODY.
    '''
    super().__init__(tag)
    self.caption = tag.find('caption')
    self.colgroups = tag.find_all('colgroup', recursive=False)
    self.thead = tag.find('thead')
    self.tbodies = tag.find_all('tbody')
    if not self.tbodies:
      # fake up a single TBODY if there are none
      tbody = BS4Tag(name='tbody')
      for tr in tag.find_all('tr', recursive=False):
        tbody.append(tr)
      self.tbodies = [tbody]
    self.tfoot = tag.find('tfoot')

  @staticmethod
  def cell_colspan(cell: BS4Tag) -> int:
    ''' Compute the `colspan` value for a table cell.
    '''
    colspan = cell.attrs.get("colspan", 1)
    try:
      colspan = int(colspan)
    except ValueError as e:
      warning(f'invalid {colspan=} ({e}), using 1: {cell}')
      colspan = 1
    return colspan

  @staticmethod
  def cell_rowspan(cell: BS4Tag) -> int:
    ''' Compute the `rowspan` value for a table cell.
    '''
    rowspan = cell.attrs.get("rowspan", 1)
    try:
      rowspan = int(rowspan)
    except ValueError as e:
      warning(f'invalid {rowspan=} ({e}), using 1: {cell}')
      rowspan = 1
    return rowspan

  @classmethod
  def row_cells(cls, tr: BS4Tag) -> list[BS4Tag]:
    ''' Return a list of the cells (`TD` or `TH`) from a `TR` tag.
        `colspan` is supported by referencing the same cell multiple times.
        Only `TD` and `TH` tags which are immediate children of the `TR` are recognised.
    '''
    cells = []
    for cell in tr.find_all(lambda tag: tag.name in ('th', 'td'),
                            recursive=False):
      colspan = cls.cell_colspan(cell)
      for _ in range(colspan):
        cells.append(cell)
    return cells

  @classmethod
  @require(lambda section: section.name in ('thead', 'tbody', 'tfoot'))
  def section_rows(cls, section: BS4Tag | None) -> list[list[BS4Tag]]:
    ''' Return the rows from a table section such as `THEAD`, `TBODY`, or `TFOOT`.
        `rowspan` is supported by referencing the same cell in lower rows.
    '''
    if section is None:
      return []
    trs = section.find_all('tr', recursive=False)
    rows = [[] for _ in trs]
    for row_index, row_cells in enumerate(cls.row_cells(tr) for tr in trs):
      row = rows[row_index]
      assert rows[row_index] is row
      cell_pos = 0
      for cell in row_cells:
        # advance past any cells presupplied by a rowspan
        while cell_pos < len(row) and row[cell_pos] is not None:
          cell_pos += 1
        if cell_pos < len(row):
          assert row[cell_pos] is None
          row[cell_pos] = cell
        else:
          assert cell_pos == len(row)
          row.append(cell)
        # propagate this cell to further rows for its rowspan
        for offset in range(1, cls.cell_rowspan(cell)):
          subindex = row_index + offset
          if subindex == len(rows):
            subrow = []
            rows.append(subrow)
          else:
            assert subindex < len(rows)
            subrow = rows[subindex]
          while len(subrow) < cell_pos:
            subrow.append(None)
          if cell_pos < len(subrow):
            subrow[cell_pos] = cell
          else:
            assert len(subrow) == cell_pos
            subrow.append(cell)
        cell_pos += 1
    # infill None cells with empty TD tags
    for row in rows[1:]:
      for i, cell in enumerate(row):
        if cell is None:
          row[i] = BS4Tag(name='td')
    return rows

  @cached_property
  def head_rows(self) -> list[list[BS4Tag]]:
    ''' The rows from the table THEAD, if any.
    '''
    return self.section_rows(self.thead)

  @cached_property
  def body_rows(self) -> list[list[BS4Tag]]:
    ''' The rows from the table TBODY tags, if any.
        Note that if there was no TBODY, the immediate rows of the
        TABLE are presented as though they were in a single TBODY.
    '''
    rows = []
    for body in self.tbodies:
      rows.extend(self.section_rows(body))
    return rows

  @cached_property
  def foot_rows(self) -> list[list[BS4Tag]]:
    ''' The rows from the table TFOOT, if any.
    '''
    return self.section_rows(self.tfoot)

  def as_lists(self,
               *,
               omit_header=False,
               omit_footer=False) -> list[list[BS4Tag]]:
    ''' Return the table contents as a list-of-lists-of-tags;
        each inner list is a row of tags.
        The innermost elements are the TH or TD tags.
        Note that cells spanning multiple columns or rows via their
        `colspan` or `rowspan` are the same reference.

        Parameters:
        * `omit_header`: do not include rows from the `THEAD` section
        * `omit_footer`: do not include rows from the `TFOOT` section
    '''
    rows = self.all_rows = []
    if not omit_header:
      rows.extend(self.head_rows)
    rows.extend(self.body_rows)
    if not omit_footer:
      rows.extend(self.foot_rows)
    return rows

  # the type of a cell value entry
  IndexedCellValueType = tuple[str, int, int, int, BS4Tag, Any]

  # the type of a cell conversion function
  IndexedCellConversionFunction = Callable[[str, int, int, int, BS4Tag], Any]

  def as_indexed_values(
      self,
      *,
      convert: IndexedCellConversionFunction | None = None,
      convert_head_cell: IndexedCellConversionFunction | None = None,
      convert_body_cell: IndexedCellConversionFunction | None = None,
      convert_foot_cell: IndexedCellConversionFunction | None = None,
      omit_header=False,
      omit_footer=False,
  ) -> list[list[IndexedCellValueType]]:
    ''' Return the table contents as a list-of-lists of indexed cell values.
        Each inner list contains the cell value records from a row.

        this is an elaborate counterpart to the `as_lists` method.

        Parameters:
        * `convert`: the default cell conversion function
        * `convert_head`: the header cell conversion function, default from `convert`
        * `convert_body`: the body cell conversion function, default from `convert`
        * `convert_foot`: the footer cell conversion function, default from `convert`
        * `omit_header`: do not include rows from the `THEAD` section
        * `omit_footer`: do not include rows from the `TFOOT` section

        The conversion functions accept the following positional parameters:
        * `section_type`: one of `"THEAD"`, `"TBODY"` or `"TFOOT"`
        * `section_index`: the index of the section, 0 for the
          header or footer but there may be multiple `TBODY` sections
        * `row_index`: the index of the row within the section
        * `col_index`: the index of the column within the row
        * `cell`: the `TD` or `TH` tag for the cell
        The function should return the converted value of `cell`.
        The default conversion function returns `cell`.

        The row and column indices supplied to the conversion unction
        are of the _resolved_ cells, after expansion via the `colspan`
        or `rowspan` values.
        For example, a row with 3 cells whose second cell had a
        `colspan=2` would be a list of 4 cells, with the second
        original cell referenced in the second and third items of
        the list; it _will_ be the same tag instance.

        Each cell instance is converted only once; the same cell
        spanning multiple columns or rows will have the same value
        instance in the result record.

        The resulting list-of-lists contains value records, a 6-tuple
        of `(section_type,section_index,row_index,col_index,cell,value)`.
        Note that the `row_index` and `col_index` are those of the
        top left index where the `cell` was first encountered for
        cells spanning multiple columns or rows.

        Examples:

        Convert every numeric cell to its `float` value, leave other cells as their text.

            def as_float(section_type, section_index, row_index, column_index, cell):
                text = cell.get_text.strip()
                try:
                    value = float(text)
                except ValueError:
                    value = text
                return value

            values = T.as_indexed_values(convert=as_float)

        Convert only the body cells, keep the headers as tags, omit the footer:

            values = T.as_indexed_values(convert_body_cell=as_float, omit_footer=True)
    '''
    if convert is None:
      convert = (
          lambda section_type, section_index, row_index, column_index, cell:
          cell
      )
    if convert_head_cell is None:
      convert_head_cell = convert
    if convert_body_cell is None:
      convert_body_cell = convert
    if convert_foot_cell is None:
      convert_foot_cell = convert
    # cell_indicies={}
    # mapping of id(tag) to (row_index,colum_index,converteed_value)
    converted = {}

    @pfx
    def conv(
        section_type, section_index, row_index, col_index, cell
    ) -> self.IndexedCellValueType:
      cell_id = id(cell)
      try:
        value_record = converted[cell_id]
      except KeyError:
        if section_type == 'THEAD':
          value = convert_head_cell(
              section_type, section_index, row_index, col_index, cell
          )
        elif section_type == 'TBODY':
          value = convert_body_cell(
              section_type, section_index, row_index, col_index, cell
          )
        elif section_type == 'TFOOT':
          value = convert_foot_cell(
              section_type, section_index, row_index, col_index, cell
          )
        else:
          raise RuntimeError(f'unhandled {section_type=}')
        value_record = converted[cell_id] = (
            section_type, section_index, row_index, col_index, cell, value
        )
      return value_record

    rows = self.all_rows = []
    if not omit_header:
      for row_index, row in enumerate(self.head_rows):
        rows.append(
            [
                conv('THEAD', 0, row_index, col_index, cell)
                for col_index, cell in enumerate(row)
            ]
        )
    for body_index, body in enumerate(self.tbodies):
      for row_index, row in enumerate(self.section_rows(body)):
        rows.append(
            [
                conv('TBODY', body_index, row_index, col_index, cell)
                for col_index, cell in enumerate(row)
            ]
        )
    if not omit_footer:
      for row_index, row in enumerate(self.foot_rows):
        rows.append(
            [
                conv('TFOOT', 0, row_index, col_index, cell)
                for col_index, cell in enumerate(row)
            ]
        )
    return rows

  @cached_property
  def all_rows(self) -> list[list[BS4Tag]]:
    ''' Return all the rows from the header, bodies, and footer.
    '''
    return self.as_lists()

  @cached_property
  def title(self):
    ''' The title of the table, from the caption or the nearest heading.
    '''
    if self.caption:
      title = self.caption.get_text()
    else:
      h = find_heading(self.tag)
      if h:
        title = h.get_text().strip()
      else:
        title = None
    return title

  def printt(self):
    ''' Print the table text.
    '''
    seen_ids = set()

    def row_trow(row):
      ''' Render a row of cells for the table.
          The row should have come from `section_rows` i.e. the
          `colspan` is already applied.
      '''
      trow = []
      for i, cell in enumerate(row):
        if id(cell) in seen_ids:
          trow.append("")
        else:
          seen_ids.add(id(cell))
          trow.append(cell.get_text())
      return trow

    def section_trows(rows):
      ''' Render the rows of a section, each of whose rows should
          have come from `section_rows` i.e. the `colspan` is already applied.
      '''

    table = []
    heading = self.title or self.tag.name.upper()
    table.append([heading])
    if self.thead:
      table.extend(((*map(row_trow, self.head_rows),),))
    for tbody in self.tbodies:
      table.extend(((*map(row_trow, self.section_rows(tbody)),),))
    if self.tfoot:
      table.extend(((*map(row_trow, self.foot_rows),),))
    ##print(self.tag.prettify())
    ##pprint(table)
    printt(*table)

if __name__ == '__main__':
  for html in ('foo', '<h1>foo</h1>', '''
    <html>
      <head>
        <title>title here</title>
      </head>
      <body>
        <h1 id="3" attr="zot" attr2="2">heading 1</h1>
        body here
        <h1>second heading</h1>
        second
        third
      </body>
    </html>
  ''', '''
  <H1>H1 HEADING</H1>
  <TABLE>
    <THEAD><TR><TD>heaing 1<TD>heading 2
    <TBODY><TR><TD>Label<TD ROWSPAN="2">9.5
           <TR>
           <TR><TD>3<TD>4
    <TFOOT><TR><TD>foot1<TD>5
    </TABLE>
  '''):
    print("======================================")
    print(html)
    print("--------------------------------------")
    soup = BeautifulSoup(html, features="lxml")
    printt_soup(soup)
    for table in Table.scan(soup):
      print()
      table.printt()

      for row in table.as_lists():
        print(*map(type, row))

      def as_float(section_type, section_index, row_index, col_index, cell):
        text = cell.get_text().strip()
        try:
          return float(text)
        except (TypeError, ValueError):
          return text

      for row_index, row in enumerate(table.as_indexed_values(
          convert_body_cell=as_float, omit_footer=True)):
        for col_index, record in enumerate(row):
          section_type = record[0]
          value = record[-1]
          print(row_index, col_index, section_type, type(value), value)
