from cs.venti.block import Block, IndirectBlock
import cs.venti.dir
from cs.venti import totext

def totext(D):
  return totext(D.encode())

def fromtext(value):
  D, name = cs.venti.dir.resolve(value)
  if name is not None:
    D = D[name]
  return D

def tobytes(D):
  return D.encode()

def frombytes(value):
  return decodeDirect(value, justone=True)

def register_with(nodedb, scheme='cs.venti'):
  nodedb.register_type(Dirent, scheme, totext, fromtext, tobytes, frombytes)
