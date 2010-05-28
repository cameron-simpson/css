from cs.venti.block import Block, IndirectBlock
from cs.venti.dir import resolve
from cs.venti import tohex

def serialise(B):
  return tohex(B.encode())

def deserialise(value):
  D, name = resolve(path)
  if name is not None:
    D=D[name]
  return D.getBlock()

def register_with(nodedb, scheme='cs.venti'):
  nodedb.register_type(Block, scheme, serialise, deserialise)
  nodedb.register_type(IndirectBlock, scheme, serialise, deserialise)
