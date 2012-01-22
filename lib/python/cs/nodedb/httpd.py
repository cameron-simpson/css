#!/usr/bin/python
#
# Basic web browser interface to a NodeDB.
#       - Cameron Simpson <cs@zip.com.au>
#

import urllib
from BaseHTTPServer import HTTPServer, BaseHTTPRequestHandler
import csv
import os.path
import socket
import sys
from types import StringTypes
import cherrypy
from cStringIO import StringIO
from cs.logutils import info, warning, error, debug, D
from cs.html import tok2s
from cs.mappings import parseUC_sAttr
from cs.nodedb import Node
from .export import export_csv_wide
from .html import TABLE_from_Node, TABLE_from_Nodes_wide, _noderef
from .node import by_name, by_type_then_name

class CherryPyNode(object):

  def __init__(self, top):
    if not top.basepath.endswith('/'):
      raise ValueError("top.basepath does not end in a slash: %s" % (top.basepath,))
    self.top = top
    self.nodedb = top.nodedb
    self.basepath = top.basepath
    self.nodes_prefix = "%snodes/" % (self.basepath,)

  def _start(self):
    self._tokens = [ ['BASE', {'HREF': self.basepath}],
                     ['SCRIPT', {'LANGUAGE': 'javascript',
                                 'SRC': os.path.join(self.basepath, 'lib-css.js'),
                                }],
                     ['SCRIPT', {'LANGUAGE': 'javascript',
                                 'SRC': os.path.join(self.basepath, 'lib.js'),
                                }]
                   ]

  def _flushtokens(self):
    ''' Transcribe the pending HTML tokens to text,
        flush the pending token list, return the text.
    '''
    html = tok2s(*self._tokens)
    self._tokens = []
    return html

  def noderef(self, N, label=None, ext=None):
    return _noderef(N, prefix=self.nodes_prefix, label=label, ext=ext)

class NodeDBView(CherryPyNode):

  NODELIST_LEADATTRS = [ 'TYPE', 'NAME', 'COMMENT' ]

  def __init__(self, nodedb, basepath, readwrite=False):
    if not basepath.endswith('/'):
      raise ValueError("basepath must end with a slash, got: %s" % (basepath,))
    self.basepath = basepath
    self.nodedb = nodedb
    CherryPyNode.__init__(self, self)
    self.readwrite = readwrite
    self.nodelist_leadattrs = NodeDBView.NODELIST_LEADATTRS

    self.nodes = NodesView(self)

  @cherrypy.expose
  def index(self):
    ''' NodeDB overview partitioned by TYPE.
    '''
    self._start()
    nodedb = self.nodedb
    self._tokens.append(['H1', 'Database Top Level'])
    nodetypes = sorted(nodedb.types)
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
                            ['A', {'HREF': nodetype+"s.txt"}, "CSV as text"], ", ",
                            ['A', {'HREF': nodetype+"s.html"}, "HTML"],
                            ")"]) )
      self._tokens.append("\n")
      nodes = nodedb.type(nodetype)
      nodes=list(nodes)
      nodes.sort(by_name)
      first = True
      for N in nodes:
        if not first:
          self._tokens.append(", ")
        self._tokens.append(N.html(prefix=self.nodes_prefix))
        first=False
      self._tokens.append("\n")
    return self._flushtokens()

  @cherrypy.expose
  def default(self, basename):
    # lib.js, lib-css.js
    if basename in ("lib.js", "lib-css.js"):
      cherrypy.response.headers['content-type'] = 'text/javascript'
      with open(os.path.join(os.path.dirname(__file__), basename)) as jsfp:
        js = jsfp.read()
      return js

    if '.' in basename:
      # TYPEs.{csv,txt,html}
      prefix, suffix = basename.rsplit('.', 1)
      if suffix == 'csv':
        content_type = 'text/csv'
      elif suffix == 'txt':
        content_type = 'text/plain'
      elif suffix == 'html':
        content_type = 'text/html'
      else:
        content_type = None
      k, plural = parseUC_sAttr(prefix)
      if k is not None and plural:
        # TYPEs.{txt,csv}
        if content_type in ('text/csv', 'text/plain'):
          fp = StringIO()
          export_csv_wide(fp, sorted(self.nodedb.type(k), by_name))
          out = fp.getvalue()
          fp.close()
          return out
        # TYPEs.html
        if content_type == 'text/html':
          self._start()
          self._tokens.append(TABLE_from_Nodes_wide(sorted(self.nodedb.type(k), by_type_then_name),
                                                    self,
                                                    leadattrs=self.nodelist_leadattrs))
          return self._flushtokens()
        raise cherrypy.HTTPError(501, basename)

    raise cherrypy.HTTPError(404, basename)

class NodesView(CherryPyNode):
  ''' View of individual Nodes, at /nodes/.
      /nodes/nodespec/           Nice report or basic table HTML.
      /nodes/nodespec/basic.html Basic table HTML.
      /nodes/nodespec/csv        CSV as text/csv.
      /nodes/nodespec/txt        CSV as plain text.
  '''

  def __init__(self, top):
    CherryPyNode.__init__(self, top)

  @cherrypy.expose
  def default(self, spec, *subpath):
    if subpath:
      subpath = list(subpath)
    view = ''
    if subpath:
      view = subpath.pop(0)
    try:
      N = self.top.nodedb[spec]
    except KeyError, e:
      raise cherrypy.HTTPError(404, "%s: %s" % (spec, e))
    if view == '':
      if hasattr(N, 'report'):
        return tok2s( *N.report(self) )
      return self._basic_html_tokens(N)
    if view == 'basic.html':
      return self._basic_html_tokens(N)
    if view == 'csv':
      return self._csv_dump(N, 'text/csv')
    if view == 'txt':
      return self._csv_dump(N, 'text/plain')
    raise cherrypy.HTTPError(404, "%s: unsupported view: %s" % (spec, view))

  def _csv_dump(self, N, content_type):
    cherrypy.response.headers['content-type'] = content_type
    fp = StringIO()
    N.nodedb.dump(fp, fmt='csv', nodes=(N,))
    out = fp.getvalue()
    fp.close()
    return out

  def _basic_html_tokens(self, N):
    self._start()

    heading = ['H1', str(N)]
    alts = []
    if hasattr(N, 'report'):
      alts.append(N.html(label="pretty report"))
    if hasattr(N, 'nagios_cfg'):
      alts.append(N.html(label="nagios.cfg", ext="/nagios.cfg"))
    if alts:
      sep = " ("
      for alt in alts:
        heading.append(sep)
        heading.append(alt)
        sep = ", "
      heading.append(")")
    self._tokens.append(heading)

    # locate parent/referring Nodes
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
        self._tokens.append(self.noderef(P))
      self._tokens.append(".")
      self._tokens.extend( (['BR'], "\n") )

    self._tokens.append( TABLE_from_Node(N, self) )
    self._tokens.append("\n")

    return self._flushtokens()

def serve(nodedb, host, port, basepath='/db/', readwrite=False, DBView=None):
  ''' Dispatch an HTTP server serving the content of `nodedb`.
  '''
  if type(port) in StringTypes:
    port = int(port)
  if DBView is None:
    DBView = NodeDBView
  if not basepath.endswith('/'):
    raise ValueError("basepath should end in a slash, got: %s" % (basepath,))
  V = DBView(nodedb, basepath, readwrite=readwrite)
  S = cherrypy.server
  S.socket_host = host
  S.socket_port = port
  cherrypy.quickstart(V, script_name=basepath[:-1])
