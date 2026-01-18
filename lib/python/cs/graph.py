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
from cs.queues import ListQueue


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
    return f'{self.__class__.__name__}:{id(self) if self.name is None else repr(self.name)}'

  def __hash__(self):
    return id(self)

  def __eq__(self, other):
    return self is other

  @property
  def in_count(self):
    ''' The number of inbound edges.
    '''
    return len(self.in_edges)

  @property
  def out_count(self):
    ''' The number of outbound edges.
    '''
    return len(self.out_edges)

  @property
  def in_nodes(self):
    ''' The inbound `Node`s.
    '''
    return [edge.in_node for edge in self.in_edges]

  @property
  def in_node(self):
    ''' The inbound/parent node, if there is exactly one inbound edge.
    '''
    edge, = self.in_edges
    return edge.in_node

  @property
  def out_node(self):
    ''' The outbound/child node, if there is exactly one outbound edge.
    '''
    edge, = self.out_edges
    return edge.out_node

  @property
  def out_nodes(self):
    ''' The outbound `Node`s.
    '''
    return [edge.out_node for edge in self.out_edges]

  def as_GVNode(self):
    return GVNode(id=self.name or str(id(self)), attrs=dict(self.attrs))

  def as_dot(self, *, no_attrs=False):
    return self.as_GVNode().as_dot(no_attrs=no_attrs)

  def as_railroad(self):
    return RRTextBox(str(self))

@dataclass
class Edge:
  ''' A directed edge between `Node`s in a `Graph`.
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
    assert node is not None
    self.nodes.add(node)
    self._nodes_by_name[node.name].add(node)
    return node

  def add_edge(self, node1: str | Node, node2: str | Node, **edge_attrs):
    ''' Add an `Edge` to the `Graph`.
    '''
    node1 = self.add_node(node1)  # may promote str to Node
    assert node1 is not None
    node2 = self.add_node(node2)  # may promote str to Node
    assert node2 is not None
    edge = Edge(node1, node2, **edge_attrs)
    self.edges.add(edge)
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

  def gvprint(self, **kw):
    gvpkw = {}
    attrs = {}
    node_attrs = {}
    edge_attrs = {}
    for k, v in kw.items():
      if k in ('file,fmt,layout,dataurl_encoding'):
        gvpkw[k] = v
      elif k.startswith('node_'):
        node_attrs[k.removeprefix('node_')] = v
      elif k.startswith('edge_'):
        node_attrs[k.removeprefix('edge_')] = v
      else:
        attrs[k] = v
    gvgraph = self.as_GVGraph(
        graph_attrs=attrs, node_attrs=node_attrs, edge_attrs=edge_attrs
    )
    gvgraph.print(**gvpkw)

  def as_railroad(self) -> RRBase:
    ''' Return a railroad node for this `Graph`.
    '''
    ##rr_by_node = { node:node.as_railroad() for node in self.nodes}
    # A mapping of `Node` to railroad nodes containing them.
    root_nodes = []
    end_nodes = []
    counted_nodes = []
    # Collate root nodes (no in nodes), tail nodes (no out nodes)
    # and interior nodes (nodes with in and out nodes.

    for node in self.nodes:
      if not node.in_edges:
        root_nodes.append(node)
      if not node.out_edges:
        end_nodes.append(node)
      if node.in_edges and node.out_edges:
        counted_nodes.append((len(node.in_edges) * len(node.out_edges), node))
    # We start with the root nodes.
    # We construct sequences of singly connected nodes until we reach
    # a tail node or a node already mapped to a railroad node.
    rr_by_node = {}

    @typechecked
    def rr_from(root: Node) -> RRBase:
      ''' Produce an `RRSequence` encompassing the `graph` from `root`.
      '''
      rr = None
      rr_for_rrnode = {}

      def root_rr(rrnode):
        ''' Find the ancestral RR node from one of its interior RR nodes.
        '''
        while True:
          try:
            rrnode = rr_for_rrnode[rrnode]
          except KeyError:
            break
        return rrnode

      container_by_node = {}
      merge_by_node = defaultdict(trace(RRMerge))
      seq_by_node = defaultdict(RRSequence)
      seqs = []
      q = ListQueue([root], unique=True)
      for node in q:
        node0 = node
        seq = seq_by_node[node0]
        if rr is None:
          # the RR diagram starts from the first RRSequence
          rr = seq
        seqs.append(seq)
        # should we record the new sequence in an enclosing RRSplit?
        try:
          container = container_by_node.pop(node)
        except KeyError:
          if node is not root and node.in_count == 0:
            print(f'  unexpected {node.in_count=} for {node}')
        else:
          print(f'  container {container.desc} + seq {seq.desc}')
          container.append(seq)
          rr_for_rrnode[seq] = container

          # for merges, record that the sequence is enclosed in the merge
          rr_for_rrnode[seq] = container
        if node.in_count == 0:
          seq.append(RR_START)
        elif node.in_count > 1:
          # Start with a merge and queue the in_nodes.
          # Fetch the RRMerge which leads to this node.
          merge = merge_by_node[node]
          rr_for_rrnode[merge] = seq
          for lnode in node.in_nodes:
            # locate the start of the left node's linear chain (sequence)
            while lnode.in_count == 1 and lnode.in_node.out_count == 1:
              lnode = lnode.in_node
            try:
              lseq = seq_by_node[lnode]
            except KeyError:
              # no sequence for this node yet, but that's ok
              # we will include the sequence later when it's made
              assert lnode not in container_by_node
              container_by_node[lnode] = merge
              q.append(lnode)
              if lnode.name == 'b': breakpoint()
            else:
              # The origin sequence will always preexist.
              # If one of these is the origin sequence, make our sequence the origin.
              lroot = root_rr(lseq)
              # append the existing sequence right now
              merge.append(lroot)  ## was lseq
              if lroot is rr:
                rr = seq  ## was merge
              breakpoint()
          seq.append(merge)
        # append this Node's RR and all the following linear Nodes
        seq.append(node.as_railroad())
        while node.out_count == 1:
          next_node = node.out_node
          if next_node.in_count == 1:
            # continue the linear chain
            node = next_node
            seq.append(node.as_railroad())
          else:
            # the is a merge
            assert next_node.in_count > 1
            q.append(next_node)
            break

        # see why we stopped
        if node.out_count > 1:
          split = RRSplit()
          seq.append(split)
          rr_for_rrnode[split] = seq
          for out_node in node.out_nodes:
            print(f'  queue {out_node} for new Split from {node}')
            q.append(out_node)
            assert out_node not in container_by_node
            container_by_node[out_node] = split
            # connect the drawing hierarchy
            rseq = seq_by_node[out_node]
            rr_for_rrnode[out_node] = rseq
            rr_for_rrnode[rseq] = split
            rr_for_rrnode[split] = seq
        elif node.out_count == 1:
          # we must have hit a merge, queue it for consideration
          next_node = node.out_node
          assert next_node.in_count > 1
          merge = merge_by_node[next_node]
          print(f'  queue {node}.out_node:{next_node}, will be a merge')
        else:
          seq.append(RR_END)
      if container_by_node:
        print(
            f'  collating {len(container_by_node)} container_by_node entries'
        )
        for node, cont in container_by_node.items():
          seq = seq_by_node[node]
          print(
              f'    {node}->{cont.desc}: append({seq.desc=}:{seq.content[0].desc}...)'
          )
          cont.append(seq)
          if rr is seq:
            print(f'    seq is rr, make rr = container')
            rr = cont
        ##print(f'  HACK rr = last cont {cont.desc}')
        ##rr = cont
      return rr

    for root in root_nodes:
      rr = trace(rr_from)(root)
      pprint(rr)
      breakpoint()
      print(rr)
    breakpoint()
    return rr

if __name__ == '__main__':
  G = Graph("graph1")
  G.add_node("a")
  G.add_node("b")
  G.add_edge("a", "b")
  ##print("AB")
  ##G.gvprint()
  ##print(G.as_railroad())
  ##breakpoint()
  G.add_node("c")
  G.add_edge("c", "b")
  ##print("ABC")
  ##G.gvprint()
  ##print(G.as_railroad())
  ##breakpoint()
  G.add_edge("b", "d")
  G.add_edge("00", "c")
  ##print(G.as_dot(fold=True))
  ##G.gvprint()
  ##rr = G.as_railroad()
  ##pprint(rr)
  ##breakpoint()
  ##rr.print()
  G.add_edge("x", "a")
  G.add_edge("x", "00")
  G.add_edge("d", "e")
  print(G.as_dot(fold=True))
  G.gvprint(rank_dir='LR')
  rr = G.as_railroad()
  print(repr(rr))
  rr.print()
