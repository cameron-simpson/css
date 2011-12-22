#!/usr/bin/python
#
# Textual representation and parsing of Nodes and attribute values.
#       - Cameron Simpson <cs@zip.com.au>
#

from contextlib import closing
import csv
import os
import re
import tempfile
import sys
from types import StringTypes
if sys.hexversion < 0x02060000:
  import simplejson as json
else:
  import json
from cs.logutils import Pfx, error, info, warning
from cs.mappings import parseUC_sAttr
import cs.sh
from .node import Node, nodekey
from .export import import_csv_wide

## NOT USED ? ## # regexp to match name(, name)*
## NOT USED ? ## re_NAMELIST = re.compile(r'([a-z][a-z0-9]+)(\s*,\s*([a-z][a-z0-9]+))*')
## NOT USED ? ## # regexp to do comma splits
## NOT USED ? ## re_COMMASEP = re.compile(r'\s*,\s*')

# JSON string expression, lenient
re_JSON_STRING = re.compile(r'"([^"\\]|\\.)*"')
# JSON simple integer
re_INT = re.compile(r'-?[0-9]+')
# "bare" URL
re_BAREURL = re.compile(r'[a-z]+://[-a-z0-9.]+/[-a-z0-9_.]*')
## barewords only for node names ## re_BAREWORD = re.compile(r'[a-z][a-z0-9]*')

def dumpNodeAttrs(N, ofp):
  # TODO: use attr_value_to_text() somehow
  ofp.write("# %s\n" % (N,))
  for attr in sorted(N.keys()):
    k, plural = parseUC_sAttr(attr)
    assert k is not None, "bad attribute: %s" % (attr,)
    assert not plural, "unexpected plural attribute: %s" % (attr,)
    first_value = True
    for value in N[attr]:
      ofp.write('%-15s %s\n'
                % ( (attr if first_value else ''),
                    N.nodedb.totoken(value, node=N, attr=attr)
                  )
               )
      first_value = False

def loadNodeAttrs(N, ifp, doCreate=False):
  ''' Read input of the form:
        ATTR    value
                value
                ...
      and return a mapping of ATTR->[values...].
      This is used by editNode().
  '''
  new_attrs = {}
  prev_attr = None
  for line in ifp:
    assert line.endswith('\n'), "%s: unexpected EOF" % (str(ifp),)
    line = line.rstrip()
    if len(line) == 0:
      # skip blank lines
      continue
    ch1 = line[0]
    if ch1 == '#':
      # skip comments
      continue
    if ch1.isspace():
      # indented ==> continuation line, get attribute name from previous line
      assert prev_attr is not None, "%s: unexpected indented line" % (str(ifp),)
      attr = prev_attr
      value_text = line.lstrip()
    else:
      # split into attribute name and value
      attr, value_text = line.split(None, 1)
      k, plural = parseUC_sAttr(attr)
      assert k, "%s: invalid attribute name \"%s\"" % (str(ifp), attr)
      ks = k+'s'
      assert not k.endswith('_ID'), \
             "%s: invalid attribute name \"%s\" - FOO_ID forbidden" \
               % (str(ifp), attr)
      prev_attr = attr

    new_attrs \
      .setdefault(k, []) \
      .extend(text_to_values(value_text, N, k, doCreate=doCreate))

  return new_attrs

def editNode(N, editor=None, doCreate=False):
  ''' Edit this node interactively.
  '''
  if editor is None:
    editor = os.environ.get('EDITOR', 'vi')
  with tempfile.NamedTemporaryFile(suffix='.csv') as T:
    with Pfx(T.name):
      dumpNodeAttrs(N, T)
      T.flush()
      qname = cs.sh.quotestr(T.name)
      os.system("%s %s" % (editor, qname))
      with closing(open(T.name)) as ifp:
        new_attrs = loadNodeAttrs(N, ifp, doCreate=doCreate)
  N.update(new_attrs, delete_missing=True)

def editNodes(nodedb, nodes, attrs, editor=None, doCreate=False):
  ''' Edit multiple nodes interactively using the horizontal dump format.
  '''
  if nodedb is None:
    nodedb = nodes[0].nodedb
  if editor is None:
    editor = os.environ.get('EDITOR', 'vi')
  with tempfile.NamedTemporaryFile(suffix='.csv') as T:
    with Pfx(T.name):
      nodedb.dump_csv_wide(T, nodes=nodes, attrs=attrs)
      qname = cs.sh.quotestr(T.name)
      os.system("%s %s" % (editor, qname))
      import_csv_wide(nodedb, T.name, doAppend=False)

def assign(N, assignment, doCreate=False):
  ''' Take a string of the form ATTR=values and apply it.
  '''
  with Pfx(assignment):
    attr, commatxt = assignment.split('=', 1)
    values = list(commatext_to_values(commatxt, N.nodedb, doCreate=doCreate))
    if attr.islower():
      # "computed" convenience attributes
      N.set_lcattr(attr, values)
    else:
      k, plural = parse_UC_sAttr(attr)
      assert k, "invalid attribute name \"%s\"" % (attr, )
      N[k] = values

def get_commatext(text, pos=0):
  ''' Fetch a "word" from the supplied `text`, starting at `pos` (default 0).
      Words consist of consecutive sequences of double quoted strings
      or nonwhitespace.
      Returns the end bound of the word.
      Raises ValueError for a malformed double quoted string.
  '''
  opos = pos
  while len(text) > pos and not text[pos].isspace():
    if text[pos] == '"':
      m = re_JSON_STRING.match(text, pos)
      if not m:
        raise ValueError, "invalid quoted string at: %s" % (text[pos:],)
      pos = m.end()
    else:
      pos += 1
  return pos

def get_commatexts(line, pos=0):
  ''' Parse a line containing commatexts as defined by get_commatext() above.
      Yield the words on the line.
  '''
  line = line[pos:].lstrip()
  while len(line) > 0:
    end = get_commatext(line)
    if end == 0:
      break
    yield line[:end]
    line = line[end:].lstrip()

def commatext_to_tokens(text):
  ''' Parse a comma separated list of human friendly value tokens,
      yield the token strings.
      Tokens are either quoted strings or chunks of non-comma non-whitespace.
  '''
  with Pfx("commatext_to_tokens(%s)" % (text,)):
    while len(text) > 0:
      if text[0].isspace():
        text = text.lstrip()
        continue
      if text[0] == ',':
        text = text[1:]
        continue
      # "foo"
      m = re_JSON_STRING.match(text)
      if m:
        qstring = m.group()
        yield qstring
        text = text[len(qstring):]
      else:
        if ',' in text:
          word, text = text.split(',', 1)
        else:
          word, text = text, ''
        word = word.strip()
        yield word
  
def commatext_to_values(text, nodedb, doCreate=False):
  ''' Parse a comma separated list of human friendly values and yield values.
     `nodedb` os the context nodedb, or None.
     `N` is the context node, or None.
     `attr` is the context attribute name, or None.
     If `nodedb` is None it defaults to `N.nodedb` (if `N` is not None).
     If `doCreate` is true, nonexistent nodes will be created as needed.
     Return the list of values.
  '''
  for token in commatext_to_tokens(text):
    yield fromtoken(token, nodedb, doCreate=doCreate)

def fromtoken(token, nodedb, doCreate=False):
  ''' Extract a token from the start of a string.
      This is the fallback method used by NodeDB.fromtoken() if none of the Node
      or NodeDB specific formats match, or in non-Node contexts.
      Return the parsed value and the remaining text or raise ValueError.
  '''
  # "foo"
  m = re_JSON_STRING.match(token)
  if m and m.group() == token:
    return json.loads(m.group())

  # int
  m = re_INT.match(token)
  if m and m.group() == token:
    return int(m.group())

  # http://foo/bah etc
  m = re_BAREURL.match(token)
  if m and m.group() == token:
    return token

  try:
    t, name = nodekey(token)
  except ValueError:
    warning("can't infer Node from \"%s\", returning string" % (token,))
    return token

  N = nodedb.get( (t, name), doCreate=doCreate )
  if N is None:
    raise ValueError, "no Node with key (%s, %s)" % (t, name)

  return N

def totoken(value):
  ''' Return "printable" form of an attribute value.
      This is intended to be human friendly but reversible.
  '''
  if type(value) in StringTypes:
    ##m = re_BAREWORD.match(value)
    ##if m is not None and m.end() == len(value):
    ##  return value
    m = re_BAREURL.match(value)
    if m is not None and m.end() == len(value):
      return value
    return '"'+value.replace('"', r'\"')+'"'

  if type(value) is int:
    return str(value)

  raise ValueError, "can't turn into token: %s" % (`value`,)

if __name__ == '__main__':
  import cs.nodedb.text_tests
  cs.nodedb.text_tests.selftest(sys.argv)
