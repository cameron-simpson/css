import cs.venti.dir
import cs.venti

def totext(D):
  return cs.venti.totext(D.encode())

def fromtext(value):
  D, name = cs.venti.dir.resolve(value)
  if name is not None:
    D = D[name]
  return D

def tobytes(D):
  return D.encode()

def frombytes(value):
  return cs.venti.dir.decodeDirent(value, justone=True)

def register_with(nodedb, scheme='cs.venti'):
  nodedb.register_type(cs.venti.dir.Dirent, scheme,
                       totext, fromtext, tobytes, frombytes)
