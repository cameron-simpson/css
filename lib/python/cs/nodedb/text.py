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
from cs.logutils import Pfx, error, info
from cs.mappings import parseUC_sAttr
import cs.sh
from .node import re_BAREURL, Node

## NOT USED ? ## # regexp to match name(, name)*
## NOT USED ? ## re_NAMELIST = re.compile(r'([a-z][a-z0-9]+)(\s*,\s*([a-z][a-z0-9]+))*')
## NOT USED ? ## # regexp to do comma splits
## NOT USED ? ## re_COMMASEP = re.compile(r'\s*,\s*')

# JSON string expression, lenient
re_STRING = re.compile(r'"([^"\\]|\\.)*"')
# JSON simple integer
re_INT = re.compile(r'-?[0-9]+')

def attr_value_to_text(N, attr, value, common=None):
  ''' Return "printable" form of an attribute value.
      This is intended to be human friendly but reversible.
  '''
  if common is None:
    common = json.dumps

  pvalue = None
  if isinstance(value, Node):
    # Node representation:
    # If value.type == FOO, Node is of type FOO and attr is SUBFOO,
    #   just write the value Node name
    if attr == "SUB"+N.type and value.type == N.type:
      pvalue = value.name
    # If value.type == FOO and attr == FOO,
    #   just write the value Node name
    elif attr == value.type:
      pvalue = value.name
  elif type(value) in StringTypes:
    m = re_BAREURL.match(value)
    if m is not None and m.end() == len(value):
      pvalue = value
    else:
      if value.isdigit() and str(int(value)) == value:
        pvalue = str(int(value))

  if pvalue is None:
    pvalue = common(value)

  return pvalue

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
                    attr_value_to_text(N, attr, value) )
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
  if sys.hexversion < 0x02060000:
    T = tempfile.NamedTemporaryFile()
  else:
    T = tempfile.NamedTemporaryFile(delete=False)
  dumpNodeAttrs(N, T)
  T.flush()
  qname = cs.sh.quotestr(T.name)
  os.system("%s %s" % (editor, qname))
  with closing(open(T.name)) as ifp:
    new_attrs = loadNodeAttrs(N, ifp, doCreate=doCreate)
  T.close()
  if os.path.exists(T.name):
    os.remove(T.name)
  N.update(new_attrs, delete_missing=True)

def editNodes(nodedb, nodes, attrs, editor=None, doCreate=False):
  ''' Edit multiple nodes interactively using the horizontal dump format.
  '''
  if nodedb is None:
    nodedb = nodes[0].nodedb
  if editor is None:
    editor = os.environ.get('EDITOR', 'vi')
  if sys.hexversion < 0x02060000:
    T = tempfile.NamedTemporaryFile()
  else:
    T = tempfile.NamedTemporaryFile(delete=False)
  nodes, attrs = dump_horizontal(T, nodes=nodes, attrs=attrs)
  T.flush()
  qname = cs.sh.quotestr(T.name)
  os.system("%s %s" % (editor, qname))
  with closing(open(T.name)) as ifp:
    new_nodes = list(load_horizontal(ifp, nodedb, doCreate=doCreate))
  T.close()
  if os.path.exists(T.name):
    os.remove(T.name)
  # map from (name, type) to old_node
  nodeMap = dict( [ ((N.name, N.type), N) for N in nodes ] )
  for N, A in new_nodes:
    for attr, values in A.items():
      print >>sys.stderr, "%s.%s = %s" % (N, attr, values)
      N[attr] = values

def dump_horizontal(fp, nodes, attrs=None):
  ''' Write Nodes to the file `fp` in the horizontal format.
      This is intended for easy editing if specific nodes' specific attributes.
  '''
  nodes = list(nodes)
  if attrs is None:
    attrs = set()
    for N in nodes:
      attrs.update(N.keys())
    attrs = sorted(attrs)
  else:
    attrs = list(attrs)
  w = csv.writer(fp)
  w.writerow( ['TYPE', 'NAME'] + attrs )
  for N in nodes:
    w.writerow( [ N.type, N.name ]
              + [ ','.join( attr_value_to_text(N, attr, v)
                            for v in N.get(attr, ())
                          ) for attr in attrs ] )
  fp.flush()
  return nodes, attrs

def load_horizontal(fp, nodedb, doCreate=False):
  ''' Read horizontal CSV data from `fp` for `nodedb`.
      Return sequence of:
        Node, dict(attr => values)
      where the dict contains new attribute values not yet applied to Node.
  '''
  r = csv.reader(fp)
  first = True
  for row in r:
    print >>sys.stderr, "row =", `row`
    if first:
      assert row[0] == 'TYPE'
      assert row[1] == 'NAME'
      attrs = row[2:]
      first = False
    else:
      t = row.pop(0)
      name = row.pop(0)
      N = nodedb.get( (t, name), None, doCreate=doCreate)
      A = {}
      for attr, valuetxt in zip(attrs, row):
        A[attr] = text_to_values(valuetxt, N, attr, doCreate=doCreate)
      yield N, A

def assign(N, assignment, doCreate=False):
  ''' Take a string of the form ATTR=values and apply it.
  '''
  with Pfx(assignment):
    attr, valuetxt = assignment.split('=', 1)
    if attr.islower():
      # "computed" convenience attributes
      N.set_lcattr(attr, valuetxt)
    else:
      k, plural = parse_UC_sAttr(attr)
      assert k, "invalid attribute name \"%s\"" % (attr, )
      values = tokenise(N, k, valuetxt, doCreate=doCreate)
      N[k]=values

def text_to_values(valuetxt, N, attr, nodedb=None, doCreate=False):
  ''' Parse a comma separated list of human friendly values.
     `nodedb` os the context nodedb, or None.
     `N` is the context node, or None.
     `attr` is the context attribute name, or None.
     If `nodedb` is None it defaults to `N.nodedb` (if `N` is not None).
     If `doCreate` is true, nonexistent nodes will be created as needed.
     Return the list of values.
  '''
  values = []
  valuetxt = valuetxt.strip()
  while len(valuetxt) > 0:
    if N:
      value, valuetxt = N.gettoken(valuetxt,
                                   attr,
                                   doCreate=doCreate)
    elif nodedb:
      value, valuetxt = nodedb.gettoken(valuetxt, doCreate=doCreate)
    else:
      value, valuetxt = gettoken(valuetxt, doCreate=doCreate)
    values.append(value)
    valuetxt = valuetxt.lstrip()
    assert len(valuetxt) == 0 or valuetxt.startswith(','), \
      "expected comma, got \"%s\"" % (valuetxt,)
    if valuetxt.startswith(','):
      valuetxt = valuetxt[1:].lstrip()
    assert len(valuetxt) == 0 or not valuetxt.startswith(','), \
      "unexpected second comma at \"%s\"" % (valuetxt,)
  return values

def gettoken(valuetxt):
  ''' Extract a token from the start of a string.
      This is the fallback method used by Node.gettoken() if none of the Node
      specific formats match, or in non-Node contexts.
      Return the parsed value and the remaining text or raise ValueError.
  '''
  # "foo"
  m = re_STRING.match(valuetxt)
  if m:
    value = json.loads(m.group())
    return value, valuetxt[m.end():]

  # int
  m = re_INT.match(valuetxt)
  if m:
    value = int(m.group())
    return value, valuetxt[m.end():]

  # http://foo/bah etc
  m = re_BAREURL.match(valuetxt)
  if m:
    value = m.group()
    return value, valuetxt[m.end():]

  raise ValueError, "not a JSON string or an int or a BAREURL: %s" % (valuetxt,)
