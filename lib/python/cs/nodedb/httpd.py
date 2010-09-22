#,!/usr/bin/python
#
# Basic web browser interface to a NodeDB.
#       - Cameron Simpson <cs@zip.com.au>
#

import urllib
from BaseHTTPServer import HTTPServer, BaseHTTPRequestHandler
import csv
import socket
import sys
from types import StringTypes
import cherrypy
from cStringIO import StringIO
import cs.html
from cs.mappings import parseUC_sAttr
from cs.nodedb import Node
from cs.nodedb.text import attrValueText
from cs.logutils import info, warn, error, debug

# HTML token convenience functions
def TD(*elements):
  return ['TD', {'align': 'left', 'valign': 'top'}] + list(elements)
def TH(*elements):
  return ['TH', {'align': 'left', 'valign': 'top'}] + list(elements)
def TR(*TDs):
  tr = ['TR']
  for td in TDs:
    tr.append(TD(td))
  return tr
def SPAN(*elements):
  span = ['SPAN']
  span.extend(elements)
  return span
def A(N, label=None, ext='.html', prefix=None):
  ''' Return an anchor HTML token.
      N: the Node to which to link.
      label: visible text for the link, default N.name.
      ext: suffix for the link URL, default '.html'.
      prefix: URL base prefix to prepend.
  '''
  if label is None:
    label = N.name
  rhref, label = N.relhref(label=label)
  if prefix is not None:
    rhref = prefix + rhref
  return ['A', {'HREF': rhref+ext}, label]

def by_name(a, b):
  ''' Compare to objects by their .name attributes.
  '''
  return cmp(a.name, b.name)

def node_href(N, label=None, context=None):
  ''' Return (nodespec, label) given the Node `N`, an optional `label`
      and an optional context (cNode, cAttr) specifying a context Node and
      Node attribute name.
  '''
  if label is None:
    if context is None:
      label = str(N)
    else:
      label = attrValueText(context[0], context[1], N)
  return str(N), label

class CherryPyNode(object):

  def __init__(self, nodedb, basepath='/'):
    self.nodedb = nodedb
    self.basepath = basepath

  def _start(self):
    self._tokens = []

  def _flushtokens(self):
    ''' Transcribe the pending HTML tokens to text,
        flush the pending token list, return the text.
    '''
    out = StringIO()
    for tok in self._tokens:
      cs.html.puttok(out, tok)
    html = out.getvalue()
    out.close()
    self._tokens = []
    return html

class NodeDBView(CherryPyNode):

  def __init__(self, nodedb, basepath):
    CherryPyNode.__init__(self, nodedb, basepath)
    self.nodes = NodeDBView._Nodes(self)

  def _nodeLink(self, N, label=None, context=None, view=''):
    ''' Return an 'A' token linking to the specified Node `N`.
    '''
    rhref, label = node_href(N, label=label, context=context)
    return ['A',
            {'HREF': "%snodes/%s/%s" % (self.basepath, rhref, view)},
            label]

  @cherrypy.expose
  def index(self):
    ''' NodeDB overview partitioned by TYPE.
    '''
    self._start()
    nodedb = self.nodedb
    self._tokens.append(['H1', 'Database Top Level'])
    nodetypes = nodedb.types()
    nodetypes.sort()
    self._tokens.append("Types:")
    sep = " "
    for nodetype in nodetypes:
      self._tokens.extend( (sep, ['A', {'HREF': "#type-"+nodetype}, nodetype]) )
      sep = ", "
    self._tokens.append(['BR'])
    self._tokens.append("\n")
    for nodetype in nodetypes:
      self._tokens.append( (['H2', ['A', {'NAME': "type-"+nodetype}, "Type "+nodetype],
                            " (",
                            ['A', {'HREF': nodetype+"s.csv"}, "CSV"], ", ",
                            ['A', {'HREF': nodetype+"s.txt"}, "CSV as text"],
                            ")"]) )
      self._tokens.append("\n")
      nodes = nodedb.nodesByType(nodetype)
      nodes=list(nodes)
      nodes.sort(by_name)
      first = True
      for N in nodes:
        if not first:
          self._tokens.append(", ")
        self._tokens.append(self._nodeLink(N, label=N.name))
        first=False
      self._tokens.append("\n")
    return self._flushtokens()

  class _Nodes(CherryPyNode):
    ''' View of individual Nodes.
        /nodes -> /node/
        /nodes/                  Nice report or basic table HTML.
        /nodes/basic.html        Basic table HTML.
        /nodes/csv               CSV as text/csv.
        /nodes/txt               CSV as plain text.
    '''

    def __init__(self, top):
      self.top = top

    @cherrypy.expose
    def default(self, spec, view=''):
      N = self.top.nodedb[spec]
      if view == '':
        if hasattr(N, 'report'):
          return N.report()
      return self._basic_html_tokens(N)

    def _basic_html_tokens(self, N):
      self._start()

      heading = ['H1', str(N)]
      alts = []
      if hasattr(N, 'report'):
        alts.append(self.top._nodeLink(N, label="pretty report"))
      if hasattr(N, 'nagios_cfg'):
        alts.append(self.top._nodeLink(N, label="nagios.cfg", view="nagios.cfg"))
      if alts:
        sep = " ("
        for alt in alts:
          heading.append(sep)
          heading.append(alt)
          sep = ", "
        heading.append(")")
      self._tokens.append(heading)

      parents = set( rN for rN, rAttr, rCount in N.references() )
      if parents:
        def bylabel(a, b): return cmp(str(a), str(b))
        parents = list(parents)
        parents.sort(bylabel)
        self._tokens.append("Attached to:")
        sep = " "
        for P in parents:
          self._tokens.append(sep)
          sep = ", "
          self._tokens.append(self.top._nodeLink(P))
        self._tokens.append(".")
        self._tokens.append(['BR'])

      rows = []
      table = ['TABLE', {"border": 1}]
      attrs = N.keys()
      attrs.sort()
      for attr in attrs:
        assert not attr.endswith('s'), "plural attr \"%s\"" % (attr,)
        values = N[attr]
        if not values:
          continue
        vtags = []
        for V in values:
          vlabel = attrValueText(N, attr, V)
          if isinstance(V, Node):
            vtag = self.top._nodeLink(V, label=vlabel, context=(N, attr))
          else:
            vtag = vlabel
          if vtags:
            vtags.append(", ")
          vtags.append(vtag)
        table.append(TR(attr, SPAN(*vtags)))
      self._tokens.append(table)

      self._tokens.append("\n")
      return self._flushtokens()

def serve(nodedb, host, port, basepath='/'):
  if type(port) in StringTypes:
    port = int(port)
  V = NodeDBView(nodedb, basepath)
  S = cherrypy.server
  S.socket_host = host
  S.socket_port = port
  cherrypy.tree.mount(V, script_name='/')
  S.start()

def OLDdo_GET(self):
    ''' Handle a GET request.
        The layout is as follows:
          /     DB overview.
          /types/TYPE[.ext]     Report on nodes of type TYPE.
          /nodes/node-ref/...   Report on node.
    '''
    srv = self.server
    nodedb = srv.nodedb
    prefix = srv.prefix
    path = urllib.unquote(self.path)
    if not path.startswith(prefix):
      self.send_error(503, "path does not start with prefix (\"%s\"): %s" % (prefix, path))
      return

    # strip prefix, get path components
    path = path[len(prefix):]
    isdir = self.path.endswith('/')
    parts = [ part for part in path.split('/') if len(part) > 0 ]

    # handle . and ..
    i = 0
    while i < len(parts):
      part = parts[i]
      if part == '.':
        del parts[i]
      elif part == '..':
        del parts[i]
        if i > 0:
          i -= 1
          del parts[i]
      else:
        i += 1

    if len(parts) == 0:
      self.send_response(200, "overview follows")
      self._overview()
      return

    top = parts.pop(0)
    warn("top=[%s]" % (top,))
    ext = ''
    dotpos = top.rfind('.')
    if dotpos > 0:
      key = top[:dotpos]
      ext = top[dotpos+1:]
    else:
      key = top

    if top == "types":
      if len(parts) == 0:
        self.send_response(400, "no TYPE specification")
        return
      typebase = parts.pop(0)
      if len(parts) > 0 or isdir:
        self.send_response(400, "extra parts after type")
      if '.' in typebase:
        typename, ext = typebase.split('.', 1)
      else:
        typename, ext = typebase
      type

      typespec = parts.pop(0)

    warn("1")
    if not parts and not isdir and ext:
      warn("2")
      k, plural = parseUC_sAttr(key)
      if k is not None and plural:
        warn("3")
        # TYPEs.{csv,txt,html} - dump of nodes of that type.
        if ext == 'csv':
          warn("3csv")
          self.send_response(200, "TYPE %s as CSV" % key)
          self.send_header('Content-Type', 'text/csv')
          self.end_headers()
          self._table_of_nodes(nodedb.nodesByType(k), 'csv')
          return
        if ext == 'txt':
          warn("3txt")
          self.send_response(200, "TYPE %s as CSV plain text" % key)
          self.send_header('Content-Type', 'text/plain')
          self.end_headers()
          self._table_of_nodes(nodedb.nodesByType(k), 'csv')
          return
        if ext == 'html':
          warn("3html")
          self.send_response(200, "TYPE %s as an HTML table" % (key,))
          self.send_header('Content-Type', 'text/html')
          self.end_headers()
          self._puttok( ['H1', "Nodes of type %s" % (k,)] )
          self._table_of_nodes(nodedb.nodesByType(k), 'html')
          return
        warn("3other")
        self.send_response(503, "unsupported TYPEs.ext \".%s\", expected .csv, .txt or .html" % (ext,))
        return

    warn("4")
    if top.endswith('.html'):
      key, ext = top.rsplit('.', 1)
      ext = '.' + ext
      N = nodedb[key]
      if hasattr(N, 'report'):
        # pretty HTML report
        self.send_response(200, "accepted")
        # headers - just HTML content
        self.send_header('Content-Type', 'text/html')
        self.end_headers()
        N.report(self.wfile, fmt='html', prefix=srv.prefix)
        return
      # otherwise use "raw mode"
    else:
      N = nodedb[top]

    if len(parts) == 0:
      # raw dump HTML report for node
      self.send_response(200, "accepted")
      # headers - just HTML content
      self.send_header('Content-Type', 'text/html')
      self.end_headers()
      heading = ['H1', str(N)]
      if hasattr(N, 'report'):
        heading.extend([" (", A(N, label="pretty report", prefix=prefix)])
        if hasattr(N, 'nagios_cfg'):
          heading.extend([ ", ",
                           A(N, label="nagios.cfg", ext="/nagios.cfg", prefix=prefix)])
        heading.append(")")
      self._puttok(heading)

      parents = set( rN for rN, rAttr, rCount in N.references() )
      if parents:
        def bylabel(a, b): return cmp(str(a), str(b))
        parents = list(parents)
        parents.sort(bylabel)
        self._puttok("Attached to:")
        sep = " "
        for P in parents:
          self._puttok(sep)
          sep = ", "
          self._puttok(self.__nodeLink(P, ext=ext))
        self._puttok(".")
        self._puttok(['BR'])

      rows = []
      table = ['TABLE', {"border": 1}]
      attrs = N.keys()
      attrs.sort()
      for attr in attrs:
        assert attr.endswith('s'), "non-plural attr \"%s\"" % (attr,)
        attr1 = attr[:-1]
        values = N[attr]
        if not values:
          continue
        vtags = []
        for V in values:
          vlabel = attrValueText(N, attr, V)
          if isinstance(V, Node):
            vtag = self.__nodeLink(V, label=vlabel, context=(N, attr), ext=ext)
          else:
            vtag = vlabel
          if vtags:
            vtags.append(", ")
          vtags.append(vtag)
        table.append(TR(attr1, SPAN(*vtags)))
      self._puttok(table)

      self.wfile.write("\n")
      return

    subpart = parts.pop(0)
    if subpart == 'nagios.cfg':
      if not hasattr(N, 'nagios_cfg'):
        self.send_error(503, "can't generate nagios config for %s" % (N,))
        return
      self.send_response(200, "accepted")
      # headers - just HTML content
      self.send_header('Content-Type', 'text/plain')
      self.end_headers()
      N.nagios_cfg(self.wfile)
      return

    self.send_error(503, "unhandled URL")

def OLD_overview(self):
    ''' NodeDB overview partitioned by TYPE.
    '''
    srv = self.server
    nodedb = srv.nodedb
    # headers - just HTML content
    self.send_header('Content-Type', 'text/html')
    self.end_headers()
    self._puttok(['H1', 'Database Top Level'])
    nodetypes = nodedb.types()
    nodetypes.sort()
    self._puttok("Types:")
    sep = " "
    for nodetype in nodetypes:
      self._puttok(sep, ['A', {'HREF': "#type-"+nodetype}, nodetype])
      sep = ", "
    self._puttok(['BR'])
    self.wfile.write("\n")
    for nodetype in nodetypes:
      self._puttok(['H2', ['A', {'NAME': "type-"+nodetype}, "Type "+nodetype],
                         " (",
                         ['A', {'HREF': nodetype+"s.csv"}, "CSV"], ", ",
                         ['A', {'HREF': nodetype+"s.txt"}, "CSV as text"],
                         ")"])
      self.wfile.write("\n")
      nodes = nodedb.nodesByType(nodetype)
      nodes=list(nodes)
      nodes.sort(by_name)
      first = True
      for N in nodes:
        if not first:
          self._puttok(", ")
        self._puttok(self.__nodeLink(N, ext=".html", label=N.name))
        first=False
      self.wfile.write("\n")
    return

def OLD_table_of_nodes(self, nodes, format):
    nodes=list(nodes)
    nodes.sort(by_name)
    fields = set()
    for N in nodes:
      fields.update(N.keys())
    fields = list(fields)
    fields.sort()

    if format == 'csv':
      csvw = csv.writer(self.wfile)
      csvw.writerow(['TYPE','NAME']+fields)
      for N in nodes:
        csvw.writerow([ N.type, N.name ]
                      + [ ",".join(str(attrValueText(N, F, A))
                                   for A in N[F]
                                  )
                          for F in fields
                        ]
                     )
      return

    if format == 'html':
      head_rows = [ TR( *( [ 'TYPE', 'NAME' ] + fields ) ) ]
      data_rows = []
      for N in nodes:
        celltext = [ N.type, N.name ]
        for F in fields:
          celltext.append( ", ".join( str(attrValueText(N, F, value))
                                      for value in N[F]
                                    ) )
        data_rows.append( TR( *celltext ) )
      self._puttok( ['TABLE', {'border': 1},
                      ['THEAD']+head_rows,
                      ['TBODY']+data_rows ] )
      return

    raise ValueError, "unsupported format: " + format
