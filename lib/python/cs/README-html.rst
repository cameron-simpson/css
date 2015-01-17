HTML transcription functions.
=============================

Malformed markup is enraging. Therefore, when I must generate HTML I construct a token structure using natural Python objects (strings, lists, dicts) and use this module to transcribe them to syntacticly correct HTML. This also avoids lots of tediuous and error prone entity escaping.

* tok2s: transcribe tokens to a string; trivial wrapper for puttok

* puttok: transcribe tokens to a file

* text2s: transcribe a string to an HTML-safe string; trivial wrapper for puttext

* puttext: transcribe a string as HTML-safe text to a file

* BR: a convenience token for <br/>, which I use a lot

A "token" in this scheme is:

* a string: transcribed safely to HTML

* an int or float: transcribed safely to HTML

* a preformed token object with .tag (a string) and .attrs (a mapping) attributes

* a sequence: an HTML tag: element 0 is the tag name, element 1 (if a mapping) is the element attributes, any further elements are enclosed tokens

  - because a string like "&foo" gets its "&" transcribed into the entity "&amp;", a single element list whose tag begins with "&" encodes an entity, example: ["&lt;"]

Example::

  from cs.html import puttoken, BR
  [...]
  table = ['TABLE', {'width': '80%'},
           ['TR',
            ['TD', 'a truism'],
            ['TD', '1 < 2']
           ]
          ]
  [...]
  puttoken(sys.stdout,
            'Here is a line with a line break.', BR,
            'Here is a trite table.',
            table)

