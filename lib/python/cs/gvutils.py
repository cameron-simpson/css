#!/usr/bin/env python3

''' Graphviz utility functions.

    See also the [https://www.graphviz.org/documentation/](graphviz documentation)
    and particularly the [https://graphviz.org/doc/info/lang.html](DOT language specification)
    and the [https://www.graphviz.org/doc/info/command.html](`dot` command line tool).
'''

from base64 import b64encode
from dataclasses import dataclass, field
from os.path import exists as existspath
from subprocess import Popen, PIPE
import sys
from threading import Thread
from typing import Any, Mapping, Optional, Tuple
from urllib.parse import quote as urlquote

from cs.lex import cutprefix, cutsuffix, indent as indent_text, r

__version__ = '20230816-post'

DISTINFO = {
    'keywords': ["python3"],
    'classifiers': [
        "Programming Language :: Python",
        "Programming Language :: Python :: 3",
    ],
    'install_requires': [
        'cs.lex',
    ],
}

DOT_KEYWORDS = ('strict', 'graph', 'digraph', 'subgraph', 'node', 'edge')

def quote(s):
  ''' Quote a string for use in DOT syntax.
      This implementation passes non-keyword identifiers and sequences
      of decimal numerals through unchanged and double quotes other
      strings.
  '''
  if isinstance(s, (int, float)):
    return str(s)
  if ((s.isalnum() or s.replace('_', '').isalnum())
      and s.lower() not in DOT_KEYWORDS):
    return s
  return (
      '"' + s.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n') +
      '"'
  )

# special value to capture the output of gvprint as binary data
GVCAPTURE = object()

# special value to capture the output of gvprint as a data: URL
GVDATAURL = object()

# pylint: disable=too-many-branches,too-many-statements,too-many-locals
def gvprint(
    dot_s, file=None, fmt=None, layout=None, dataurl_encoding=None, **dot_kw
):
  ''' Print the graph specified by `dot_s`, a graph in graphViz DOT syntax,
      to `file` (default `sys.stdout`)
      in format `fmt` using the engine specified by `layout` (default `'dot'`).

      If `fmt` is unspecified it defaults to `'png'` unless `file`
      is a terminal in which case it defaults to `'sixel'`.

      In addition to being a file or file descriptor,
      `file` may also take the following special values:
      * `GVCAPTURE`: causes `gvprint` to return the image data as `bytes`
      * `GVDATAURL`: causes `gvprint` to return the image data as a `data:` URL

      For `GVDATAURL`, the parameter `dataurl_encoding` may be used
      to override the default encoding, which is `'utf8'` for `fmt`
      values `'dot'` and `'svg'`, otherwise `'base64'`.

      This uses the graphviz utility `dot` to draw graphs.
      If printing in SIXEL format the `img2sixel` utility is required,
      see [https://saitoha.github.io/libsixel/](libsixel).

      Example:

          data_url = gvprint('digraph FOO {A->B}', file=GVDATAURL, fmt='svg')
  '''
  if file is None:
    file = sys.stdout
  if isinstance(file, str):
    with open(file, 'xb') as f:
      return gvprint(dot_s, file=f, fmt=fmt, layout=layout, **dot_kw)
  if file is GVDATAURL:
    if dataurl_encoding is None:
      dataurl_encoding = 'utf8' if fmt in (
          'dot',
          'svg',
      ) else 'base64'
    gvdata = gvprint(dot_s, file=GVCAPTURE, fmt=fmt, layout=layout, **dot_kw)
    data_content_type = f'image/{"svg+xml" if fmt == "svg" else fmt}'
    if dataurl_encoding == 'utf8':
      gv_data_s = gvdata.decode('utf8')
      data_part = urlquote(gv_data_s.replace('\n', ''), safe=':/<>{}')
    elif dataurl_encoding == 'base64':
      data_part = b64encode(gvdata).decode('ascii')
    else:
      raise ValueError(
          "invalid data URL encoding %r; I accept 'utf8' or 'base64'" %
          (dataurl_encoding,)
      )
    return f'data:{data_content_type};{dataurl_encoding},{data_part}'
  if file is GVCAPTURE:
    capture_mode = True
    file = PIPE
  else:
    capture_mode = False
  if layout is None:
    layout = 'dot'
  if fmt is None:
    if file.isatty():
      fmt = 'sixel'
    else:
      fmt = 'png'
  graph_modes = dict(layout=layout, splines='true')
  node_modes = {}
  edge_modes = {}
  for dot_mode, value in dot_kw.items():
    try:
      modetype, mode = dot_mode.split('_', 1)
    except ValueError:
      if dot_mode in ('fg',):
        node_modes.update(color=value)
        edge_modes.update(color=value)
      elif dot_mode in ('fontcolor',):
        node_modes.update(fontcolor=value)
      else:
        graph_modes[dot_mode] = value
    else:
      if modetype == 'graph':
        graph_modes[mode] = value
      elif modetype == 'node':
        node_modes[mode] = value
      elif modetype == 'edge':
        edge_modes[mode] = value
      else:
        raise ValueError(
            "%s=%r: unknown mode type %r,"
            " expected one of graph, node, edge" % (dot_mode, value, modetype)
        )
  dot_fmt = 'png' if fmt == 'sixel' else fmt
  dot_argv = ['dot', f'-T{dot_fmt}']
  for gmode, gvalue in sorted(graph_modes.items()):
    dot_argv.append(f'-G{gmode}={gvalue}')
  for nmode, nvalue in sorted(node_modes.items()):
    dot_argv.append(f'-N{nmode}={nvalue}')
  for emode, evalue in sorted(edge_modes.items()):
    dot_argv.append(f'-E{emode}={evalue}')
  # make sure any preceeding output gets out first
  if file is not PIPE:
    file.flush()
  # subprocesses to wait for in order
  subprocs = []
  output_popen = None
  if fmt == 'sixel':
    # pipeline to pipe "dot" through "img2sixel"
    # pylint: disable=consider-using-with
    img2sixel_popen = Popen(['img2sixel'], stdin=PIPE, stdout=file)
    dot_output = img2sixel_popen.stdin
    subprocs.append(img2sixel_popen)
    output_popen = img2sixel_popen
  else:
    img2sixel_popen = None
    dot_output = file
  # pylint: disable=consider-using-with
  dot_popen = Popen(dot_argv, stdin=PIPE, stdout=dot_output)
  if output_popen is None:
    output_popen = dot_popen
  subprocs.insert(0, dot_popen)
  if img2sixel_popen is not None:
    # release our handle to img2sixel
    img2sixel_popen.stdin.close()
  if capture_mode:
    captures = []
    T = Thread(
        target=lambda: captures.append(output_popen.stdout.read()),
        daemon=True,
    )
    T.start()
  dot_bs = dot_s.encode('ascii')
  dot_popen.stdin.write(dot_bs)
  dot_popen.stdin.close()
  for subp in subprocs:
    subp.wait()
  if capture_mode:
    # get the captured bytes
    T.join()
    bs, = captures
    return bs
  return None

## Nothing renders this :-(
##
##gvprint.__doc__ += (
##    '\n    produces a `data:` URL rendering as:\n    <img src="' + gvprint(
##        'digraph FOO {A->B}',
##        file=GVDATAURL,
##        fmt='svg',
##        dataurl_encoding='base64',
##    ) + '">'
##)

def gvdata(dot_s, **kw):
  ''' Convenience wrapper for `gvprint` which returns the binary image data.
  '''
  return gvprint(dot_s, file=GVCAPTURE, **kw)

def gvdataurl(dot_s, **kw):
  ''' Convenience wrapper for `gvprint` which returns the binary image data
      as a `data:` URL.
  '''
  return gvprint(dot_s, file=GVDATAURL, **kw)

def gvsvg(dot_s, **gvdata_kw):
  ''' Convenience wrapper for `gvprint` which returns an SVG string.
  '''
  svg = gvdata(dot_s, fmt='svg', **gvdata_kw).decode('utf-8')
  svg = svg[svg.find('<svg'):].rstrip()  # trim header and tail
  return svg

class DOTNodeMixin:
  ''' A mixin providing methods for things which can be drawn as
      nodes in a DOT graph description.
  '''

  DOT_NODE_FONTCOLOR_PALETTE = {}
  DOT_NODE_FILLCOLOR_PALETTE = {}

  def __getattr__(self, attr: str):
    ''' Recognise various `dot_node_*` attributes.

        `dot_node_*color` is an attribute derives from `self.DOT_NODE_COLOR_*PALETTE`.
    '''
    dot_node_suffix = cutprefix(attr, 'dot_node_')
    if dot_node_suffix is not attr:
      # dot_node_*
      colourname = cutsuffix(dot_node_suffix, 'color')
      if colourname is not dot_node_suffix:
        # dot_node_*color
        palette_name = f'DOT_NODE_{colourname.upper()}COLOR_PALETTE'
        try:
          palette = getattr(self, palette_name)
        except AttributeError:
          # no colour palette
          pass
        else:
          try:
            colour = palette[self.dot_node_palette_key]
          except KeyError:
            colour = palette.get(None)
          return colour
    try:
      sga = super().__getattr__
    except AttributeError as e:
      raise AttributeError(
          "no %s.%s attribute" % (self.__class__.__name__, attr)
      ) from e
    return sga(attr)

  @property
  def dot_node_id(self):
    ''' An id for this DOT node, also the default index into the palettes.
    '''
    return str(id(self))

  @property
  def dot_node_palette_key(self):
    ''' The default palette index is `self.dot_node_id``.
    '''
    return self.dot_node_id

  @staticmethod
  def dot_node_attrs_str(attrs):
    ''' An attributes mapping transcribed for DOT,
        ready for insertion between `[]` in a node definition.
    '''
    strs = []
    for attr, value in attrs.items():
      if isinstance(value, (int, float)):
        value_s = str(value)
      elif isinstance(value, str):
        value_s = quote(value)
      else:
        raise TypeError(
            "attrs[%r]=%s: expected int,float,str" % (attr, r(value))
        )
      strs.append(quote(attr) + '=' + value_s)
    attrs_s = ','.join(strs)
    return attrs_s

  def dot_node(self, label=None, **node_attrs) -> str:
    ''' A DOT syntax node definition for `self`.
    '''
    if label is None:
      label = self.dot_node_label()
    attrs = dict(self.dot_node_attrs())
    attrs.update(label=label)
    attrs.update(node_attrs)
    if not attrs:
      return quote(label)
    return f'{quote(self.dot_node_id)}[{self.dot_node_attrs_str(attrs)}]'

  # pylint: disable=no-self-use
  def dot_node_attrs(self) -> Mapping[str, str]:
    ''' The default DOT node attributes.
    '''
    attrs = dict(style='solid')
    fontcolor = self.dot_node_fontcolor
    if fontcolor is not None:
      attrs.update(fontcolor=fontcolor)
    fillcolor = self.dot_node_fillcolor
    if fillcolor is not None:
      attrs.update(style='filled')
      attrs.update(fillcolor=fillcolor)
    return attrs

  def dot_node_label(self) -> str:
    ''' The default node label.
        This implementation returns `str(self)`
        and a common implementation might return `self.name` or similar.
    '''
    return str(self)

@dataclass
class Node(DOTNodeMixin):
  id: str
  rankdir: str = "LR"
  shape: str = "rect"
  attrs: dict = field(default_factory=dict)

  def __str__(self):
    return self.as_dot()

  def __hash__(self):
    return id(self)

  def __eq__(self, other):
    return self is other

  @property
  def dot_node_id(self) -> str:
    return self.id

  def as_dot(self, *, no_attrs=False):
    dot = []
    node_id = self.dot_node_id
    if node_id:
      dot.append(quote(node_id))
      if not no_attrs:
        for k, v in sorted(self.attrs.items()):
          dot.append(f'{k}={quote(v)}')
    return " ".join(dot)

@dataclass
class Graph:
  ''' A representation of a graphviz graph suitable for transcribing as DOT.
  '''
  id: Optional[str] = None
  digraph: bool = False
  strict: bool = False
  attrs: dict = field(default_factory=dict)
  node_attrs: dict = field(default_factory=dict)
  edge_attrs: dict = field(default_factory=dict)
  nodes: Mapping[str, Node] = field(default_factory=dict)
  edges: list[Tuple[list, dict]] = field(default_factory=list)
  subgraphs: Mapping[str, "Graph"] = field(default_factory=dict)

  def __str__(self):
    return self.as_dot()

  def __hash__(self):
    return id(self)

  def __eq__(self, other):
    return self is other

  def __getitem__(self, node_id: str):
    return self.nodes[node_id]

  def add(self, *items):
    ''' Add a `Node` id or a `Node`s or `Graph`s to `self.nodes`.
    '''
    for item in items:
      if isinstance(item, str):
        if item not in self.nodes:
          node = Node(item)
          self.nodes[item] = node
      elif isinstance(item, Node):
        if (node := self.nodes.get(item.id)) is None:
          self.nodes[item.id] = item
        elif node is not item:
          raise ValueError(f'self.nodes[{item.id!r}] already exists')
      elif isinstance(item, Graph):
        if (graph := self.subgraphs.get(item.id)) is None:
          self.subgraphs[item.id] = item
        elif graph is not item:
          raise ValueError(f'self.subgraphs[{item.id!r}] already exists')
      else:
        raise TypeError(f'not a Node or Graph: {r(item)}')

  def join(self, *items, **attrs):
    ''' Join the specified `Node`s, `Node` ids or `Graph`s in an edge.
    '''
    if len(items) < 2:
      raise ValueError(
          f'at least 2 items required to join, received {len(items)}'
      )
    edge_nodes = []
    for item in items:
      if isinstance(item, str):
        if item in self.nodes:
          item = self.nodes[item]
        else:
          node = Node(item)
          self.nodes[item] = node
          item = node
      elif not isinstance(item, (Node, Graph)):
        raise TypeError(f'not a Node or Graph or Node id: {r(item)}')
      edge_nodes.append(item)
    self.edges.append((edge_nodes, attrs))

  @staticmethod
  def mapping_as_dot(kv: Mapping[str, Any]):
    ''' Transcribe a mapping as DOT i.e. an `a_list`.
    '''
    return ", ".join(f'{k}={quote(v)}' for k, v in sorted(kv.items()))

  def as_dot(
      self,
      *,
      fold=False,
      indent="",
      subindent="  ",
      graphtype=None,
  ) -> str:
    ''' Return a DOT representation of this `Graph`.

        Parameters:
        * `fold`: default `False`; if true then produce indented multiline text
        * `indent`: the prevailing indent if `fold`, default `""`
        * `subindent`: incremental indent of nested items if `fold`, default `"  "`
    '''
    dot = [
        " ".join(
            filter(
                None, (
                    ('strict' if self.strict else ''),
                    graphtype or ('digraph' if self.digraph else 'graph'),
                    self.id and quote(self.id),
                    '{',
                )
            )
        )
    ]
    # graph wide object defaults
    if self.attrs:
      dot.append(f'graph [ {self.mapping_as_dot(self.attrs)} ]')
    if self.node_attrs:
      dot.append(f'node [ {self.mapping_as_dot(self.node_attrs)} ]')
    if self.edge_attrs:
      dot.append(f'edge [ {self.mapping_as_dot(self.edge_attrs)} ]')
    # define nodes and their attributes
    dot.extend(node.as_dot() for node in self.nodes.values())
    # define subgraphs and their attributes
    for graph in self.subgraphs.values():
      dot.append(
          graph.as_dot(fold=fold, subindent=subindent, graphtype='subgraph')
      )
    for edge_nodes, edge_attrs in self.edges:
      edge_dot = []
      first = True
      for node in edge_nodes:
        if first:
          first = False
        else:
          edge_dot.append('->' if self.digraph else '--')
        if isinstance(node, Node):
          edge_dot.append(node.as_dot(no_attrs=True))
        elif isinstance(node, Graph):
          edge_dot.append(
              node.as_dot(
                  fold=fold,
                  indent=indent + subindent,
                  subindent=subindent,
                  graphtype='subgraph'
              )
          )
        else:
          raise TypeError(f'unhandled egde node {r(node)}')
      edge_dot.append(self.mapping_as_dot(edge_attrs))
      dot.append(" ".join(edge_dot))
    dot.append('}')
    if not fold:
      return " ".join(dot)
    line0, *midlines, linez = dot
    return indent + f'\n{indent}'.join(
        (
            line0,
            *(indent_text(line, subindent) for line in midlines),
            linez,
        )
    )

  def print(self, **gvprint_kw):
    dot_s = self.as_dot()
    return gvprint(dot_s, **gvprint_kw)

if __name__ == '__main__':
  G = Graph(digraph=True)
  G.attrs.update(rankdir="LR")
  G.node_attrs.update(shape="rect")
  G.node_attrs["b"] = "c"
  g1 = Graph()
  g1.add("ga", "gb")
  g2 = Graph("cluster-g2")
  g2.add("x", "y")
  G.add("a", "b", g1, g2)
  G["a"].attrs["x"] = 1
  G.join("a", "b", "c")
  G.join("b", g1)
  G.join("gb", g2)
  print(G.as_dot(fold=True))
  for layout in 'neato fdp sfdp circo twopi osage patchwork dot'.split():
    print("LAYOUT", layout)
    G.print(layout=layout)
