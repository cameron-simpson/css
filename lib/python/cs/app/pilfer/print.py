#!/usr/bin/env python3

from typing import NamedTuple, Sequence

from bs4 import BeautifulSoup as BS4, NavigableString, Tag as BS4Tag
from typeguard import typechecked

from cs.ascii_art import box_char
from cs.lex import printt

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
      id_attr = attrs.pop('name')
    except KeyError:
      pass
    else:
      label += f' {id_attr!r}'
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

def dump_soup(tag: BS4Tag, *, file=None, **printt_kw):
  ''' Dump the contents of the soup via `cs.lex.printt`
      using `tabulate_soup` to make the table.
  '''
  if isinstance(tag, BS4):
    table = []
    if tag.head:
      table.extend(tabulate_soup(tag.head))
    table.extend(tabulate_soup(tag.body))
  else:
    table = tabulate_soup(tag)
  printt(*table, file=file, **printt_kw)

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
        <h1 id="3" attr="zot">heading 1</h1>
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
    soup = BS4(html, features="lxml")
    dump_soup(soup)
