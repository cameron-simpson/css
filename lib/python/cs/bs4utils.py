#!/usr/bin/env python3

''' Some little utility functions for working with the soup from `beautifulsoup4`.
'''

from typing import Iterable

from bs4.element import Tag as BS4Tag

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
def child_tags(tag, child_name: str = None) -> Iterable[BS4Tag]:
  ''' A generator yielding the immediate child tags of `child`
      whose tag name is `child_name`.
      If `child_name` is `None`, yield all the immediate child tags,
      skipping things like strings and comments.
  '''
  for child in tag.children:
    if isinstance(child, BS4Tag) and (child_name is None
                                      or child.name == child_name):
      yield child

def table_grid(table: BS4Tag) -> list[list]:
  ''' Given a `<TABLE>` tag, return a `list[list]` representing
      the text contents of the table in a grid.
      This is pretty simple minded, with initial support for `colspan=`
      but no support for `rowspan=`.
      `colspan` is supported by associating the same datum with multiple cells.
      `<TH>` and `<TR>` rows are supported but not differentiated.
      Only `<TH>` and `<TR>` which are immediate children of the `<TABLE>` tag
      are recognised.
      Only `<TD>` which are immediate children of `<TH>` or `<TR>` are recognised.
  '''
  # TODO: rowspan=
  # TODO: pad rows? optionally?
  rows = []
  for tx in child_tags(table):
    if tx.name in ('th', 'tr'):
      row = []
      for td in child_tags(tx, 'td'):
        datum = td.text.strip()
        colspan = td.get("colspan", 1)
        try:
          colspan = int(colspan)
        except ValueError:
          colspan = 1
        for _ in range(colspan):
          row.append(datum)
      rows.append(row)
  return rows
