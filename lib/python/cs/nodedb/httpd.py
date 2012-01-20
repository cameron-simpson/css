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
from .html import TABLE_from_Node, TABLE_from_Nodes_wide, by_type_then_name

def by_name(a, b):
  ''' Compare to objects by their .name attributes.
  '''
  return cmp(a.name, b.name)

def node_href(N, label=None, node=None, attr=None):
  ''' Return (nodespec, label) given the Node `N`, an optional `label`
      and an optional context Node and attribute name.
  '''
  if label is None:
    if not node:
      label = str(N)
    else:
      label = node.nodedb.totoken(N, node=node, attr=attr)
  return str(N), label

class CherryPyNode(object):

  def __init__(self, nodedb, basepath='/'):
    self.nodedb = nodedb
    self.basepath = basepath

  def _start(self):
    self._tokens = [ ['SCRIPT', {'LANGUAGE': 'javascript',
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

class NodeDBView(CherryPyNode):

  NODELIST_LEADATTRS = [ 'TYPE', 'NAME', 'COMMENT' ]

  def __init__(self, nodedb, basepath, readwrite=False):
    CherryPyNode.__init__(self, nodedb, basepath)
    self.node = NodeDBView._Nodes(self)
    self.readwrite = readwrite
    self.nodelist_leadattrs = NodeDBView.NODELIST_LEADATTRS

  def _nodeLink(self, N, label=None, context=None, view=''):
    ''' Return an 'A' token linking to the specified Node `N`.
    '''
    rhref, label = node_href(N, label=label, node=context)
    return ['A',
            {'HREF': "%snode/%s/%s" % (self.basepath, rhref, view)},
            label]

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
        self._tokens.append(self._nodeLink(N, label=N.name))
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
          export_csv_wide(fp, self.nodedb.type(k))
          out = fp.getvalue()
          fp.close()
          return out
        # TYPEs.html
        if content_type == 'text/html':
          self._start()
          self._tokens.append(TABLE_from_Nodes_wide(sorted(self.nodedb.type(k), by_type_then_name), leadattrs=self.nodelist_leadattrs))
          return self._flushtokens()
        raise cherrypy.HTTPError(501, basename)

    raise cherrypy.HTTPError(404, basename)

  class _Nodes(CherryPyNode):
    ''' View of individual Nodes, at /nodes/.
        /node/nodespec/           Nice report or basic table HTML.
        /node/nodespec/basic.html Basic table HTML.
        /node/nodespec/csv        CSV as text/csv.
        /node/nodespec/txt        CSV as plain text.
    '''

    def __init__(self, top):
      CherryPyNode.__init__(self, top.basepath)
      self.top = top

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
          return tok2s( *N.report() )
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
          self._tokens.append(self.top._nodeLink(P))
        self._tokens.append(".")
        self._tokens.extend( (['BR'], "\n") )

      self._tokens.append( TABLE_from_Node(N) )
      self._tokens.append("\n")

      return self._flushtokens()

def serve(nodedb, host, port, basepath='/', readwrite=False, DBView=None):
  ''' Dispatch an HTTP server serving the content of `nodedb`.
  '''
  if type(port) in StringTypes:
    port = int(port)
  if DBView is None:
    DBView = NodeDBView
  V = DBView(nodedb, basepath, readwrite=readwrite)
  S = cherrypy.server
  S.socket_host = host
  S.socket_port = port
  cherrypy.quickstart(V)
