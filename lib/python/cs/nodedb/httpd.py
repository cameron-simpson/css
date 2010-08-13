#!/usr/bin/python
#
# Basic web browser interface to a NodeDB.
#       - Cameron Simpson <cs@zip.com.au>
#

import urllib
from BaseHTTPServer import HTTPServer, BaseHTTPRequestHandler
import csv
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
  ''' 
  '''
  if label is None:
    if context is None:
      label = str(N)
    else:
      label = attrValueText(context[0], context[1], N)
  return str(N), label

class NodeDBWebServer(HTTPServer):
  ''' Browsable interface to database.
  '''

  def __init__(self, servaddr, prefix, nodedb):
    HTTPServer.__init__(self, servaddr, NodeDB_RQHandler)
    if not prefix.endswith('/'):
      prefix += '/'
    self.prefix = prefix
    self.nodedb = nodedb
    nodedb.readonly = True

class NodeDB_RQHandler(BaseHTTPRequestHandler):

  def __nodeLink(self, N, label=None, context=None, ext=''):
    ''' Return an 'A' token linking to the specified Node `N`.
    '''
    rhref, label = node_href(N, label=label, context=context)
    return ['A',
            {'HREF': "%s%s%s" % (self.server.prefix, rhref, ext)},
            label]

  def _puttok(self, *tokens):
    ''' Write the supplied `tokens` to the request output.
    '''
    for tok in tokens:
      cs.html.puttok(self.wfile, tok)

  def do_GET(self):
    ''' Handle a GET request.
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
    ext = None
    dotpos = top.rfind('.')
    if dotpos > 0:
      key = top[:dotpos]
      ext = top[dotpos+1:]
    else:
      key = top

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

  def _overview(self):
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

  def _table_of_nodes(self, nodes, format):
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
