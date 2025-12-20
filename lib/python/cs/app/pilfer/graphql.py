#!/usr/bin/env python3

''' Various things for working with GraphQL.
'''

from functools import cached_property

from cs.lex import printt
from cs.logutils import warning

class GraphQLDataMixin:
  ''' A mixin for dealing with the data from a GraphQL response.
  '''

  def __init__(self, data: dict):
    ''' Save `data` as `self.data`.
    '''
    assert isinstance(data, dict)
    self.data = data

  @cached_property
  def __(self):
    ''' A proxy to the `__*` names in `self.data`.
    '''
    return DunderProxy(self)

  @property
  def dtype(self) -> str:
    ''' The `data` type name, this object's `"__typename"` field.
    '''
    return self.__.typename

  def connected_nodes(self, base_nodetype) -> list["GraphQLDataMixin"]:
    ''' Return a list of `GraphQLDataMixin` instances for the nodes connected
        via the edges.

        We expect `self.data` to have this example structure:

            "__typename": "UserComicCoverObjectConnection",
            "edges": [
              {
                "__typename": "UserComicCoverObjectEdge",
                "node": {
                  "__typename": "UserComicCoverListObject",
                  "comicCoverId": "5f02d2d9-64df-435d-abbd-7025d8614339",

    '''
    cls = self.__class__
    nodes = []
    if self.dtype != f'{base_nodetype}Connection':
      warning(
          f'connected_nodes(base_nodetype): {self.dtype=} does not match {base_nodetype=}'
      )
    # expect edges, each edge containing a node
    edge_typename = f'{base_nodetype}Edge'
    for edge in map(cls, self["edges"]):
      assert edge.__.typename == edge_typename, (
          f'{edge.__.typename=} is not {edge_typename=}'
      )
      node = cls(self.node)
      assert node.dtype.startswith(base_nodetype), (
          f'expected {node.dtype=} to start with {base_nodetype=}'
      )
      nodes.append(node)
    return nodes

  def printt(self, **printt_kw):
    ''' Print the contents of `self.data` via `cs.lex.printt()`.
    '''
    printt(*sorted(self.data.items()), **printt_kw)

class DunderProxy:
  ''' A proxy to the `__*` names in a dict from a GraphQL data response.
  '''

  def __init__(self, gql_data: GraphQLDataMixin):
    self.gql_data = gql_data

  def __getattr__(self, dund: str) -> str | None:
    ''' Return the value of `__{dund}` or `None` if missing.
    '''
    return self.gql_data.get(f'__{dund}')
