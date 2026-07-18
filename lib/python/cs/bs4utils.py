#!/usr/bin/env python3

''' Some little utility functions for working with the soup from `beautifulsoup4`.
'''

import json
from json.decoder import JSONDecodeError
from pprint import pformat
from typing import Iterable

from bs4 import BeautifulSoup, Tag as BS4Tag, NavigableString
from typeguard import typechecked

from cs.lex import cropped_repr, printt

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
      # I saw an amazon page embed an obscene amount of JSON in a name attribute :-(
      label += f' name={cropped_repr(name_attr)}'
    children = list(
        child for child in tag.children if isinstance(child, NavigableString)
        or child.name not in ('script', 'style')
    )
    # count the subtags which aren't strings
    nsubtags = sum(
        not isinstance(child, NavigableString) for child in children
    )
    bigrows = []
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
