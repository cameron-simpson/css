#!/usr/bin/python
#
# Routines for working with Nodes and their textual representation.
#       - Cameron Simpson <cs@zip.com.au>
#

from contextlib import closing
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

def attrValueText(N, attr, value):
  ''' Return "printable" form of an attribute value.
      This is intended for use in "pretty" reports such as web pages.
  '''
  pvalue = str(value)
  if isinstance(value, Node):
    if attr == "SUB"+N.type and value.type == N.type:
      pvalue = value.name
    elif attr == value.type:
      pvalue = value.name
  elif type(value) in StringTypes:
    m = re_BAREURL.match(value)
    if m is not None and m.end() == len(value):
      pvalue = value
    else:
      if value.isdigit() and str(int(value)) == value:
        pvalue = int(value)
      else:
        pvalue = json.dumps(value)
  return pvalue

def dumpNodeAttrs(N, ofp):
  # TODO: use attrValueText() somehow
  ofp.write("# %s\n" % (N,))
  attrnames = N.keys()
  attrnames.sort()
  old_pattr = None
  for attr in attrnames:
    k, plural = parseUC_sAttr(attr)
    if not k:
      continue
    pattr = k
    values = N[k]
    if not values:
      continue
    for value in values:
      pvalue = str(value)
      if isinstance(value, Node):
        if attr == "SUB"+N.type and value.type == N.type:
          pvalue = value.name
        elif attr == value.type:
          pvalue = value.name
      elif type(value) in StringTypes:
        m = re_BAREURL.match(value)
        if m is not None and m.end() == len(value):
          pvalue = value
        else:
          if value.isdigit() and str(int(value)) == value:
            pvalue = int(value)
          else:
            pvalue = json.dumps(value)
      if old_pattr is not None and old_pattr == pattr:
        pattr = ''
      else:
        old_pattr = pattr
      ofp.write('%-15s %s\n' % (pattr, pvalue))

def loadNodeAttrs(N, ifp, createSubNodes=False):
  ''' Read input of the form:
        ATTR    value
                value
                ...
      and return a mapping of ATTR->[values...].
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
      value = line.lstrip()
    else:
      # split into attribute name and value
      attr, value = line.split(None, 1)
      k, plural = parseUC_sAttr(attr)
      assert k, "%s: invalid attribute name \"%s\"" % (str(ifp), attr)
      ks = k+'s'
      assert not k.endswith('_ID'), \
             "%s: invalid attribute name \"%s\" - FOO_ID forbidden" \
               % (str(ifp), attr)
      prev_attr = attr
    new_attrs \
      .setdefault(k, []) \
      .extend(tokenise(N, k, value, createSubNodes=createSubNodes))
  return new_attrs

def editNode(N, editor=None, createSubNodes=False):
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
    new_attrs = loadNodeAttrs(N, ifp, createSubNodes=createSubNodes)
  T.close()
  if os.path.exists(T.name):
    os.remove(T.name)
  ##print >>sys.stderr, "new_attrs:"
  ##for attr in sorted(new_attrs.keys()):
  ##  print >>sys.stderr, "%s %s" % (attr, new_attrs[attr])
  N.update(new_attrs, delete_missing=True)

def assign(N, assignment, createSubNodes=False):
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
      values = tokenise(N, k, valuetxt, createSubNodes=createSubNodes)
      N[k+'s']=values

def tokenise(N, attr, valuetxt, createSubNodes=False):
  ''' Parse a comma separated list of Node attribute values.
  '''
  values = []
  valuetxt = valuetxt.strip()
  while len(valuetxt) > 0:
    value, valuetxt = N.gettoken(attr, valuetxt, createSubNodes=createSubNodes)
    values.append(value)
    valuetxt = valuetxt.lstrip()
    assert len(valuetxt) == 0 or valuetxt.startswith(','), \
      "expected comma, got \"%s\"" % (valuetxt,)
    if valuetxt.startswith(','):
      valuetxt = valuetxt[1:].lstrip()
    assert len(valuetxt) == 0 or not valuetxt.startswith(','), \
      "unexpected second comma at \"%s\"" % (valuetxt,)
  return values
