from cs.venti import texthexify, untexthexify
import cs.venti.dir

def register_with(nodedb, scheme='cs.venti'):
  ''' Register the cs.venti transcriptions with the supplied NodeDB.
  '''
  nodedb.register_attr_type(cs.venti.dir.Dirent, scheme+'.Dirent',
                            dirent_totext, dirent_fromtext,
                            dirent_tobytes, dirent_frombytes)

def dirent_totext(D):
  return D.textEncode()

def dirent_fromtext(value):
  D, name = cs.venti.dir.resolve(value)
  if name is not None:
    D = D[name]
  return D

def dirent_tobytes(D):
  return D.encode()

def dirent_frombytes(value):
  return cs.venti.dir.decodeDirent(value, justone=True)

def storeFile(fp, name=None, oldBlock=None):
  ''' Store a file, return the Dirent.
      TODO: get some metadata using stat(). Merge with cs.venti.dir.storeFile?
  '''
  if type(fp) is str:
    if name is None:
      name = fp
    with open(fp, "rb") as fp2:
      stored = storeFile(fp2, name=name, oldBlock=oldBlock)
  else:
    if oldBlock is None:
      matchBlocks = None
    else:
      matchBlocks = oldBlock.leaves()
    stored = storeFile(fp, name=name, matchBlocks=matchBlocks)
  return stored
