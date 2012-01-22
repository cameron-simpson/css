#!/usr/bin/python
#
# HTML transcription, pulled and adapted from cs.nodedb.httpd.
#       - Cameron Simpson <cs@zip.com.au> 15jan2012
#

import sys
from types import StringTypes
import cherrypy
from cStringIO import StringIO
import cs.html
from cs.mappings import parseUC_sAttr
from cs.nodedb import Node
from cs.nodedb.export import export_csv_wide
from cs.logutils import info, warning, error, debug, D

# HTML token convenience functions
def SPAN(*elements):
  span = ['SPAN']
  span.extend(elements)
  return span

def _noderef(N, prefix, label=None, ext=None):
  ''' Return an anchor HTML token referring to a Node view.
      N: the Node to which to link.
      prefix: URL base prefix to prepend.
      label: visible text for the link, default N.name.
      ext: suffix for the link URL, default '.html'.
  '''
  if not prefix.endswith('/'):
    raise ValueError("noderef(N=%s,...): prefix does not end in a slash: %s" % (N, prefix))
  if prefix == '/': raise ValueError("bad prefix: %s" % (prefix,))
  if label is None:
    label = N.name
  if ext is None:
    ext = '/'
  return ['A', {'HREF': '%s%s:%s%s' % (prefix, N.type, N.name, ext)}, label]

def TD(*elements):
  return ['TD', {'align': 'left', 'valign': 'top'}] + list(elements)

def TH(*elements):
  return ['TH', {'align': 'left', 'valign': 'top'}] + list(elements)

def TR(*TDs):
  tr = ['TR']
  for td in TDs:
    tr.append(TD(td))
  return tr

def TABLE(*rows):
  trows = []
  for row in rows:
    for value in row:
      vlabel = N.nodedb.totoken(value, node=N, attr=attr)
      if isinstance(value, Node):
        vtag = self.top._nodeLink(value, label=vlabel, context=(N, attr))
      else:
        vtag = vlabel
      if vtags:
        vtags.append(", ")
      vtags.append(vtag)
    trows.append(TR(attr, SPAN(*vtags)))
  return ['TABLE', {'BORDER': 1}] + trows

def tag_from_value(value, CP):
  ''' Convert the supplied `value` to an HTML token using the CherrypyNode
      object `CP` for context as needed.
  '''
  if isinstance(value, Node):
    tag = value.html(prefix=CP.nodes_prefix)
  elif type(value) in StringTypes:
    lines = [ line.rstrip() for line in value.rstrip().split('\n') ]
    taglist = []
    for line in lines:
      linetags = []
      for word in line.split():
        if linetags:
          linetags.append(['&nbsp;'])
        linetags.append(word)
      if taglist:
        taglist.append(['BR'])
      taglist.extend(linetags)
    tag = SPAN(*taglist)
  elif isinstance(value, list):
    taglist = []
    for v in value:
      if taglist:
        taglist.append(['BR'])
      taglist.append(tag_from_value(v, CP))
    tag = SPAN(*taglist)
  else:
    tag = str(value)
  return tag

def TABLE_from_rows(rows, CP):
  trows = [ TR( *[ TD(tag_from_value(v, CP)) for v in row ] )
            for row in rows
          ]
  return ['TABLE', {'BORDER': 1}] + trows

def TABLE_from_Node(node, CP):
  return TABLE_from_rows( [ [attr, node[attr]] for attr in sorted(node.keys()) ], CP )

def rows_from_Node(node):
  for attr in sorted(node.keys()):
    yield attr, node[attr]

def TABLE_from_Nodes_wide(nodes, CP, leadattrs=None):
  return TABLE_from_rows( rows_from_Nodes_wide(nodes, leadattrs=leadattrs), CP )

def rows_from_Nodes_wide(nodes, leadattrs=None):
  ''' A generator to yield lists of values for table rows.
  '''
  if type(nodes) is not list:
    nodes = list(nodes)
  if leadattrs is None:
    leadattrs = ('TYPE', 'NAME')

  # compute list of attributes
  attrs = set()
  for N in nodes:
    attrs.update(N.keys())
  leadattrs = [ attr for attr in leadattrs if attr in attrs ]
  attrs.difference_update(leadattrs)
  attrs = list(leadattrs) + sorted(attrs)

  yield attrs
  for N in nodes:
    yield [ ( N.type if attr == 'TYPE'
              else N if attr == 'NAME'
              else N.get(attr, ())
            ) for attr in attrs
          ]
