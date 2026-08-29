#!/usr/bin/env python3

''' Some little utility functions for working with the soup from `beautifulsoup4`.
'''

from functools import cached_property
from typing import Iterable, Self

from bs4 import BeautifulSoup, Tag as BS4Tag, NavigableString
from icontract import require
from lxml.builder import ElementMaker
from typeguard import typechecked

from cs.lex import cropped_repr, printt
from cs.gimmicks import warning

DISTINFO = {
    'keywords': ["python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
        "Topic :: Text Processing",
    ],
    'install_requires': [
        'beautifulsoup4',
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
  ''' Base class for various "widget" HTML constructs, such as TABLE.
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

        This Defined
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
    ''' Return a list of the cells (TD or TH) from a TR tag.
        This is pretty simple minded, with initial support for `colspan=`
        but no support for `rowspan=`.
        `colspan` is supported by referencing the same cell multiple times.
        Only TD and TH tags which are immediate children of the TR are recognised.
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
    ''' Return the rows from a table sections such as THEAD, TBODY, or TFOOT.
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
        while cell_pos < len(row) and row[cell_pos] is not None:
          cell_pos += 1
        if cell_pos < len(row):
          assert row[cell_pos] is None
          row[cell_pos] = cell
        else:
          assert cell_pos == len(row)
          row.append(cell)
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

  @cached_property
  def all_rows(self) -> list[list[BS4Tag]]:
    ''' Return all the rows from the header, bodies, and footer.
    '''
    rows = self.all_rows = []
    rows.extend(self.head_rows)
    rows.extend(self.body_rows)
    rows.extend(self.foot_rows)
    return rows

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
      ''' Render the rows of a section, each of whose rows should have come from `section_rows` i.e. the
          `colspan` is already applied.
      '''

    table = []
    table.append(
        [
            self.caption.get_text()
            if self.caption else f'<{self.tag.name.upper()}>'
        ]
    )
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
  for html in (
      'foo',
      '<h1>foo</h1>',
      '''
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
  ''',
  ):
    print("======================================")
    print(html)
    print("--------------------------------------")
    soup = BeautifulSoup(html, features="lxml")
    printt_soup(soup)
