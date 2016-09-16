HTML transcription functions.
=============================

Malformed markup is enraging. Therefore, when I must generate HTML I construct a token structure using natural Python objects (strings, lists, dicts) and use this module to transcribe them to syntacticly correct HTML. This also avoids lots of tedious and error prone entity escaping.

A "token" in this scheme is:

* a string: transcribed safely to HTML, eg "some text here"

* an int or float: transcribed safely to HTML, eg 1 or 2.5.

* a sequence: an HTML tag: element 0 is the tag name, element 1 (if a mapping) is the element attributes, any further elements are enclosed tokens, eg: ['H1', {'align': 'centre'}, 'Heading Text']

  - because a string like "&foo" gets its "&" transcribed into the entity "&amp;", a single element list whose tag begins with "&" encodes an entity, example: ["&lt;"]

* a preformed token object with .tag (a string) and .attrs (a mapping) attributes

Core functions:
---------------

* ``transcribe(*tokens)``: a generator yielding strings comprising HTML

* ``xtranscribe(*tokens)``: a generator yielding strings comprising XHTML

* ``attrquote(s)``: quote the string ``s`` for use as a tag attribute according to HTML 4.01 section 3.2.2

* ``nbsp(s)``: convert ``s`` to nonbreaking text: generator yielding tokens with whitespace converted to &nbsp; entities

Convenience routines:
---------------------

* ``transcribe_s(*tokens)``: convert ``tokens`` into a string containing HTML

* ``xtranscribe_s(*tokens)``: convert ``tokens`` into a string containing XHTML

Obsolete:
---------
* tok2s: transcribe tokens to a string; trivial wrapper for puttok
* puttok: transcribe tokens to a file
* text2s: transcribe a string to an HTML-safe string; trivial wrapper for puttext
* puttext: transcribe a string as HTML-safe text to a file

Example::

  from cs.html import transcribe, transcribe_s, xtranscribe, puttok
  [...]
  table = ['TABLE', {'width': '80%'},
           ['TR',
            ['TD', 'a truism'],
            ['TD', '1 < 2']
           ]
           ['TR',
            ['TD', 'a couple'],
            ['TD', 'A & B']
           ]
          ]
  prose_with_table = [
            'Here is a line with a line break.', ['BR'],
            'Here is a trite table:',
            table,
          ]
  [...]
  print('Here is the table's HTML:', transcribe_s(table))
  [...]
  # write HTML tokens to a file
  for s in transcribe(['H1', {'align': 'left'}, 'Prose'], *prose_with_table):
    fp.write(s)
  [...]
  # write XHTML tokens to a file
  for s in xtranscribe(*prose_with_table):
    fp.write(s)

