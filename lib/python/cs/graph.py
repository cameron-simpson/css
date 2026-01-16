#!/usr/bin/python

''' A basic graph representation with graphviz/DOT and railroad representations.
'''

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from cs.ascii_art import (
    RRBase,
    RRMerge,
    RRSequence,
    RRSplit,
    RRTextBox,
    RR_START,
    RR_END,
)
from cs.gvutils import Graph as GVGraph, Node as GVNode


@dataclass
class Node:
  ''' A node in a `Graph`.
  '''
  name: Optional[str] = None
  in_edges: list["Edge"] = field(default_factory=list)
  out_edges: list["Edge"] = field(default_factory=list)
  attrs: dict = field(default_factory=dict)
  refobj: Any = None

  def __str__(self):
    return f'{self.__class__.__name__}:{id(self)}' if self.name is None else self.name

  def __hash__(self):
    return id(self)

  def __eq__(self, other):
    return self is other

  def as_GVNode(self):
    return GVNode(id=self.name or str(id(self)), attrs=dict(self.attrs))

  def as_dot(self, *, no_attrs=False):
    return self.as_GVNode().as_dot(no_attrs=no_attrs)

@dataclass
class Edge:
  ''' An edge between `Noe`s in a `Graph`.
  '''
  in_node: Node
  out_node: Node
  name: Optional[str] = None
  attrs: dict = field(default_factory=dict)
  refobj: Any = None

  def __str__(self):
    return f'{self.__class__.__name__}:{id(self)}' if self.name is None else self.name

  def __hash__(self):
    return id(self)

  def __eq__(self, other):
    return self is other

@dataclass
class Graph(Node):
  ''' A graph containing `Node`s and `Edge`s.
      This is also a subclass of `Node`, to support subgraphs.
  '''
  nodes: set[Node] = field(default_factory=set)
  _nodes_by_name: dict[str, set[Node]] = field(
      default_factory=lambda: defaultdict(set)
  )
  edges: set[Edge] = field(default_factory=set)

  def __str__(self):
    return self.as_dot()

  def __hash__(self):
    return id(self)

  def __eq__(self, other):
    return self is other

  def add_node(self, node: str | Node):
    ''' Add a `Node` to the `Graph`.
        If `node` is a string, promote it to a new `Node` with that name.
        Return the `Node` (because it may be a new `Node`).
    '''
    if isinstance(node, str):
      name = node
      named_nodes = self._nodes_by_name[node]
      if named_nodes:
        try:
          node, = named_nodes
        except ValueError:
          raise ValueError(
              f'ambiguous {name=}: multiple Nodes with that name already exist'
          )
      else:
        node = Node(node)
    self.nodes.add(node)
    self._nodes_by_name[node.name].add(node)
    return node

  def add_edge(self, node1: str | Node, node2: str | Node, **edge_attrs):
    ''' Add an `Edge` to the `Graph`.
    '''
    node1 = self.add_node(node1)  # may promote str to Node
    node2 = self.add_node(node2)  # may promote str to Node
    edge = self.edges.add(Edge(node1, node2, **edge_attrs))
    node1.out_edges.append(edge)
    node2.in_edges.append(edge)

  def as_GVGraph(
      self,
      *,
      digraph=True,
      strict=False,
      graph_attrs=None,
      node_attrs=None,
      edge_attrs=None,
  ):
    ''' Construct a `cs.gvutils.Graph` from this `Graph`.
    '''
    gvgraph = GVGraph(
        digraph=digraph,
        strict=strict,
        attrs=graph_attrs,
        node_attrs=node_attrs,
        edge_attrs=edge_attrs
    )
    gvnodes = {}
    for node in self.nodes:
      gvnode = node.as_GVNode()
      gvnodes[node] = gvnode
      gvgraph.add(gvnode)
    for edge in self.edges:
      gvgraph.join(gvnodes[edge.in_node], gvnodes[edge.out_node])
    return gvgraph

  def as_dot(
      self, *, fold=False, indent="", subindent="  ", graphtype=None, **gvkw
  ):
    ''' Construct a `cs.gvutils.Graph` and return it as a DOT string.
    '''
    gvgraph = self.as_GVGraph(**gvkw)
    return gvgraph.as_dot(
        fold=fold, indent=indent, subindent=subindent, graphtype=graphtype
    )

  def gvprint(self, **gvpkw):
    gvgraph = self.as_GVGraph()
    gvgraph.print(**gvpkw)

if __name__ == '__main__':
  G = Graph("graph1")
  G.add_node("a")
  G.add_node("b")
  G.add_node("c")
  G.add_edge("a", "b")
  G.add_edge("c", "b")
  print(G.as_dot(fold=True))
  G.gvprint()
