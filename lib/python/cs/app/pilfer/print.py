#!/usr/bin/env python3

from typing import NamedTuple, Sequence

from bs4 import BeautifulSoup as BS4, NavigableString, Tag as BS4Tag
from typeguard import typechecked

from cs.ascii_art import box_char
from cs.bs4utils import printt_soup
from cs.deco import OBSOLETE
from cs.lex import printt

dump_soup = OBSOLETE(printt_soup)

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
